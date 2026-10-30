"""Real-record performance calculations and browser receipt accounting."""
import csv
import io
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.performance import percentile, summarize
from backend.test_app import AUTH, server


def records():
    return [dict(id=i,device_id='cargo-01',boot_id='boot-a',seq=seq,uptime_ms=seq*1000,
                 kind='telemetry',received_at=f'2026-10-30T10:00:0{i}+00:00',
                 data={'dht_ok':True,'imu_ok':True,'reference_valid':False,'temperature_c':24+i,
                       'humidity_pct':50,'tilt_deg':42,'condition_state':'normal'})
            for i,seq in [(1,10),(2,11),(3,13)]]


def seed(client):
    rows=records()
    response=client.post('/api/v1/batches',headers=AUTH,json={
        'device_id':'cargo-01','boot_id':'boot-a','records':[
            {k:r[k] for k in ['seq','uptime_ms','kind','data']} for r in rows]})
    assert response.status_code==200


def test_observed_sequence_spans_not_assumed_total_loss():
    data=summarize(records())
    assert data['window_count']==3
    assert data['sequence_expected']==4
    assert data['sequence_observed']==3
    assert data['sequence_gaps']==1
    assert data['sequence_coverage']==.75
    assert data['boots'][0]['missing_ids']==[12]
    assert data['interarrival']['median']==1000
    assert data['throughput_per_minute']==90
    assert data['sensors']['temperature_c']['mean']==26
    assert data['sensors']['tilt_deg']['n']==0  # invalid orientation is not a measured angle
    assert data['health']['reference_valid']['rate']==0
    other=records()[0];other={**other,'id':4,'boot_id':'boot-b','seq':1}
    assert summarize(records()+[other])['sequence_gaps']==1
    assert summarize([])['sequence_coverage'] is None
    assert summarize([])['interarrival']['p95'] is None
    assert percentile([100,200],.95)==195
    assert json.dumps(summarize([]),allow_nan=False)


def test_live_endpoint_empty_stays_empty_and_requires_auth(server):
    client,path=server
    assert client.get('/api/v1/performance').status_code==401
    assert client.post('/api/v1/performance',headers=AUTH,json={}).status_code==405
    result=client.get('/api/v1/performance',headers=AUTH).json()
    assert result['source']=='esp32'
    assert result['total_received']==0
    assert result['records']==[]
    assert result['metrics']['window_count']==0
    assert result['browser']['api_latency']['n']==0
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='performance_runs'").fetchone()[0]==0


def test_acknowledgements_scoped_to_snapshot_client_and_device(server):
    client,_=server;seed(client)
    snapshot=client.get('/api/v1/performance?device_id=cargo-01&client_id=browser-a',headers=AUTH).json()
    assert snapshot['total_received']==3
    assert snapshot['browser']['acknowledged_in_window']==0
    ids=[r['id'] for r in snapshot['records']]
    body={'snapshot_id':snapshot['snapshot_id'],'client_id':'browser-a','record_ids':ids,'api_ms':42,'render_ms':16}
    assert client.post('/api/v1/performance/ack',json=body).status_code==401
    assert client.post('/api/v1/performance/ack',headers=AUTH,json={**body,'record_ids':[999999]}).status_code==422
    for _ in range(2):
        assert client.post('/api/v1/performance/ack',headers=AUTH,json=body).json()['coverage']==1
    result=client.get('/api/v1/performance?device_id=cargo-01&client_id=browser-a',headers=AUTH).json()
    assert result['browser']['acknowledged_in_window']==3
    assert result['browser']['total_acknowledged']==3
    assert result['browser']['api_latency']['median']==42
    assert result['browser']['api_latency']['n']==1
    assert client.get('/api/v1/performance?device_id=cargo-01&client_id=browser-b',headers=AUTH).json()['browser']['total_acknowledged']==0
    assert client.get('/api/v1/performance?device_id=another&client_id=browser-a',headers=AUTH).json()['total_received']==0
    assert client.post('/api/v1/performance/ack',headers=AUTH,json={**body,'snapshot_id':'missing'}).status_code==404


def test_exports_only_received_rows_and_unavailable_metrics(server):
    client,_=server;seed(client)
    url='/api/v1/performance/export?device_id=cargo-01'
    assert client.get(url).status_code==401
    rows=list(csv.DictReader(io.StringIO(client.get(url,headers=AUTH).text)))
    assert len(rows)==3
    assert [int(r['sequence']) for r in rows]==[10,11,13]
    report=client.get(url+'&format=json',headers=AUTH).json()
    assert report['total_received']==3
    assert report['source']=='esp32'
    assert len(report['records'])==3
    assert 'classification' not in report['metrics']
    assert client.get(url+'&format=invalid',headers=AUTH).status_code==422
    for query in ['limit=401','limit=0','device_id=']:
        assert client.get('/api/v1/performance?'+query,headers=AUTH).status_code==422


def test_receiver_connection_and_matching_live_probe(tmp_path,monkeypatch):
    import backend.performance as module
    source_records=records();counter=[100.0];request_ids=[]
    monkeypatch.setattr(module.time,'perf_counter',lambda:counter[0])
    class Reply(io.BytesIO):
        pass
    def receiver(request,timeout):
        url=request.full_url
        if '/api/v1/records?' in url:payload={'total':1234,'records':source_records}
        elif url.endswith('/api/v1/devices'):payload={'devices':[{'device_id':'cargo-01','connected':True}]}
        elif url.endswith('/api/v1/fetch'):
            assert json.loads(request.data)['device_id']=='cargo-01'
            payload={'request_id':'actual-request-id'};request_ids.append(payload['request_id'])
        else:raise AssertionError(url)
        return Reply(json.dumps(payload).encode())
    monkeypatch.setattr(module,'urlopen',receiver)
    path=tmp_path/'live.db'
    with TestClient(create_app(path,'test-secret',performance_source='http://receiver')) as client:
        detail=client.get('/api/v1/performance?device_id=cargo-01',headers=AUTH).json()
        assert detail['total_received']==1234
        assert detail['metrics']['window_count']==3
        assert detail['device']['connected'] is True
        probe=client.post('/api/v1/performance/probe',headers=AUTH,json={'device_id':'cargo-01'}).json()
        same=client.post('/api/v1/performance/probe',headers=AUTH,json={'device_id':'cargo-01'}).json()
        assert same['id']==probe['id'] and len(request_ids)==1
        url='/api/v1/performance/probe/'+probe['id']
        counter[0]+=1
        assert client.get(url,headers=AUTH).json()['status']=='pending'
        source_records[0]['data']['fetch_request_id']='actual-request-id'
        counter[0]+=1.5
        result=client.get(url,headers=AUTH).json()
        assert result['status']=='received' and result['elapsed_ms']==2500
        counter[0]+=5
        assert client.get(url,headers=AUTH).json()['elapsed_ms']==2500
    with TestClient(create_app(path,'test-secret',performance_source='http://receiver')) as client:
        assert client.get('/api/v1/performance?device_id=cargo-01',headers=AUTH).json()['probe_latency']['median']==2500


def test_probe_timeout_and_no_local_fabrication(tmp_path,monkeypatch):
    import backend.performance as module
    counter=[100.0]
    monkeypatch.setattr(module.time,'perf_counter',lambda:counter[0])
    def receiver(request,timeout):
        payload={'request_id':'not-yet-arrived'} if request.data else {'total':0,'records':[]}
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(module,'urlopen',receiver)
    with TestClient(create_app(tmp_path/'probe.db','test-secret',performance_source='http://receiver')) as client:
        probe=client.post('/api/v1/performance/probe',headers=AUTH,json={}).json()
        counter[0]+=31
        result=client.get('/api/v1/performance/probe/'+probe['id'],headers=AUTH).json()
        assert result['status']=='timeout' and result['elapsed_ms'] is None


def test_tab_and_assets(server):
    client,_=server
    assert 'data-view="performance"' in client.get('/').text
    for name in ['performance.css','performance.js']:
        assert client.get('/assets/'+name).status_code==200
