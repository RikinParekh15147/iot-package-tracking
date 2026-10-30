"""Real-browser workflows against an isolated server/database, never the live cargo DB."""
import csv
import io
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright, expect
from backend.app import create_app

TOKEN = "browser-test-token"

@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        channel = os.environ.get("CARGO_BROWSER_CHANNEL", "msedge" if os.name == "nt" else "chromium")
        b = p.chromium.launch(channel=channel, headless=True)
        yield b
        b.close()

@pytest.fixture
def web(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "browser.db", TOKEN), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert thread.is_alive() and time.time() < deadline, "Browser test server failed to start"
        time.sleep(0.02)
    url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=url, headers={"X-Device-Token": TOKEN}) as api:
        yield url, api
    server.should_exit = True
    thread.join(5)
    sock.close()
    assert not thread.is_alive(), "Browser test server did not shut down"

@pytest.fixture
def page(browser, web):
    context = browser.new_context(accept_downloads=True)
    context.add_init_script("sessionStorage.setItem('waypost-token', 'browser-test-token')")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert not errors, "Browser JavaScript errors: " + repr(errors)


def load_dashboard(page, web):
    page.goto(web[0])
    expect(page.locator("#sync-status")).to_contain_text("Updated", timeout=10000)


def test_trip_checkpoint_note_export_finish(page, web):
    load_dashboard(page, web)
    page.get_by_role("button", name="Start a trip", exact=True).click()
    form = page.locator("#trip-form")
    form.get_by_label("Trip name").fill("Fragile prototype shipment")
    form.get_by_label("Device ID").fill("browser-board")
    form.get_by_label("Pickup location").fill("Warehouse A")
    form.get_by_label("Destination", exact=True).fill("Lab B")
    form.get_by_role("button", name="Start trip", exact=True).click()
    expect(page.locator(".trip-title")).to_have_text("Fragile prototype shipment")
    page.get_by_role("button", name="Add checkpoint", exact=False).click()
    page.locator("#log-form").get_by_label("Checkpoint location").fill("Hub C")
    page.locator("#log-form").get_by_label("Details").fill("Seal inspected")
    page.locator("#log-form").get_by_role("button", name="Save activity").click()
    expect(page.locator(".checkpoint-row")).to_contain_text("Hub C")
    page.get_by_role("button", name="Add note", exact=False).click()
    page.locator("#log-form").get_by_label("Details").fill("=test-formula")
    page.locator("#log-form").get_by_role("button", name="Save activity").click()
    expect(page.locator("#log-dialog")).not_to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="Export", exact=True).click()
    exported = Path(download.value.path()).read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(exported)))
    assert any(row["location"] == "Hub C" for row in rows)
    assert any(row["message"] == "'=test-formula" for row in rows)
    page.get_by_role("button", name="Finish trip", exact=True).click()
    page.locator("#finish-form").get_by_role("button", name="Confirm & finish").click()
    expect(page.locator("#content")).to_contain_text("Delivered")
    expect(page.get_by_role("button", name="Add checkpoint", exact=False)).to_have_count(0)
    assert web[1].get("/api/v1/trips").json()["trips"][0]["status"] == "completed"


def test_bad_token_can_recover_via_settings(page, web):
    page.goto(web[0])
    page.evaluate("sessionStorage.setItem('waypost-token','wrong')")
    # Override the init script for this navigation with a later script.
    page.context.add_init_script("sessionStorage.setItem('waypost-token','wrong')")
    page.reload()
    expect(page.locator("#error-banner")).to_contain_text("token rejected", timeout=10000)
    page.locator("#settings-open").click()
    page.locator("#settings-form").get_by_label("Device token").fill(TOKEN)
    page.locator("#settings-form").get_by_role("button", name="Connect").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#sync-status")).to_contain_text("Updated")
    expect(page.locator("#error-banner")).not_to_be_visible()


def test_server_outage_cannot_show_device_healthy(page, web):
    api = web[1]
    assert api.post("/api/v1/heartbeat", json={"device_id": "browser-board"}).status_code == 200
    load_dashboard(page, web)
    expect(page.locator("#sidebar-state")).to_have_text("Device connected")
    page.route("**/api/v1/**", lambda route: route.abort())
    expect(page.locator("#sidebar-state")).to_have_text("Server unavailable", timeout=12000)
    expect(page.locator(".connection-row")).to_contain_text("Unknown")
    page.unroute("**/api/v1/**")
    expect(page.locator("#error-banner")).not_to_be_visible(timeout=12000)
    expect(page.locator("#sidebar-state")).to_have_text("Device connected")


def test_raw_records_render_text_and_filter(page, web):
    api = web[1]
    assert api.post("/api/v1/batches", json={"device_id": "browser-board", "boot_id": "ui-test",
        "records": [{"seq": 1, "uptime_ms": 25, "kind": "event", "data": {
            "detail": "<img src=x onerror='window.injected=true'>"}}]}).status_code == 200
    page.goto(web[0] + "/records")
    page.get_by_label("Device token").fill(TOKEN)
    page.get_by_role("button", name="Connect").click()
    expect(page.locator("#rows tr")).to_have_count(1)
    expect(page.locator("#rows pre")).to_contain_text("<img")
    expect(page.locator("#rows img")).to_have_count(0)
    assert page.evaluate("window.injected === undefined")
    page.get_by_label("Device filter").fill("missing-board")
    page.get_by_role("button", name="Connect").click()
    expect(page.locator("#rows tr")).to_have_count(0)
    page.get_by_label("Device token").fill("wrong")
    page.get_by_role("button", name="Connect").click()
    expect(page.locator("#status")).to_contain_text("Token rejected")


def test_connection_page_times_out_and_reconnects(page, web, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("backend.app.time", SimpleNamespace(monotonic=lambda: clock[0]))
    page.goto(web[0] + "/connection")
    page.locator("#device").fill("browser-board")
    page.locator("#token").fill(TOKEN)
    page.get_by_role("button", name="Check connection").click()
    expect(page.locator("#state")).to_have_text("Waiting for ESP32")
    web[1].post("/api/v1/heartbeat", json={"device_id": "browser-board"})
    expect(page.locator("#state")).to_have_text("ESP32 is connected", timeout=7000)
    clock[0] += 21
    expect(page.locator("#state")).to_have_text("ESP32 is offline", timeout=7000)
    web[1].post("/api/v1/heartbeat", json={"device_id": "browser-board"})
    expect(page.locator("#state")).to_have_text("ESP32 is connected", timeout=7000)


def test_mobile_layout_and_navigation(page, web):
    page.set_viewport_size({"width": 390, "height": 844})
    load_dashboard(page, web)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Trips", exact=True).click()
    expect(page.locator("#page-title")).to_have_text("Your journeys")
    page.get_by_role("button", name="Device", exact=True).click()
    expect(page.locator("#page-title")).to_have_text("Connected to your cargo")


def test_fetch_requests_new_snapshot_instead_of_reloading_old_record(page, web):
    api = web[1]
    assert api.post('/api/v1/heartbeat', json={'device_id': 'browser-board',
        'firmware': 'cargo-monitor', 'supports_fetch': True}).status_code == 200
    old = {'device_id': 'browser-board', 'boot_id': 'fetch-test', 'records': [
        {'seq': 1, 'uptime_ms': 100, 'kind': 'telemetry', 'data': {'temperature_c': 10, 'dht_ok': True}}]}
    assert api.post('/api/v1/batches', json=old).status_code == 200
    load_dashboard(page, web)
    expect(page.locator('.sensor').filter(has_text='Temperature').locator('.sensor-value')).to_contain_text('10.0')
    page.get_by_role('button', name='Fetch live data', exact=True).click()
    expect(page.get_by_role('button', name='Waiting for device…', exact=True)).to_be_disabled()
    pending = api.get('/api/v1/latest?device_id=browser-board').json()['request']
    assert pending is not None
    request_id = pending['request_id']
    heartbeat = api.post('/api/v1/heartbeat', json={'device_id': 'browser-board', 'supports_fetch': True}).json()
    assert heartbeat['telemetry_request_id'] == request_id
    # Act as the device: only this new captured record satisfies the fetch.
    old['records'][0].update(seq=2, uptime_ms=200, data={
        'temperature_c': 27.5, 'humidity_pct': 45, 'dht_ok': True, 'fetch_request_id': request_id})
    assert api.post('/api/v1/batches', json=old).status_code == 200
    expect(page.get_by_role('button', name='Fetch live data', exact=True)).to_be_enabled(timeout=12000)
    expect(page.locator('.sensor').filter(has_text='Temperature').locator('.sensor-value')).to_contain_text('27.5')
    expect(page.get_by_role('heading', name='Live device snapshot')).to_be_visible()


def test_fetch_offline_is_actionable_and_button_recovers(page, web):
    load_dashboard(page, web)
    page.get_by_role('button', name='Fetch live data', exact=True).click()
    expect(page.locator('#error-banner')).to_contain_text('offline')
    expect(page.get_by_role('button', name='Fetch live data', exact=True)).to_be_enabled()


def test_wifi_settings_priority_save_redaction_and_confirmation(page, web):
    load_dashboard(page, web)
    page.locator('#wifi-open').click()
    form = page.locator('#wifi-form')
    expect(page.locator('#wifi-status')).to_contain_text('No networks saved')
    page.get_by_role('button', name='Add network', exact=True).click()
    row = page.locator('.wifi-network-row').first
    row.get_by_label('Network name (SSID)').fill('Test hotspot')
    row.get_by_label('Password', exact=True).fill('only-test-password')
    row.get_by_label('Priority', exact=True).fill('0')
    form.get_by_role('button', name='Save Wi-Fi settings').click()
    expect(page.locator('#wifi-status')).to_contain_text('revision 1 saved')
    expect(page.locator('.wifi-network-row').get_by_label('Password', exact=True)).to_have_value('')
    config = web[1].get('/api/v1/wifi?device_id=cargo-esp32-01').json()
    assert 'only-test-password' not in str(config)
    assert config['profiles'] == [{'ssid': 'Test hotspot', 'priority': 0, 'has_password': True}]
    assert web[1].post('/api/v1/heartbeat', json={'device_id':'cargo-esp32-01','wifi_revision':1}).status_code == 200
    expect(page.locator('#wifi-status')).to_contain_text('confirmed by device', timeout=12000)
    page.locator('.wifi-network-row').get_by_label('Priority', exact=True).fill('10')
    form.get_by_role('button', name='Save Wi-Fi settings').click()
    expect(page.locator('#wifi-status')).to_contain_text('revision 2 saved')
    heartbeat = web[1].post('/api/v1/heartbeat', json={'device_id':'cargo-esp32-01','wifi_revision':1}).json()
    assert heartbeat['wifi_config']['profiles'][0]['password'] == 'only-test-password'


def test_dashboard_displays_uncertain_states_from_evidence(page, web):
    api = web[1]
    api.post('/api/v1/heartbeat', json={'device_id':'browser-board','firmware':'cargo-monitor'})
    api.post('/api/v1/batches', json={'device_id':'browser-board','boot_id':'state-ui','records':[
        {'seq':1,'uptime_ms':100,'kind':'telemetry','data':{
            'seal_state':'possible_packaging_separation','seal_evidence':'Internal light despite reed closed',
            'condition_state':'strong_vibration','movement_state':'strong_vibration'}}]})
    load_dashboard(page, web)
    expect(page.locator('.sensor').filter(has_text='Seal assessment')).to_contain_text('Possible packaging gap')
    expect(page.locator('.sensor').filter(has_text='Seal assessment')).to_contain_text('Internal light despite reed closed')
    expect(page.locator('.sensor').filter(has_text='Handling state')).to_contain_text('Strong vibration')


def test_device_cannot_start_second_trip_and_history_stays_separate(page, web):
    api = web[1]
    body = {'name':'First fragile shipment','device_id':'cargo-esp32-01','origin':'A','destination':'B'}
    first = api.post('/api/v1/trips', json=body).json()['id']
    load_dashboard(page, web)
    page.get_by_role('button', name='Start a trip', exact=True).click()
    expect(page.locator('#trip-form').get_by_role('button', name='Start trip', exact=True)).to_be_disabled()
    expect(page.locator('#trip-form .form-error')).to_contain_text('already on')
    page.get_by_role('button', name='Close start trip', exact=True).click()
    assert api.post('/api/v1/trips/'+first+'/finish', json={'status':'completed'}).status_code == 200
    body['name'] = 'Second shipment'
    second = api.post('/api/v1/trips', json=body).json()['id']
    records = {'device_id':'cargo-esp32-01','boot_id':'history','records':[
        {'seq':1,'uptime_ms':10,'trip_id':first,'kind':'telemetry','data':{'temperature_c':10,'dht_ok':True}},
        {'seq':2,'uptime_ms':20,'trip_id':second,'kind':'telemetry','data':{'temperature_c':27,'dht_ok':True}}]}
    assert api.post('/api/v1/batches', json=records).status_code == 200
    page.reload()
    expect(page.locator('#sync-status')).to_contain_text('Updated')
    page.get_by_label('Select trip', exact=True).select_option(first)
    expect(page.locator('.trip-title')).to_have_text('First fragile shipment')
    expect(page.locator('.sensor').filter(has_text='Temperature').locator('.sensor-value')).to_contain_text('10.0')
    page.get_by_label('Select trip', exact=True).select_option(second)
    expect(page.locator('.trip-title')).to_have_text('Second shipment')
    expect(page.locator('.sensor').filter(has_text='Temperature').locator('.sensor-value')).to_contain_text('27.0')


def test_raw_console_search_pause_resume_and_text_safety(page, web):
    api = web[1]
    payload = {'device_id':'cargo-esp32-01','boot_id':'console-ui','dropped_lines':2,'lines':[
        {'seq':1,'uptime_ms':1000,'message':'IMU FAULT <img src=x onerror=alert(1)>'},
        {'seq':2,'uptime_ms':2000,'message':'HEARTBEAT HTTP=200'}]}
    assert api.post('/api/v1/console', json=payload).status_code == 200
    page.goto(web[0]+'/console')
    page.get_by_role('button', name='Connect logs', exact=True).click()
    out = page.get_by_label('ESP console output')
    expect(out).to_contain_text('[1.000s]')
    expect(out).to_contain_text('IMU FAULT <img')
    expect(out.locator('img')).to_have_count(0)
    expect(page.locator('#status')).to_contain_text('Dropped before upload this reported boot: 2')
    page.get_by_label('Search logs').fill('FAULT')
    expect(out).not_to_contain_text('HEARTBEAT')
    page.get_by_label('Search logs').fill('')
    page.get_by_role('button', name='Pause', exact=True).click()
    payload['lines'] = [{'seq':3,'uptime_ms':3000,'message':'FETCH new reading'}]
    assert api.post('/api/v1/console', json=payload).status_code == 200
    page.clock.install()
    page.clock.fast_forward(3000)
    expect(out).not_to_contain_text('FETCH new reading')
    page.get_by_role('button', name='Resume', exact=True).click()
    expect(out).to_contain_text('FETCH new reading')


def test_mpu_axes_and_ldr_visible_without_hiding_sensor_failures(page, web):
    api = web[1]
    api.post('/api/v1/heartbeat', json={'device_id':'cargo-esp32-01','firmware':'cargo-monitor'})
    body = {'device_id':'cargo-esp32-01','boot_id':'sensor-ui','records':[
        {'seq':1,'uptime_ms':100,'kind':'telemetry','data':{'imu_ok':True,'reference_valid':True,
            'ax_g':-0.1,'ay_g':0.02,'az_g':0.94,'total_g':0.946,
            'gx_rad_s':0.01,'gy_rad_s':0.005,'gz_rad_s':-0.01,
            'ldr_enabled':True,'ldr_raw':3132,'light_exposed':True}}]}
    assert api.post('/api/v1/batches', json=body).status_code == 200
    load_dashboard(page, web)
    expect(page.locator('.mpu-readings div').filter(has_text='Acceleration X')).to_contain_text('-0.100 g')
    expect(page.locator('.mpu-readings div').filter(has_text='Gyro Y')).to_contain_text('0.005 rad/s')
    expect(page.locator('.sensor').filter(has_text='Internal light')).to_contain_text('3132')
    expect(page.locator('.sensor').filter(has_text='Internal light')).to_contain_text('Light exposure detected')
    body['records'][0].update(seq=2, uptime_ms=200, data={'imu_ok':False,'reference_valid':False,'ldr_enabled':False})
    assert api.post('/api/v1/batches', json=body).status_code == 200
    expect(page.locator('.mpu-panel .badge')).to_have_text('Sensor unavailable', timeout=12000)
    expect(page.locator('.mpu-readings div').filter(has_text='Acceleration X').locator('dd')).to_have_text('—')
    expect(page.locator('.sensor').filter(has_text='Internal light')).to_contain_text('Disabled')


def create_state_trip(api, name='State history', device='cargo-esp32-01', samples=10, seal='likely_closed'):
    response = api.post('/api/v1/trips', json={'name':name,'device_id':device,'origin':'Warehouse','destination':'Laboratory'})
    assert response.status_code == 201
    trip = response.json()['id']
    body = {'device_id':device,'boot_id':'state-boot-'+trip,'records':[
        {'seq':i+1,'uptime_ms':i*5000,'kind':'telemetry','trip_id':trip,
         'data':{'seal_state':seal,'condition_state':'normal','movement_state':'still',
                 'temperature_c':25,'dht_ok':True}} for i in range(samples)]}
    assert api.post('/api/v1/batches', json=body).status_code == 200
    return trip, body


def test_trip_opens_grouped_states_and_original_logs(page, web):
    api = web[1]
    trip, body = create_state_trip(api)
    assert api.post('/api/v1/trips/'+trip+'/logs', json={'kind':'note','message':'Warehouse inspection'}).status_code == 201
    load_dashboard(page, web)
    page.get_by_role('button', name='Trips', exact=True).click()
    page.get_by_role('button', name='State history', exact=True).click()
    expect(page.locator('#page-title')).to_have_text('Trip details')
    expect(page).to_have_url(web[0]+'/?trip='+trip)
    run = page.locator('.state-run').filter(has_text='Likely closed')
    expect(run).to_have_count(1)
    expect(run).to_contain_text('About 50 s')
    expect(run).to_contain_text('10 readings')
    expect(run).to_contain_text('Observed span: 45 s')
    expect(page.locator('.trip-other-logs')).to_contain_text('Warehouse inspection')
    run.get_by_role('button', name='Show 10 logs', exact=True).click()
    expect(run.locator('tbody tr')).to_have_count(10)
    expect(run.locator('tbody tr').first).to_contain_text('Likely closed')
    run.get_by_role('button', name='Raw JSON', exact=True).first.click()
    expect(page.locator('#detail-json')).to_contain_text(trip)
    expect(page.locator('#detail-json')).to_contain_text('"seal_state": "likely_closed"')
    page.get_by_role('button', name='Close activity details', exact=True).click()
    page.reload()
    expect(page.locator('#page-title')).to_have_text('Trip details')
    expect(page.locator('.trip-title')).to_have_text('State history')


def test_grouped_logs_member_pagination(page, web):
    trip, _ = create_state_trip(web[1], samples=45)
    page.goto(web[0]+'/?trip='+trip)
    expect(page.locator('#sync-status')).to_contain_text('Updated')
    page.get_by_role('button', name='Show 45 logs', exact=True).click()
    expect(page.locator('.state-samples tbody tr')).to_have_count(20)
    expect(page.locator('.state-samples tbody tr').first).to_contain_text('Sequence 1')
    page.get_by_role('button', name='Next readings', exact=True).click()
    expect(page.locator('.state-samples tbody tr').first).to_contain_text('Sequence 21')
    page.get_by_role('button', name='Next readings', exact=True).click()
    expect(page.locator('.state-samples tbody tr')).to_have_count(5)
    expect(page.locator('.state-samples tbody tr').first).to_contain_text('Sequence 41')
    page.get_by_role('button', name='Previous readings', exact=True).click()
    expect(page.locator('.state-samples tbody tr').first).to_contain_text('Sequence 21')


def test_old_trip_link_never_shows_new_trip_states(page, web):
    api=web[1]
    first,_=create_state_trip(api,name='Old closed shipment')
    api.post('/api/v1/trips/'+first+'/finish',json={'status':'completed'})
    second,_=create_state_trip(api,name='New opened shipment',seal='likely_opened',samples=2)
    page.goto(web[0]+'/?trip='+first)
    expect(page.locator('.trip-title')).to_have_text('Old closed shipment')
    expect(page.locator('.state-history')).to_contain_text('Likely closed')
    expect(page.locator('.state-history')).not_to_contain_text('Likely opened')
    page.get_by_label('Select trip',exact=True).select_option(second)
    expect(page.locator('.trip-title')).to_have_text('New opened shipment')
    expect(page.locator('.state-history')).to_contain_text('Likely opened')
    page.goto(web[0]+'/?trip=missing-trip')
    expect(page.locator('#error-banner')).to_contain_text('Trip not found')
    expect(page.locator('.trip-title')).to_have_count(0)


def test_device_card_wifi_settings_use_that_device(page, web):
    api=web[1]
    for device in ['device-a','device-b']:
        assert api.post('/api/v1/heartbeat',json={'device_id':device}).status_code==200
    assert api.put('/api/v1/wifi',json={'device_id':'device-a','profiles':[{'ssid':'Network A','password':'password-a','priority':0}]}).status_code==200
    load_dashboard(page,web)
    page.get_by_role('button',name='Device',exact=True).click()
    card=page.locator('.device-panel').filter(has_text='device-b')
    card.get_by_role('button',name='Wi-Fi networks',exact=True).click()
    expect(page.locator('#wifi-form input[name="device_id"]')).to_have_value('device-b')
    expect(page.locator('#wifi-list .wifi-network-row')).to_have_count(0)
    page.get_by_role('button',name='Add network',exact=True).click()
    row=page.locator('.wifi-network-row')
    row.get_by_label('Network name (SSID)').fill('Network B')
    row.get_by_label('Password',exact=True).fill('password-b')
    row.get_by_label('Priority',exact=True).fill('3')
    page.get_by_role('button',name='Save Wi-Fi settings',exact=True).click()
    expect(page.locator('#wifi-status')).to_contain_text('revision 1 saved')
    assert api.get('/api/v1/wifi?device_id=device-a').json()['profiles'][0]['ssid']=='Network A'
    assert api.get('/api/v1/wifi?device_id=device-b').json()['profiles'][0]['ssid']=='Network B'


def test_identical_gap_states_link_in_compact_unified_timeline(page, web):
    api=web[1]
    trip=api.post('/api/v1/trips',json={'name':'Linked gap','device_id':'cargo-esp32-01','origin':'A','destination':'B'}).json()['id']
    state_data={'seal_state':'likely_opened','condition_state':'orientation_uncalibrated','movement_state':'still','sample_interval_ms':1000,'temperature_c':29,'dht_ok':True}
    body={'device_id':'cargo-esp32-01','boot_id':'gap-boot','records':[
        {'seq':1,'uptime_ms':1000,'kind':'telemetry','trip_id':trip,'data':state_data},
        {'seq':2,'uptime_ms':6000,'kind':'telemetry','trip_id':trip,'data':state_data},
        {'seq':3,'uptime_ms':6500,'kind':'event','trip_id':trip,'data':{'type':'impact','detail':'Drop detected','value':4}}]}
    assert api.post('/api/v1/batches',json=body).status_code==200
    page.goto(web[0]+'/?trip='+trip)
    expect(page.locator('.state-run')).to_have_count(1)
    expect(page.locator('.state-run')).to_contain_text('2 readings')
    expect(page.locator('.state-run')).to_contain_text('1 data gap')
    expect(page.locator('.state-history')).to_have_count(1)
    event=page.locator('.trip-timeline-event').filter(has_text='Drop detected')
    expect(event).to_contain_text('Likely opened')
    expect(event).to_contain_text('State at/before event')
    run=page.locator('.state-run')
    expect(run.get_by_role('heading',name='Likely opened',exact=True)).to_be_visible()
    expect(run.locator('.state-diagnostic')).to_be_hidden()
    expect(run.locator('.state-run-meta')).to_be_hidden()
    assert run.locator('.state-headline').evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 18
    run.locator('.state-technical summary').click()
    expect(run.locator('.state-diagnostic')).to_be_visible()
    expect(run.locator('.state-run-meta')).to_be_visible()
    page.wait_for_timeout(2500)
    expect(run.locator('.state-run-meta')).to_be_visible()
    page.get_by_role('button',name='Show 2 logs',exact=True).click()
    expect(page.locator('.state-samples tbody tr')).to_have_count(2)
    assert page.locator('.state-samples').bounding_box()['height']<170
    expect(page.get_by_role('button',name='Previous readings',exact=True)).to_have_count(0)


def test_show_all_states_then_return_to_mixed_timeline(page, web):
    api=web[1]
    trip,initial=create_state_trip(api,samples=1)
    body={'device_id':'cargo-esp32-01','boot_id':initial['boot_id'],'records':[
        {'seq':i+1,'uptime_ms':i*5000,'kind':'telemetry','trip_id':trip,'data':{
            'seal_state':'likely_opened' if i%2 else 'likely_closed','condition_state':'normal','movement_state':'still'}} for i in range(1,26)]}
    assert api.post('/api/v1/batches',json=body).status_code==200
    page.goto(web[0]+'/?trip='+trip)
    expect(page.locator('#sync-status')).to_contain_text('Updated')
    assert page.locator('.state-run').count()<=20
    page.get_by_role('button',name='Show all states',exact=True).click()
    expect(page.locator('.state-run')).to_have_count(26)
    expect(page.locator('.trip-timeline-event')).to_have_count(0)
    page.get_by_role('button',name='Show events too',exact=True).click()
    expect(page.locator('.state-history>article')).to_have_count(20)


@pytest.mark.parametrize('condition,label,tone',[
    ('recent_impact','Recent impact','danger'),
    ('strong_vibration','Strong vibration','danger'),
    ('tilted','Tilted','caution'),
    ('unsafe_environment','Unsafe environment','danger'),
])
def test_cargo_hazard_is_the_timeline_headline(page,web,condition,label,tone):
    trip,body=create_state_trip(web[1],samples=1)
    record=body['records'][0]
    record['seq']=2
    record['uptime_ms']=5000
    record['data']['condition_state']=condition
    assert web[1].post('/api/v1/batches',json=body).status_code==200
    page.goto(web[0]+'/?trip='+trip)
    run=page.locator('.state-run').filter(has=page.get_by_role('heading',name=label,exact=True))
    expect(run.get_by_role('heading',name=label,exact=True)).to_be_visible()
    expect(run).to_have_class('state-run state-tone-'+tone)
    expect(run.locator('.state-support')).not_to_contain_text('Still')
    expect(run.locator('.state-run-meta')).to_be_hidden()


def test_matching_cargo_states_merge_with_movement_inside_original_logs(page,web):
    trip,body=create_state_trip(web[1],samples=1,seal='likely_opened')
    body['records']=[{'seq':i+2,'uptime_ms':(i+1)*5000,'kind':'telemetry','trip_id':trip,
                     'data':{'seal_state':'likely_opened','condition_state':'orientation_uncalibrated',
                             'movement_state':movement}}
                    for i,movement in enumerate(['moving','still','moving'])]
    assert web[1].post('/api/v1/batches',json=body).status_code==200
    page.goto(web[0]+'/?trip='+trip)
    run=page.locator('.state-run')
    expect(run).to_have_count(1)
    expect(run.get_by_role('heading',name='Likely opened',exact=True)).to_be_visible()
    expect(run.locator('.state-support')).to_have_text('4 readings')
    expect(run.locator('.state-support')).not_to_contain_text('Moving')
    run.get_by_role('button',name='Show 4 logs',exact=True).click()
    expect(run.locator('tbody tr')).to_have_count(4)
    expect(run.locator('tbody tr').nth(0)).to_contain_text('Still')
    expect(run.locator('tbody tr').nth(1)).to_contain_text('Moving')
    expect(run.locator('tbody tr').nth(2)).to_contain_text('Still')
    expect(run.locator('tbody tr').nth(3)).to_contain_text('Moving')


def test_server_write_pushes_dashboard_without_fixed_refresh_timer(page,web):
    load_dashboard(page,web)
    expect(page.locator('#sync-status')).to_contain_text('live updates')
    response=web[1].post('/api/v1/trips',json={'name':'Pushed shipment','device_id':'push-board','origin':'A','destination':'B'})
    assert response.status_code==201
    expect(page.get_by_role('button',name='Pushed shipment',exact=True).first).to_be_visible(timeout=3000)



def test_wifi_list_marks_connected_network_and_reports_unlisted_default(page,web):
    api=web[1]
    device='wifi-display-board'
    api.post('/api/v1/heartbeat',json={'device_id':device})
    api.put('/api/v1/wifi',json={'device_id':device,'profiles':[{'ssid':'Test network','password':'test-password','priority':1}]})
    body={'device_id':device,'boot_id':'wifi-display','records':[{'seq':1,'uptime_ms':1000,'kind':'telemetry','data':{'wifi_ssid':'Test network','wifi_connected':True}}]}
    assert api.post('/api/v1/batches',json=body).status_code==200
    load_dashboard(page,web)
    page.get_by_role('button',name='Device',exact=True).click()
    page.locator('.device-panel').filter(has_text=device).get_by_role('button',name='Wi-Fi networks').click()
    expect(page.locator('#wifi-current')).to_have_text('Connected to Test network')
    expect(page.locator('.wifi-connection-badge')).to_have_text('Connected')
    body['records'][0]['seq']=2
    body['records'][0]['data']['wifi_ssid']='Firmware fallback'
    assert api.post('/api/v1/batches',json=body).status_code==200
    expect(page.locator('#wifi-current')).to_have_text('Connected to Firmware fallback')
    expect(page.locator('.wifi-connection-badge')).to_have_count(0)
    expect(page.locator('#wifi-list input[data-field="ssid"]')).to_have_value('Test network')


def test_wifi_current_name_appears_from_heartbeat_before_first_sensor_log(page,web):
    api=web[1]
    assert api.post('/api/v1/heartbeat',json={'device_id':'heartbeat-wifi','wifi_ssid':'Default hotspot'}).status_code==200
    load_dashboard(page,web)
    page.get_by_role('button',name='Device',exact=True).click()
    page.locator('.device-panel').filter(has_text='heartbeat-wifi').get_by_role('button',name='Wi-Fi networks').click()
    expect(page.locator('#wifi-current')).to_have_text('Connected to Default hotspot')
    expect(page.locator('#wifi-list .wifi-network-row')).to_have_count(0)


def test_guided_analysis_trial_records_observation_and_original_logs(page,web):
    page.goto(web[0]+'/analysis')
    expect(page.get_by_role('heading',name='Set up a pilot')).to_be_visible()
    page.get_by_label('Session name',exact=True).fill('Browser guided pilot')
    page.get_by_label('Device ID',exact=True).fill('analysis-board')
    # Keep a single scenario so the browser trial is deterministic.
    for checkbox in page.locator('[name="scenario"]').all():
        checkbox.uncheck()
    page.locator('[name="scenario"][value="open_bright"]').check()
    page.get_by_label('Setup and settings',exact=True).fill('Empty box, fixed sensors, known test build')
    page.get_by_label('Expected success / failure criteria, decided before testing',exact=True).fill('Open and lit should be likely opened; retain misses')
    page.get_by_label('Independent reference / evidence method',exact=True).fill('Independent video of lid and light')
    page.get_by_role('button',name='Create saved protocol',exact=True).click()
    expect(page.locator('#session-name')).to_have_text('Browser guided pilot')
    expect(page.locator('#trial-name')).to_have_text('Open with light')
    page.locator('#ready').check()
    page.get_by_role('button',name='Start recording',exact=True).click()
    expect(page.locator('#recording')).to_be_visible()
    page.get_by_role('button',name='Mark action',exact=True).click()
    expect(page.locator('#markers')).to_contain_text('action')
    body={'device_id':'analysis-board','boot_id':'analysis-boot','records':[
        {'seq':1,'uptime_ms':1000,'kind':'telemetry','data':{'seal_state':'likely_opened','movement_state':'moving'}}]}
    assert web[1].post('/api/v1/batches',json=body).status_code==200
    expect(page.locator('#record-count')).to_have_text('1')
    expect(page.locator('#live-evidence')).to_be_hidden()
    page.get_by_label('What physically happened?',exact=True).fill('Physical lid opened in room light; video recorded')
    page.get_by_label('Operator assessment',exact=True).select_option('observed')
    page.get_by_role('button',name='Finish & preserve evidence',exact=True).click()
    expect(page.get_by_role('heading',name='All trials recorded')).to_be_visible()
    expect(page.locator('#review-list')).to_contain_text('observed')
    page.locator('#review-list button').click()
    expect(page.locator('#review-records')).to_contain_text('likely_opened')
    page.locator('#review-records').get_by_role('button',name='Raw',exact=True).click()
    expect(page.locator('#raw-record')).to_contain_text('analysis-boot')
    with page.expect_download() as info:
        page.get_by_role('button',name='Download complete evidence JSON').click()
    import json
    evidence=json.loads(Path(info.value.path()).read_text())
    assert evidence['trials'][0]['records'][0]['data']['movement_state']=='moving'
    assert evidence['trials'][0]['result']['observed'].startswith('Physical lid')
    page.reload()
    expect(page.get_by_role('heading',name='All trials recorded')).to_be_visible()
