import csv
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.test_app import AUTH, batch, server


def start(client, **overrides):
    return client.post('/api/v1/trips', headers=AUTH, json={
        'name': 'Fragile electronics', 'device_id': 'cargo-01',
        'origin': 'Ahmedabad warehouse', 'destination': 'Pune office', **overrides})


def test_trip_lifecycle_manual_checkpoints_and_export(server):
    client, _ = server
    assert client.get('/api/v1/trips').status_code == 401
    response = start(client)
    assert response.status_code == 201
    trip = response.json()
    url = '/api/v1/trips/' + trip['id']
    assert start(client).status_code == 409
    assert client.post(url + '/logs', headers=AUTH, json={'kind': 'checkpoint', 'message': 'Arrived'}).status_code == 422
    assert client.post(url + '/logs', headers=AUTH, json={'kind': 'checkpoint', 'message': 'Received by hub', 'location': 'Surat hub'}).status_code == 201
    assert client.post(url + '/logs', headers=AUTH, json={'kind': 'note', 'message': '=malicious formula'}).status_code == 201
    detail = client.get(url, headers=AUTH).json()
    assert detail['activity_count'] == 3
    assert detail['checkpoints'][0]['location'] == 'Surat hub'
    assert client.get(url + '?kind=note', headers=AUTH).json()['total'] == 1
    assert len(client.get(url + '?limit=1&offset=1', headers=AUTH).json()['activity']) == 1
    exported = list(csv.DictReader(io.StringIO(client.get(url + '/export', headers=AUTH).text)))
    assert len(exported) == 3
    assert any(row['message'] == "'=malicious formula" for row in exported)
    assert all(row['origin'] == 'Ahmedabad warehouse' for row in exported)
    for _ in range(2):
        assert client.post(url + '/finish', headers=AUTH, json={'status': 'completed'}).status_code == 200
    assert client.get(url, headers=AUTH).json()['activity_count'] == 4
    assert client.post(url + '/logs', headers=AUTH, json={'kind': 'checkpoint', 'message': 'Late', 'location': 'Hub'}).status_code == 409
    assert client.post(url + '/finish', headers=AUTH, json={'status': 'cancelled'}).status_code == 409
    assert start(client).status_code == 201


def test_offline_records_keep_original_trip_and_unknown_records_unassigned(server):
    client, _ = server
    trip_a = start(client).json()['id']
    client.post(f'/api/v1/trips/{trip_a}/finish', headers=AUTH, json={'status': 'completed'})
    trip_b = start(client, name='Second shipment').json()['id']
    old = batch()
    for item in old['records']:
        item['trip_id'] = trip_a
    assert client.post('/api/v1/batches', headers=AUTH, json=old).json()['inserted'] == 2
    for item in old['records']:
        item['trip_id'] = trip_b
    assert client.post('/api/v1/batches', headers=AUTH, json=old).json()['inserted'] == 0
    assert client.get(f'/api/v1/trips/{trip_b}', headers=AUTH).json()['activity_count'] == 1
    assert client.get(f'/api/v1/trips/{trip_a}', headers=AUTH).json()['activity_count'] == 4
    client.post('/api/v1/batches', headers=AUTH, json=batch('old-without-trip'))
    assert client.get('/api/v1/trips', headers=AUTH).json()['unassigned_records'] == 2
    invalid = batch('bad-trip')
    invalid['records'][0]['trip_id'] = 'unknown'
    assert client.post('/api/v1/batches', headers=AUTH, json=invalid).status_code == 422
    wrong_device = start(client, device_id='another-device').json()['id']
    invalid['records'][0]['trip_id'] = wrong_device
    assert client.post('/api/v1/batches', headers=AUTH, json=invalid).status_code == 422


def test_heartbeats_are_trip_scoped_and_sampled(server, monkeypatch):
    client, _ = server
    timestamp = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr('backend.trips.now', lambda: timestamp[0].isoformat())
    heartbeat = {'device_id': 'cargo-01', 'firmware': 'connection-test', 'rssi': -53}
    assert client.post('/api/v1/heartbeat', headers=AUTH, json=heartbeat).json()['active_trip_id'] is None
    trip = start(client).json()['id']
    assert client.post('/api/v1/heartbeat', headers=AUTH, json=heartbeat).json()['active_trip_id'] == trip
    for _ in range(12):
        timestamp[0] += timedelta(seconds=5)
        assert client.post('/api/v1/heartbeat', headers=AUTH, json=heartbeat).status_code == 200
    detail = client.get(f'/api/v1/trips/{trip}', headers=AUTH).json()
    assert detail['activity_count'] == 3  # started, connected, one minute sample
    assert client.get('/api/v1/devices', headers=AUTH).json()['devices'][0]['rssi'] == -53
    timestamp[0] += timedelta(seconds=25)
    client.post('/api/v1/heartbeat', headers=AUTH, json=heartbeat)
    assert client.get(f'/api/v1/trips/{trip}', headers=AUTH).json()['activity_count'] == 4
    client.post(f'/api/v1/trips/{trip}/finish', headers=AUTH, json={'status': 'completed'})
    assert client.post('/api/v1/heartbeat', headers=AUTH, json=heartbeat).json()['active_trip_id'] is None
    assert client.get(f'/api/v1/trips/{trip}', headers=AUTH).json()['activity_count'] == 5


def test_only_one_concurrent_start_and_whitespace_validation(server):
    client, _ = server
    assert start(client, name='   ').status_code == 422
    assert start(client, origin='   ').status_code == 422
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: start(client).status_code, range(2)))
    assert sorted(statuses) == [201, 409]


def test_trip_data_survives_restart_and_legacy_database_migrates(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL, boot_id TEXT NOT NULL, seq INTEGER NOT NULL,
            uptime_ms INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
            received_at TEXT NOT NULL, UNIQUE(device_id,boot_id,seq))''')
        db.execute("INSERT INTO records VALUES (1,'cargo-01','old',1,10,'telemetry','{}','2026-01-01T00:00:00+00:00')")
    with TestClient(create_app(path, 'test-secret')) as client:
        trip = start(client).json()['id']
        client.post('/api/v1/heartbeat', headers=AUTH, json={'device_id': 'cargo-01'})
    with TestClient(create_app(path, 'test-secret')) as client:
        assert client.get('/api/v1/trips', headers=AUTH).json()['unassigned_records'] == 1
        assert client.get(f'/api/v1/trips/{trip}', headers=AUTH).json()['trip']['status'] == 'active'
        assert client.get('/api/v1/devices', headers=AUTH).json()['devices'][0]['connected'] is False


def test_alerts_and_latest_telemetry_are_scoped(server):
    client, _ = server
    trip = start(client).json()['id']
    readings = batch()
    for record in readings['records']:
        record['trip_id'] = trip
    readings['records'][1].update(kind='event', data={'type': 'suspected_tamper', 'detail': 'Lid opened'})
    client.post('/api/v1/batches', headers=AUTH, json=readings)
    detail = client.get(f'/api/v1/trips/{trip}?kind=alert', headers=AUTH).json()
    assert detail['alert_count'] == detail['total'] == 1
    assert detail['activity'][0]['message'] == 'Lid opened'
    assert len(detail['telemetry']) == 1


def test_cancelled_trip_history_and_delayed_records_stay_separate_from_next_trip(server):
    client, _ = server
    old = start(client).json()['id']
    assert client.post(f'/api/v1/trips/{old}/logs', headers=AUTH,
                       json={'kind': 'note', 'message': 'Original trip note'}).status_code == 201
    assert client.post(f'/api/v1/trips/{old}/finish', headers=AUTH,
                       json={'status': 'cancelled'}).status_code == 200
    new = start(client, name='Next cargo').json()['id']
    assert new != old
    assert start(client).status_code == 409
    # Different devices may have one active trip each.
    assert start(client, device_id='cargo-02').status_code == 201
    delayed = batch('delayed')
    for record in delayed['records']:
        record['trip_id'] = old
    delayed['records'][1].update(kind='event', data={'type': 'packaging_separation', 'detail': 'Old package opened'})
    assert client.post('/api/v1/batches', headers=AUTH, json=delayed).json()['inserted'] == 2
    old_detail = client.get(f'/api/v1/trips/{old}', headers=AUTH).json()
    new_detail = client.get(f'/api/v1/trips/{new}', headers=AUTH).json()
    assert old_detail['trip']['status'] == 'cancelled'
    assert old_detail['alert_count'] == 1
    assert any(entry['message'] == 'Original trip note' for entry in old_detail['activity'])
    assert new_detail['alert_count'] == 0 and new_detail['telemetry'] == []
    assert all(entry.get('data', {}).get('type') != 'packaging_separation' for entry in new_detail['activity'])
    assert client.post('/api/v1/heartbeat', headers=AUTH, json={'device_id': 'cargo-01'}).json()['active_trip_id'] == new
    assert old_detail['trip']['id'] == old  # explicit history URL never resolves to active trip


def test_handling_summary_counts_new_alert_types_and_reports_peak_without_duration(server):
    client, _ = server
    trip = start(client).json()['id']
    records = []
    for seq, (event_type, value) in enumerate([
        ('impact', 3.4), ('impact', 5.2), ('impact', 4.1),
        ('strong_vibration', 0.7), ('packaging_separation', 1),
        ('likely_opened', 1), ('unsafe_environment', 30.0),
    ], start=1):
        records.append({'seq': seq, 'uptime_ms': seq * 1000, 'kind': 'event',
                        'trip_id': trip, 'data': {'type': event_type, 'value': value}})
    assert client.post('/api/v1/batches', headers=AUTH,
                       json={'device_id': 'cargo-01', 'boot_id': 'handling', 'records': records}).status_code == 200
    detail = client.get(f'/api/v1/trips/{trip}?kind=alert&limit=1', headers=AUTH).json()
    assert detail['alert_count'] == 7
    summary = detail['handling_summary']
    assert summary['max_impact_g'] == 5.2
    assert summary['max_impact_boot_id'] == 'handling'
    assert summary['max_impact_uptime_ms'] == 2000
    assert summary['max_impact_received_at'].endswith('+00:00')
    assert summary['alert_event_counts'] == {
        'impact': 3, 'strong_vibration': 1, 'packaging_separation': 1,
        'likely_opened': 1, 'unsafe_environment': 1,
    }
    assert not any('duration' in key for key in summary)
