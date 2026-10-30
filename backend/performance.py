"""Read actual device records and measure acknowledged dashboard delivery."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import csv
import io
import json
import os
import statistics
import time
import uuid

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]


def percentile(values, q):
    if not values:
        return None
    values=sorted(values)
    index=(len(values)-1)*q
    lo=int(index);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(index-lo)


def distribution(values):
    return {'n':len(values),'mean':statistics.mean(values) if values else None,
            'median':percentile(values,.5),'p95':percentile(values,.95),'p99':percentile(values,.99),
            'minimum':min(values) if values else None,'maximum':max(values) if values else None,
            'stddev':statistics.stdev(values) if len(values)>1 else 0 if values else None}


def summarize(records):
    """Gaps describe bounded observed sequence spans, never proven packet loss."""
    boots=defaultdict(set)
    for row in records:
        boots[row['boot_id']].add(row['seq'])
    spans=[]
    for boot,seqs in boots.items():
        ordered=sorted(seqs);expected=ordered[-1]-ordered[0]+1
        missing=[]
        for a,b in zip(ordered,ordered[1:]):
            missing.extend(range(a+1,min(b,a+1+100-len(missing))))
            if len(missing)>=100:break
        spans.append({'boot_id':boot,'first_seq':ordered[0],'last_seq':ordered[-1],
                      'observed':len(seqs),'expected':expected,'gaps':expected-len(seqs),'missing_ids':missing})
    observed=sum(b['observed'] for b in spans);expected=sum(b['expected'] for b in spans)
    ordered=sorted(records,key=lambda r:(r['received_at'],r['id']))
    intervals=[]
    for a,b in zip(ordered,ordered[1:]):
        delta=(datetime.fromisoformat(b['received_at'])-datetime.fromisoformat(a['received_at'])).total_seconds()*1000
        if delta>=0:intervals.append(delta)
    span=(datetime.fromisoformat(ordered[-1]['received_at'])-datetime.fromisoformat(ordered[0]['received_at'])).total_seconds() if len(ordered)>1 else 0
    telemetry=[r for r in records if r['kind']=='telemetry']
    latest=next((r for r in reversed(ordered) if r['kind']=='telemetry'),None)
    sensors={}
    for key in ['temperature_c','humidity_pct','total_g','vibration_rms_g','tilt_deg']:
        valid='dht_ok' if key in {'temperature_c','humidity_pct'} else 'imu_ok'
        values=[r['data'][key] for r in telemetry if r['data'].get(valid) is not False
                and (key!='tilt_deg' or r['data'].get('reference_valid') is not False)
                and isinstance(r['data'].get(key),(float,int)) and not isinstance(r['data'].get(key),bool)]
        sensors[key]=distribution(values)
    health={}
    for flag in ['dht_ok','imu_ok','reference_valid']:
        available=[r for r in telemetry if isinstance(r['data'].get(flag),bool)]
        healthy=sum(r['data'][flag] is True for r in available)
        health[flag]={'reported':len(available),'healthy':healthy,'rate':healthy/len(available) if available else None}
    return {'window_count':len(records),'sequence_observed':observed,'sequence_expected':expected,
            'sequence_gaps':expected-observed,'sequence_coverage':observed/expected if expected else None,
            'boots':spans,'intervals':intervals,'interarrival':distribution(intervals),
            'throughput_per_minute':len(records)/span*60 if span>0 else None,
            'window_seconds':span,'telemetry_count':len(telemetry),'event_count':len(records)-len(telemetry),
            'sensors':sensors,'health':health,'latest_telemetry':latest,
            'states':dict(Counter(str(r['data'].get('condition_state','Not reported')) for r in telemetry)),
            'first_received_at':ordered[0]['received_at'] if ordered else None,
            'last_received_at':ordered[-1]['received_at'] if ordered else None}


def init_performance_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS performance_snapshots (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, device_id TEXT NOT NULL,
            created_at TEXT NOT NULL, record_ids TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS performance_receipts (
            client_id TEXT NOT NULL, source TEXT NOT NULL, device_id TEXT NOT NULL,
            record_id INTEGER NOT NULL, received_at TEXT NOT NULL,
            PRIMARY KEY(client_id,source,device_id,record_id)
        );
        CREATE TABLE IF NOT EXISTS performance_browser_timings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT NOT NULL,
            device_id TEXT NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, api_ms REAL NOT NULL, render_ms REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS performance_live_probes (
            id TEXT PRIMARY KEY, device_id TEXT NOT NULL, source TEXT NOT NULL,
            request_id TEXT NOT NULL, started_at TEXT NOT NULL, started_monotonic REAL NOT NULL,
            process_epoch TEXT NOT NULL, status TEXT NOT NULL, elapsed_ms REAL, record_id INTEGER
        );
    ''')


class Acknowledge(BaseModel):
    model_config=ConfigDict(extra='forbid')
    snapshot_id: Identifier
    client_id: Identifier
    record_ids: Annotated[list[Annotated[int,Field(strict=True,ge=1)]],Field(max_length=400)]
    api_ms: Annotated[float,Field(ge=0,le=60000,allow_inf_nan=False)]
    render_ms: Annotated[float,Field(ge=0,le=60000,allow_inf_nan=False)]


class Probe(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id: Identifier = 'cargo-esp32-01'


def install_performance_routes(app,connect,authenticate,connection_status,expected_token,source_url=None):
    auth=[Depends(authenticate)]
    epoch=uuid.uuid4().hex
    # Receiver configuration is local administration, never a request-supplied URL.
    if source_url is None:
        config_path=Path(__file__).parent/'data'/'performance_source.json'
        config=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
        source_url=os.environ.get('PERFORMANCE_SOURCE_URL',config.get('url',''))
    source=(source_url or '').rstrip('/')
    source_key=source or 'local'
    source_token=os.environ.get('PERFORMANCE_SOURCE_TOKEN',expected_token)

    def upstream(path,body=None):
        request=Request(source+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'X-Device-Token':source_token,'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=3) as response:
                return json.load(response)
        except HTTPError as exc:
            try:detail=json.load(exc).get('detail','Receiver rejected the request')
            except (ValueError,OSError):detail='Receiver rejected the request'
            raise HTTPException(exc.code if exc.code in {401,404,409,422} else 502,detail)
        except (OSError,URLError,ValueError):
            raise HTTPException(502,'Cannot reach the ESP32 receiver. Check its network connection.')

    def read_records(device_id,limit):
        if source:
            payload=upstream('/api/v1/records?'+urlencode({'device_id':device_id,'limit':limit}))
            return payload['total'],payload['records']
        with closing(connect()) as db:
            total=db.execute('SELECT COUNT(*) FROM records WHERE device_id=?',(device_id,)).fetchone()[0]
            rows=db.execute('SELECT * FROM records WHERE device_id=? ORDER BY id DESC LIMIT ?',(device_id,limit)).fetchall()
        return total,[{**dict(r),'data':json.loads(r['data'])} for r in rows]

    @app.get('/api/v1/performance',dependencies=auth)
    def live(device_id:Identifier='cargo-esp32-01',limit:int=Query(400,ge=1,le=400),client_id:Identifier='dashboard'):
        started=time.perf_counter()
        total,records=read_records(device_id,limit)
        if source:
            devices=upstream('/api/v1/devices')['devices']
            device=next((d for d in devices if d['device_id']==device_id),{'device_id':device_id,'connected':False})
        else:
            device=connection_status(device_id=device_id)
            with closing(connect()) as db:
                row=db.execute('SELECT * FROM devices WHERE device_id=?',(device_id,)).fetchone()
                if row:device={**dict(row),**device}
        timestamp=datetime.now(timezone.utc).isoformat()
        identifier=uuid.uuid4().hex
        ids=[r['id'] for r in records]
        with closing(connect()) as db,db:
            db.execute('INSERT INTO performance_snapshots VALUES (?,?,?,?,?)',(identifier,source_key,device_id,timestamp,json.dumps(ids)))
            db.execute('DELETE FROM performance_snapshots WHERE rowid NOT IN (SELECT rowid FROM performance_snapshots ORDER BY rowid DESC LIMIT 1000)')
            receipts={r[0] for r in db.execute('SELECT record_id FROM performance_receipts WHERE client_id=? AND source=? AND device_id=?',(client_id,source_key,device_id))}
            timings=[dict(r) for r in db.execute('SELECT api_ms,render_ms,created_at FROM performance_browser_timings WHERE client_id=? AND source=? AND device_id=? ORDER BY id DESC LIMIT 120',(client_id,source_key,device_id))]
            probes=[dict(r) for r in db.execute('SELECT id,status,elapsed_ms,started_at,record_id FROM performance_live_probes WHERE device_id=? AND source=? ORDER BY rowid DESC LIMIT 100',(device_id,source_key))]
        metrics=summarize(records)
        return {'snapshot_id':identifier,'source':'esp32','receiver':source or 'This server','device':device,
                'total_received':total,'records':list(reversed(records)),'metrics':metrics,
                'browser':{'acknowledged_in_window':sum(i in receipts for i in ids),'total_acknowledged':len(receipts),
                           'timings':list(reversed(timings)),
                           'api_latency':distribution([t['api_ms'] for t in timings]),
                           'render_latency':distribution([t['render_ms'] for t in timings])},
                'probes':probes,'probe_latency':distribution([p['elapsed_ms'] for p in probes if p['status']=='received']),
                'updated_at':timestamp,'source_read_ms':(time.perf_counter()-started)*1000,
                'limits':{'device_to_server_latency':'Not instrumented: records contain uptime, not synchronized capture timestamps.',
                          'packet_loss':'Sequence gaps can include queued or delayed records; they do not prove permanent loss.',
                          'accuracy':'No independent ground-truth labels or reference measurements have been collected.'}}

    @app.post('/api/v1/performance/ack',dependencies=auth)
    def acknowledge(message:Acknowledge):
        with closing(connect()) as db,db:
            snapshot=db.execute('SELECT * FROM performance_snapshots WHERE id=?',(message.snapshot_id,)).fetchone()
            if not snapshot:raise HTTPException(404,'Performance snapshot expired; refresh the page')
            allowed=set(json.loads(snapshot['record_ids']));ids=set(message.record_ids)
            if not ids<=allowed:raise HTTPException(422,'Only records included in this snapshot can be acknowledged')
            timestamp=datetime.now(timezone.utc).isoformat()
            db.executemany('INSERT OR IGNORE INTO performance_receipts VALUES (?,?,?,?,?)',
                           [(message.client_id,snapshot['source'],snapshot['device_id'],i,timestamp) for i in ids])
            db.execute('INSERT OR IGNORE INTO performance_browser_timings(client_id,device_id,source,snapshot_id,created_at,api_ms,render_ms) VALUES (?,?,?,?,?,?,?)',
                       (message.client_id,snapshot['device_id'],snapshot['source'],message.snapshot_id,timestamp,message.api_ms,message.render_ms))
            db.execute('DELETE FROM performance_browser_timings WHERE id NOT IN (SELECT id FROM performance_browser_timings ORDER BY id DESC LIMIT 5000)')
        return {'acknowledged':len(ids),'window_count':len(allowed),'coverage':len(ids)/len(allowed) if allowed else None}

    @app.post('/api/v1/performance/probe',dependencies=auth,status_code=202)
    def begin_probe(message:Probe):
        started=time.perf_counter()
        if not source:
            raise HTTPException(409,'Live response measurement needs the configured ESP32 receiver. Browser timing remains available below.')
        with closing(connect()) as db,db:
            active=db.execute("SELECT * FROM performance_live_probes WHERE device_id=? AND source=? AND status='pending' AND process_epoch=? ORDER BY rowid DESC LIMIT 1",(message.device_id,source_key,epoch)).fetchone()
            if active and started-active['started_monotonic']<30:return {'id':active['id'],'status':'pending'}
            response=upstream('/api/v1/fetch',{'device_id':message.device_id})
            identifier=uuid.uuid4().hex
            db.execute('INSERT INTO performance_live_probes VALUES (?,?,?,?,?,?,?,?,NULL,NULL)',
                       (identifier,message.device_id,source_key,response['request_id'],datetime.now(timezone.utc).isoformat(),started,epoch,'pending'))
        return {'id':identifier,'status':'pending'}

    @app.get('/api/v1/performance/probe/{identifier}',dependencies=auth)
    def probe_status(identifier:str):
        with closing(connect()) as db:
            row=db.execute('SELECT * FROM performance_live_probes WHERE id=?',(identifier,)).fetchone()
        if not row:raise HTTPException(404,'Response measurement not found')
        row=dict(row)
        if row['status']!='pending':return {'id':identifier,'status':row['status'],'elapsed_ms':row['elapsed_ms'],'record_id':row['record_id']}
        # A restart invalidates a process-local monotonic clock measurement.
        status='interrupted' if row['process_epoch']!=epoch else 'pending'
        elapsed=(time.perf_counter()-row['started_monotonic'])*1000
        match=None
        if status=='pending':
            _,records=read_records(row['device_id'],400)
            match=next((r for r in records if r['kind']=='telemetry' and r['data'].get('fetch_request_id')==row['request_id']),None)
            elapsed=(time.perf_counter()-row['started_monotonic'])*1000
            status='received' if match else 'timeout' if elapsed>=30000 else 'pending'
        if status!='pending':
            with closing(connect()) as db,db:
                db.execute("UPDATE performance_live_probes SET status=?,elapsed_ms=?,record_id=? WHERE id=? AND status='pending'",(status,elapsed if status=='received' else None,match['id'] if match else None,identifier))
        return {'id':identifier,'status':status,'elapsed_ms':elapsed if status=='received' else None,'record_id':match['id'] if match else None}

    @app.get('/api/v1/performance/export',dependencies=auth)
    def export(device_id:Identifier='cargo-esp32-01',limit:int=Query(400,ge=1,le=400),format:str='csv',client_id:Identifier='dashboard'):
        if format not in {'csv','json'}:raise HTTPException(422,'Choose csv or json')
        headers={'Content-Disposition':f'attachment; filename="esp32-performance.{format}"'}
        if format=='json':
            return Response(json.dumps(live(device_id,limit,client_id),indent=2,allow_nan=False),media_type='application/json',headers=headers)
        total,records=read_records(device_id,limit)
        output=io.StringIO(newline='');writer=csv.writer(output)
        writer.writerow(['record_id','device_id','boot_id','sequence','device_uptime_ms','received_at','kind','data'])
        def safe(v):
            value=str(v)
            return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
        for r in reversed(records):writer.writerow([safe(v) for v in [r['id'],r['device_id'],r['boot_id'],r['seq'],r['uptime_ms'],r['received_at'],r['kind'],json.dumps(r['data'])]])
        return Response(output.getvalue(),media_type='text/csv',headers=headers)
