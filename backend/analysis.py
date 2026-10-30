"""Guided, persisted experimental trials. No hardware action or trial starts itself."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import random
import uuid
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]
Note = Annotated[str, Field(strict=True, max_length=4000)]
SCENARIOS = [
    dict(id='closed_still', name='Closed and still', seconds=30, expected='likely_closed',
         prepare='Close the empty box, put the magnet in its closed position, and keep the inside dark. Place it on a stable table.',
         action='Keep the box still for 30 seconds. Record any unexpected opening, movement or condition alert.',
         reference='Your visual check of lid and magnet position. Photograph the setup if possible.'),
    dict(id='closed_moving', name='Closed while carried', seconds=20, expected='likely_closed',
         prepare='Close the box and keep the inside dark. Make sure the magnet remains in place.',
         action='Lift and carry the box normally for 20 seconds. Keep the lid closed. Do not shake or strike it.',
         reference='Visual confirmation that the lid stayed closed throughout. Movement should not alone imply opening.'),
    dict(id='open_bright', name='Open with light', seconds=20, expected='likely_opened',
         prepare='Start with the box closed and dark. Use ordinary room lighting. Keep the LDR uncovered inside the box.',
         action='Click Mark action, then open the lid and move its magnet away. Keep it open and lit for 20 seconds. Mark return, then close it.',
         reference='Observe the physical lid and light exposure independently; a video is useful. Click markers are approximate human timestamps.'),
    dict(id='open_dark', name='Open in darkness', seconds=20, expected='opening_suspected',
         prepare='Start closed. Arrange a dark area or opaque cover so the LDR stays dark when the lid opens.',
         action='Click Mark action, open the lid and move its magnet away while keeping the LDR dark. Hold 20 seconds; mark return and close.',
         reference='Record that the lid physically opened even though illumination stayed low. Do not label this as a closed box.'),
    dict(id='closed_light', name='Light through a closed box', seconds=20, expected='possible_packaging_separation',
         prepare='Keep lid and magnet closed. Arrange light through a small existing gap without changing the reed position.',
         action='Mark action, expose the LDR to light through that gap for 20 seconds. Mark return and block the light again.',
         reference='Observe that the lid and magnet remain closed. This tests an evidence combination, not proof of tampering.'),
    dict(id='tilt', name='Sustained tilt', seconds=20, expected='tilted',
         prepare='With the empty box upright and still, capture reference before arming. Use a protractor or measured fixture if available.',
         action='Mark action, tilt the box beyond the configured threshold and hold it still 20 seconds. Record the measured angle. Mark return and restore upright.',
         reference='Use an independently measured angle. Without it, this is a functional check, not angle accuracy.'),
    dict(id='vibration', name='Repeated vibration', seconds=20, expected='strong_vibration',
         prepare='Use the empty enclosure. Keep the lid closed. Choose a repeatable gentle motion or fixture; record the procedure.',
         action='Mark action and apply repeated motion for 20 seconds. Mark return and place the box still. Do not drop or damage the device.',
         reference='Describe imposed motion independently. Without a vibration reference this measures trigger repeatability, not calibrated vibration amplitude.'),
    dict(id='offline', name='Offline storage and recovery', seconds=30, expected='manual_record_reconciliation',
         prepare='Keep the server running. Note the current boot/sequence IDs. Keep this browser on a working server connection.',
         action='Mark action, disable only the ESP hotspot/network, and open/close the lid once. Wait 30 seconds. Re-enable that network, mark return, and wait until the queue drains before finishing.',
         reference='Record how many actions you performed and their order. Delayed arrivals are retained as receipt-window evidence; physical timing is not inferred.'),
]
BY_ID = {s['id']: s for s in SCENARIOS}


class CreateSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: Text
    device_id: Text
    stage: Literal['pilot','evaluation'] = 'pilot'
    repetitions: Annotated[int, Field(strict=True, ge=1, le=30)] = 1
    scenarios: Annotated[list[Text], Field(min_length=1, max_length=8)]
    setup: Note
    criteria: Note
    reference: Note


class Marker(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['action','return','note']
    note: Note = ''


class Finish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: Literal['observed','missed','false_alert','inconclusive','aborted']
    observed: Note
    deviation: Note = ''
    reference_value: Annotated[float | None, Field(allow_inf_nan=False)] = None
    reference_unit: Annotated[str, Field(strict=True, max_length=32)] = ''


def init_analysis_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS analysis_sessions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, device_id TEXT NOT NULL,
            stage TEXT NOT NULL, created_at TEXT NOT NULL, protocol TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analysis_trials (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL, position INTEGER NOT NULL,
            scenario TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT, ended_at TEXT, start_id INTEGER, end_id INTEGER,
            baseline TEXT, result TEXT, evidence TEXT, console_evidence TEXT
        );
        CREATE TABLE IF NOT EXISTS analysis_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trial_id TEXT NOT NULL,
            created_at TEXT NOT NULL, kind TEXT NOT NULL, note TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_analysis_recording_per_session
            ON analysis_trials(session_id) WHERE status='recording';
    ''')


def now():
    return datetime.now(timezone.utc).isoformat()


def install_analysis_routes(app, connect, authenticate, connection_status):
    auth = [Depends(authenticate)]

    def session(db, identifier):
        row = db.execute('SELECT * FROM analysis_sessions WHERE id=?',(identifier,)).fetchone()
        if not row:
            raise HTTPException(404,'Analysis session not found')
        return dict(row)

    def trial(db, identifier):
        row = db.execute('SELECT * FROM analysis_trials WHERE id=?',(identifier,)).fetchone()
        if not row:
            raise HTTPException(404,'Trial not found')
        return dict(row)

    def evidence(db, t, device):
        rows = db.execute('SELECT * FROM records WHERE device_id=? AND id>? AND id<=? ORDER BY id',
                          (device,t['start_id'],t['end_id'] if t['end_id'] is not None else 2**63-1)).fetchall()
        return [{**dict(r),'data':json.loads(r['data'])} for r in rows]

    def serialize_trial(db,t,device,include=False):
        t=dict(t)
        for key in ['baseline','result']:
            t[key]=json.loads(t[key]) if t[key] else None
        frozen=json.loads(t.pop('evidence')) if t['evidence'] else None
        console=json.loads(t.pop('console_evidence') or '[]')
        records=frozen if frozen is not None else evidence(db,t,device) if t['start_id'] is not None else []
        t['record_count']=len(records)
        t['markers']=[dict(r) for r in db.execute('SELECT * FROM analysis_markers WHERE trial_id=? ORDER BY id',(t['id'],))]
        if include:
            t['records']=records
            t['console_records']=console
        return t

    @app.get('/analysis',include_in_schema=False)
    def analysis_page():
        return FileResponse(Path(__file__).with_name('analysis.html'))

    @app.get('/api/v1/analysis/scenarios',dependencies=auth)
    def scenarios():
        return {'scenarios':SCENARIOS}

    @app.get('/api/v1/analysis/sessions',dependencies=auth)
    def sessions():
        with closing(connect()) as db:
            return {'sessions':[dict(r) for r in db.execute('SELECT id,name,device_id,stage,created_at FROM analysis_sessions ORDER BY created_at DESC')]}

    @app.post('/api/v1/analysis/sessions',dependencies=auth,status_code=201)
    def create(message:CreateSession):
        if len(set(message.scenarios))!=len(message.scenarios) or any(key not in BY_ID for key in message.scenarios):
            raise HTTPException(422,'Choose distinct supported scenarios')
        if not message.setup.strip() or not message.criteria.strip() or not message.reference.strip():
            raise HTTPException(422,'Record setup, criteria and reference before starting')
        identifier=uuid.uuid4().hex
        order=message.scenarios*message.repetitions
        random.SystemRandom().shuffle(order)
        root=Path(__file__).resolve().parents[1]
        fingerprint={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ['firmware/cargo_monitor/cargo_monitor.ino','firmware/cargo_monitor/monitor_logic.h','backend/analysis.py']}
        protocol={"source_fingerprint":fingerprint,**message.model_dump(),'order':order,'scenario_definitions':SCENARIOS,
                  'association':'server_receipt_window','timing_limit':'Markers are human reports at server receipt; not physical-event latency.'}
        with closing(connect()) as db,db:
            db.execute('INSERT INTO analysis_sessions VALUES (?,?,?,?,?,?)',
                       (identifier,message.name,message.device_id,message.stage,now(),json.dumps(protocol)))
            for i,key in enumerate(order):
                db.execute('INSERT INTO analysis_trials(id,session_id,position,scenario) VALUES (?,?,?,?)',
                           (uuid.uuid4().hex,identifier,i,key))
        return {'id':identifier}

    @app.get('/api/v1/analysis/sessions/{identifier}',dependencies=auth)
    def detail(identifier:str):
        with closing(connect()) as db:
            item=session(db,identifier)
            item['protocol']=json.loads(item['protocol'])
            item['trials']=[serialize_trial(db,dict(r),item['device_id']) for r in db.execute('SELECT * FROM analysis_trials WHERE session_id=? ORDER BY position',(identifier,))]
            item['connection']=connection_status(device_id=item['device_id'])
            latest=db.execute("SELECT received_at,data FROM records WHERE device_id=? AND kind='telemetry' ORDER BY id DESC LIMIT 1",(item['device_id'],)).fetchone()
            item['preflight']={'received_at':latest['received_at'],'checks':{key:json.loads(latest['data']).get(key) for key in ['imu_ok','reference_valid','armed','ldr_enabled','dht_ok','queued','dropped_records','storage_errors']}} if latest else None
            return item

    @app.post('/api/v1/analysis/trials/{identifier}/start',dependencies=auth)
    def start(identifier:str):
        with closing(connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            t=trial(db,identifier); owner=session(db,t['session_id'])
            if t['status']!='pending':
                raise HTTPException(409,'This trial has already started')
            active=db.execute("SELECT t.id FROM analysis_trials t JOIN analysis_sessions s ON t.session_id=s.id WHERE s.device_id=? AND t.status='recording'",(owner['device_id'],)).fetchone()
            if active:
                raise HTTPException(409,'This device already has a recording trial')
            pending=db.execute("SELECT id FROM analysis_trials WHERE session_id=? AND status='pending' ORDER BY position LIMIT 1",(t['session_id'],)).fetchone()
            if pending['id']!=identifier:
                raise HTTPException(409,'Use the next trial in the saved randomized order')
            row=db.execute('SELECT * FROM records WHERE device_id=? ORDER BY id DESC LIMIT 1',(owner['device_id'],)).fetchone()
            baseline={'record':{**dict(row),'data':json.loads(row['data'])} if row else None,
                      'console_start_id':db.execute('SELECT COALESCE(MAX(id),0) FROM raw_console').fetchone()[0],
                      'connection':connection_status(device_id=owner['device_id'])}
            last=db.execute('SELECT COALESCE(MAX(id),0) FROM records').fetchone()[0]
            db.execute("UPDATE analysis_trials SET status='recording',started_at=?,start_id=?,baseline=? WHERE id=?",(now(),last,json.dumps(baseline),identifier))
        return {'started':True}

    @app.post('/api/v1/analysis/trials/{identifier}/markers',dependencies=auth,status_code=201)
    def mark(identifier:str,message:Marker):
        with closing(connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if trial(db,identifier)['status']!='recording':
                raise HTTPException(409,'Start recording before adding markers')
            timestamp=now()
            db.execute('INSERT INTO analysis_markers(trial_id,created_at,kind,note) VALUES (?,?,?,?)',(identifier,timestamp,message.kind,message.note))
        return {'created_at':timestamp}

    @app.post('/api/v1/analysis/trials/{identifier}/finish',dependencies=auth)
    def finish(identifier:str,message:Finish):
        if not message.observed.strip():
            raise HTTPException(422,'Describe what physically happened, including an inconclusive or aborted trial')
        with closing(connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            t=trial(db,identifier); owner=session(db,t['session_id'])
            if t['status']!='recording':
                raise HTTPException(409,'Only a recording trial can be finished')
            t['end_id']=db.execute('SELECT COALESCE(MAX(id),0) FROM records').fetchone()[0]
            captured=evidence(db,t,owner['device_id'])
            console_start=json.loads(t['baseline'])['console_start_id']
            console=[dict(r) for r in db.execute('SELECT * FROM raw_console WHERE device_id=? AND id>? ORDER BY id',(owner['device_id'],console_start))]
            db.execute("UPDATE analysis_trials SET status='finished',ended_at=?,end_id=?,result=?,evidence=?,console_evidence=? WHERE id=?",
                       (now(),t['end_id'],json.dumps(message.model_dump()),json.dumps(captured),json.dumps(console),identifier))
        return {'finished':True,'record_count':len(captured)}

    @app.get('/api/v1/analysis/trials/{identifier}',dependencies=auth)
    def trial_detail(identifier:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200)):
        with closing(connect()) as db:
            t=trial(db,identifier); owner=session(db,t['session_id'])
            item=serialize_trial(db,t,owner['device_id'],True)
            records=item['records'];item['records']=records[offset:offset+limit]
            console=item['console_records'];item['console_record_count']=len(console)
            item['console_records']=console[offset:offset+limit]
            item.update(offset=offset,limit=limit)
            return item

    @app.get('/api/v1/analysis/sessions/{identifier}/export',dependencies=auth)
    def export(identifier:str):
        with closing(connect()) as db:
            owner=session(db,identifier);owner['protocol']=json.loads(owner['protocol'])
            owner['trials']=[serialize_trial(db,dict(r),owner['device_id'],True) for r in db.execute('SELECT * FROM analysis_trials WHERE session_id=? ORDER BY position',(identifier,))]
            owner['exported_at']=now()
            owner['warning']='Receipt-window records may include offline backlogs. Operator outcomes are not independently verified scores. No automatic accuracy or physical latency is inferred.'
        return Response(json.dumps(owner,indent=2,allow_nan=False),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="analysis-{identifier}.json"'})
