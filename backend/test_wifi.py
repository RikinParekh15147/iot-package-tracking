"""Wi-Fi configuration tests use isolated SQLite databases, never live secrets."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.test_app import AUTH, server


def profile(ssid="Lab WiFi", password="test-passphrase", priority=10):
    return {"ssid": ssid, "password": password, "priority": priority}


def save(client, profiles, device="cargo-01", headers=AUTH):
    return client.put("/api/v1/wifi", headers=headers, json={"device_id": device, "profiles": profiles})


def read(client, device="cargo-01", headers=AUTH):
    return client.get("/api/v1/wifi", headers=headers, params={"device_id": device})


def heartbeat(client, revision=0, device="cargo-01", headers=AUTH):
    return client.post("/api/v1/heartbeat", headers=headers,
                       json={"device_id": device, "wifi_revision": revision})


@pytest.mark.parametrize("headers", [{}, {"X-Device-Token": "wrong"}])
def test_wifi_routes_require_authentication(server, headers):
    client, _ = server
    assert read(client, headers=headers).status_code == 401
    assert save(client, [profile()], headers=headers).status_code == 401
    assert read(client).json()["revision"] == 0


def test_wifi_staged_offline_redacted_and_delivered_until_applied(server):
    client, _ = server
    assert read(client).json() == {"device_id": "cargo-01", "revision": 0,
                                   "applied_revision": None, "profiles": [],
                                   "network": {"ssid": None, "connected": False, "reported_at": None}}
    saved = save(client, [profile("Backup", priority=50), profile("Preferred", priority=1)])
    assert saved.status_code == 200
    assert "test-passphrase" not in saved.text
    config = read(client).json()
    assert config["revision"] == 1 and config["applied_revision"] is None
    assert config["profiles"] == [
        {"ssid": "Preferred", "priority": 1, "has_password": True},
        {"ssid": "Backup", "priority": 50, "has_password": True},
    ]
    for _ in range(2):
        response = heartbeat(client).json()
        assert response["wifi_config"]["revision"] == 1
        assert response["wifi_config"]["profiles"][0] == profile("Preferred", priority=1)
    assert read(client).json()["applied_revision"] == 0
    assert heartbeat(client, revision=1).json()["wifi_config"] is None
    assert read(client).json()["applied_revision"] == 1


def test_password_preservation_replacement_removal_and_clear(server):
    client, _ = server
    save(client, [profile("First"), profile("Second", "other-password")])
    saved = save(client, [{"ssid": "First", "priority": 0}, profile("Second", "replacement-password", 1)])
    assert saved.json()["revision"] == 2
    delivered = heartbeat(client).json()["wifi_config"]
    assert delivered["profiles"][0]["password"] == "test-passphrase"
    assert delivered["profiles"][1]["password"] == "replacement-password"
    # Explicit null also preserves; omitted profiles are removed.
    assert save(client, [profile("First", None)]).json()["revision"] == 3
    assert len(heartbeat(client).json()["wifi_config"]["profiles"]) == 1
    assert save(client, []).json()["revision"] == 4
    assert heartbeat(client, revision=3).json()["wifi_config"] == {"revision": 4, "profiles": []}
    assert heartbeat(client, revision=4).json()["wifi_config"] is None
    assert read(client).json()["profiles"] == []


def test_missing_password_for_new_ssid_cannot_partially_save(server):
    client, _ = server
    save(client, [profile("Original")])
    response = save(client, [profile("Original", "changed-password"), {"ssid": "New", "priority": 1}])
    assert response.status_code == 422
    assert read(client).json()["revision"] == 1
    assert heartbeat(client).json()["wifi_config"]["profiles"][0]["password"] == "test-passphrase"


@pytest.mark.parametrize("profiles", [
    [profile("duplicate"), profile("duplicate")],
    [profile(str(i)) for i in range(9)],
    [profile("x" * 33)],
    [profile("é" * 17)],
    [profile("")],
    [profile("bad\x00ssid")],
    [profile(password="short")],
    [profile(password="p" * 64)],
    [profile(password="password\n")],
    [profile(password="é" * 8)],
    [profile(priority=True)],
    [profile(priority="1")],
    [profile(priority=-1)],
    [profile(priority=1000)],
    [profile(priority=1.5)],
])
def test_wifi_validation_rejects_invalid_settings_without_write(server, profiles):
    client, _ = server
    assert save(client, profiles).status_code == 422
    assert read(client).json()["revision"] == 0


def test_wifi_boundaries_and_device_isolation(server):
    client, _ = server
    profiles = [profile("é" * 16, "p" * 63, 999), profile("A", "p" * 8, 0)]
    assert save(client, profiles, device="  cargo-01  ").status_code == 200
    assert read(client).json()["revision"] == 1
    assert read(client, "cargo-02").json()["profiles"] == []
    assert heartbeat(client, device="cargo-02").json()["wifi_config"] is None
    assert save(client, [profile(password=None)], device="cargo-02").status_code == 422
    # A different device cannot mark this device's configuration as applied.
    heartbeat(client, revision=1, device="cargo-02")
    assert read(client).json()["applied_revision"] is None
    assert read(client, "cargo-02").json()["applied_revision"] == 1


def test_unauthenticated_heartbeat_cannot_mark_config_applied(server):
    client, _ = server
    save(client, [profile()])
    assert heartbeat(client, revision=1, headers={"X-Device-Token": "wrong"}).status_code == 401
    assert read(client).json()["applied_revision"] is None


def test_wifi_config_and_applied_state_persist_after_restart(tmp_path):
    path = tmp_path / "wifi.db"
    with TestClient(create_app(path, "test-secret")) as client:
        save(client, [profile()])
        heartbeat(client, revision=1)
    with TestClient(create_app(path, "test-secret")) as client:
        assert read(client).json()["revision"] == read(client).json()["applied_revision"] == 1
        assert heartbeat(client).json()["wifi_config"]["profiles"][0]["password"] == "test-passphrase"


def test_concurrent_wifi_saves_increment_revisions_without_lost_write(server):
    client, _ = server
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda i: save(client, [profile(str(i))]), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["revision"] for response in responses) == [1, 2]
    assert read(client).json()["revision"] == 2


def test_database_failure_rolls_back_wifi_save_and_heartbeat_application(server):
    client, path = server
    save(client, [profile()])
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER fail_wifi_update BEFORE UPDATE ON wifi_configs
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    assert save(client, [profile("New")]).status_code == 503
    assert read(client).json()["revision"] == 1
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER fail_application BEFORE INSERT ON wifi_applied
            BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    assert heartbeat(client, revision=1).status_code == 503
    assert read(client).json()["applied_revision"] is None
    assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is False


@pytest.mark.parametrize("revision", [-1, True, "1", 1.5, 2**63])
def test_heartbeat_revision_is_strict_sqlite_safe_integer(server, revision):
    client, _ = server
    assert heartbeat(client, revision=revision).status_code == 422
    assert read(client).json()["applied_revision"] is None


def test_wifi_password_never_enters_trip_logs_or_get_responses(server):
    client, _ = server
    trip = client.post("/api/v1/trips", headers=AUTH,
                       json={"name": "Test", "device_id": "cargo-01", "origin": "A", "destination": "B"}).json()["id"]
    secret = "secret-for-config-only"
    save(client, [profile(password=secret)])
    assert heartbeat(client).json()["wifi_config"]["profiles"][0]["password"] == secret
    for route in [f"/api/v1/trips/{trip}", f"/api/v1/trips/{trip}/export", "/api/v1/trips",
                  "/api/v1/devices", "/api/v1/records", "/api/v1/wifi?device_id=cargo-01"]:
        assert secret not in client.get(route, headers=AUTH).text



def test_reported_wifi_is_device_specific_and_stale_data_is_not_connected(server):
    client,path=server
    heartbeat(client)
    save(client,[profile('Lab WiFi')])
    payload={'device_id':'cargo-01','boot_id':'wifi-boot','records':[
        {'seq':1,'uptime_ms':1000,'kind':'telemetry','data':{'wifi_ssid':'Lab WiFi','wifi_connected':True}}]}
    assert client.post('/api/v1/batches',headers=AUTH,json=payload).status_code==200
    network=read(client).json()['network']
    assert network['ssid']=='Lab WiFi' and network['connected'] is True
    assert read(client,device='other-device').json()['network']['ssid'] is None
    with sqlite3.connect(path) as db:
        db.execute("UPDATE records SET received_at='2020-01-01T00:00:00+00:00'")
    stale=read(client).json()['network']
    assert stale['ssid']=='Lab WiFi' and stale['connected'] is False



def test_heartbeat_reports_current_wifi_without_telemetry_and_overrides_old_logs(server):
    client,_=server
    assert client.post('/api/v1/heartbeat',headers=AUTH,json={'device_id':'cargo-01','wifi_ssid':'Current hotspot'}).status_code==200
    network=read(client).json()['network']
    assert network['ssid']=='Current hotspot' and network['connected'] is True
    assert client.post('/api/v1/heartbeat',headers=AUTH,json={'device_id':'other','wifi_ssid':'Other hotspot'}).status_code==200
    assert read(client).json()['network']['ssid']=='Current hotspot'
    assert client.post('/api/v1/heartbeat',headers=AUTH,json={'device_id':'cargo-01','wifi_ssid':'New hotspot'}).status_code==200
    assert read(client).json()['network']['ssid']=='New hotspot'
