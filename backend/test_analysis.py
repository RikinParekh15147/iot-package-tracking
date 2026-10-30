"""Guided trials record evidence; tests never create trials in the live database."""
import json
import sqlite3
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.test_app import AUTH, server, batch


def protocol(device='cargo-01', scenarios=None):
    return dict(name='Pilot <test>',device_id=device,stage='pilot',repetitions=1,
                scenarios=scenarios or ['closed_still'],setup='Empty box; fixed sensors',
                criteria='Closed stays closed; retain false alerts',reference='Visual check and video')


def create(client, **kwargs):
    r=client.post('/api/v1/analysis/sessions',headers=AUTH,json=protocol(**kwargs))
    assert r.status_code==201,r.text
    identifier=r.json()['id']
    return client.get('/api/v1/analysis/sessions/'+identifier,headers=AUTH).json()


def post(client,trial,suffix,body):
    return client.post('/api/v1/analysis/trials/'+trial+'/'+suffix,headers=AUTH,json=body)


def test_auth_input_validation_and_no_automatic_trials(server):
    c,_=server
    assert c.get('/api/v1/analysis/sessions').status_code==401
    assert c.post('/api/v1/analysis/sessions',json=protocol()).status_code==401
    assert c.get('/analysis').status_code==200
    assert c.get('/api/v1/analysis/sessions',headers=AUTH).json()=={'sessions':[]}
    for changes in [{'scenarios':['invented']},{'scenarios':['closed_still','closed_still']},
                    {'setup':''},{'criteria':' '},{'reference':''},{'repetitions':0},{'repetitions':True}]:
        assert c.post('/api/v1/analysis/sessions',headers=AUTH,json={**protocol(),**changes}).status_code==422
    assert c.get('/api/v1/analysis/sessions/missing',headers=AUTH).status_code==404
    s=create(c,scenarios=['closed_still','open_bright'])
    assert len(s['trials'])==2 and all(t['status']=='pending' for t in s['trials'])
    assert len(s['protocol']['source_fingerprint'])==3
    assert s['protocol']['association']=='server_receipt_window'
    assert post(c,s['trials'][1]['id'],'start',{}).status_code==409


def test_trial_markers_evidence_isolation_immutable_finish_and_late_arrival(server):
    c,path=server
    c.post('/api/v1/batches',headers=AUTH,json=batch())
    s=create(c);t=s['trials'][0]['id']
    assert post(c,t,'markers',{'kind':'action'}).status_code==409
    assert post(c,t,'start',{}).status_code==200
    assert post(c,t,'start',{}).status_code==409
    other=create(c)
    assert post(c,other['trials'][0]['id'],'start',{}).status_code==409
    marker=post(c,t,'markers',{'kind':'action','note':'Opened physical lid'})
    assert marker.status_code==201
    body=batch('new-boot');c.post('/api/v1/batches',headers=AUTH,json=body)
    c.post('/api/v1/batches',headers=AUTH,json=body) # retry does not create evidence duplicates
    foreign=batch('foreign');foreign['device_id']='other';c.post('/api/v1/batches',headers=AUTH,json=foreign)
    assert post(c,t,'finish',{'outcome':'inconclusive','observed':' '}).status_code==422
    r=post(c,t,'finish',{'outcome':'inconclusive','observed':'No independent timing reference','deviation':'Network changed'})
    assert r.status_code==200 and r.json()['record_count']==2
    c.post('/api/v1/batches',headers=AUTH,json=batch('late-boot'))
    frozen=c.get('/api/v1/analysis/trials/'+t,headers=AUTH).json()
    assert frozen['status']=='finished'
    assert frozen['record_count']==2
    assert all(r['boot_id']=='new-boot' for r in frozen['records'])
    assert len(frozen['markers'])==1
    assert post(c,t,'finish',{'outcome':'observed','observed':'overwrite'}).status_code==409
    assert post(c,t,'markers',{'kind':'note'}).status_code==409
    export=c.get('/api/v1/analysis/sessions/'+s['id']+'/export',headers=AUTH)
    assert 'attachment' in export.headers['content-disposition']
    assert export.json()['trials'][0]['records']==frozen['records']
    assert 'Receipt-window' in export.json()['warning']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0]==8


def test_analysis_persists_across_restart_and_paginates(tmp_path):
    path=tmp_path/'analysis.db'
    with TestClient(create_app(path,'test-secret')) as c:
        s=create(c);t=s['trials'][0]['id'];post(c,t,'start',{})
        c.post('/api/v1/batches',headers=AUTH,json=batch())
        post(c,t,'finish',{'outcome':'aborted','observed':'Stopped trial intentionally'})
    with TestClient(create_app(path,'test-secret')) as c:
        got=c.get('/api/v1/analysis/trials/'+t+'?offset=1&limit=1',headers=AUTH).json()
        assert got['record_count']==2 and len(got['records'])==1 and got['records'][0]['seq']==2
        assert got['result']['outcome']=='aborted'
        assert c.get('/api/v1/analysis/trials/'+t+'?limit=999',headers=AUTH).status_code==422


def test_console_evidence_and_protocol_are_preserved_with_finished_trial(server):
    c,path=server
    s=create(c);t=s['trials'][0]['id']
    post(c,t,'start',{})
    console={'device_id':'cargo-01','boot_id':'test-console','lines':[{'seq':1,'uptime_ms':100,'message':'Event line'}]}
    assert c.post('/api/v1/console',headers=AUTH,json=console).status_code==200
    post(c,t,'finish',{'outcome':'inconclusive','observed':'No reliable reference'})
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM raw_console')
    frozen=c.get('/api/v1/analysis/trials/'+t,headers=AUTH).json()
    assert frozen['console_records'][0]['message']=='Event line'
    assert frozen['console_record_count']==1
    assert c.get('/api/v1/analysis/sessions/'+s['id'],headers=AUTH).json()['protocol']['criteria']==protocol()['criteria']
