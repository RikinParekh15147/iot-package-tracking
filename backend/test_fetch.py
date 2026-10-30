"""Live snapshot requests use heartbeat polling and committed upload acknowledgments."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.test_app import AUTH, batch, server
from backend.test_trips import start


def heartbeat(client, device="cargo-01", capable=True):
    return client.post("/api/v1/heartbeat", headers=AUTH,
                       json={"device_id": device, "supports_fetch": capable})


def fetch(client, device="cargo-01"):
    return client.post("/api/v1/fetch", headers=AUTH, json={"device_id": device})


def latest(client, device="cargo-01"):
    return client.get("/api/v1/latest", headers=AUTH, params={"device_id": device}).json()


def snapshot(request_id, device="cargo-01", seq=1):
    return {"device_id": device, "boot_id": "snapshot-boot", "records": [
        {"seq": seq, "uptime_ms": 1000, "kind": "telemetry",
         "data": {"temperature_c": 27.0, "fetch_request_id": request_id}}
    ]}


@pytest.mark.parametrize("headers", [{}, {"X-Device-Token": "wrong"}])
def test_fetch_routes_require_authentication(server, headers):
    client, _ = server
    assert client.post("/api/v1/fetch", headers=headers, json={"device_id": "cargo-01"}).status_code == 401
    assert client.get("/api/v1/latest?device_id=cargo-01", headers=headers).status_code == 401


def test_fetch_requires_connected_capable_device(server, monkeypatch):
    client, _ = server
    clock = [100.0]
    monkeypatch.setattr("backend.app.time.monotonic", lambda: clock[0])
    assert fetch(client).status_code == 409
    assert "offline" in fetch(client).json()["detail"].lower()
    # Old firmware can send a valid heartbeat but cannot satisfy a fetch.
    client.post("/api/v1/heartbeat", headers=AUTH, json={"device_id": "cargo-01"})
    unsupported = fetch(client)
    assert unsupported.status_code == 409
    assert "firmware" in unsupported.json()["detail"].lower()
    assert latest(client)["request"] is None
    heartbeat(client)
    assert fetch(client).status_code == 202
    clock[0] += 20
    assert fetch(client).status_code == 409


@pytest.mark.parametrize("device", ["", "   ", "d" * 129, 123, None])
def test_fetch_validates_device_identity(server, device):
    client, _ = server
    assert fetch(client, device).status_code == 422


def test_fetch_coalesces_repeated_clicks_and_reaches_heartbeat(server):
    client, _ = server
    assert heartbeat(client).json()["telemetry_request_id"] is None
    first = fetch(client).json()
    assert first["device_id"] == "cargo-01" and first["status"] == "pending"
    assert fetch(client).json() == first
    assert fetch(client, "  cargo-01  ").json() == first
    assert heartbeat(client).json()["telemetry_request_id"] == first["request_id"]
    assert latest(client) == {"device_id": "cargo-01", "record": None,
                              "request": {"request_id": first["request_id"], "status": "pending"}}


def test_matching_snapshot_acknowledges_only_its_device_after_commit(server):
    client, _ = server
    heartbeat(client)
    heartbeat(client, "cargo-02")
    request = fetch(client).json()["request_id"]
    other_request = fetch(client, "cargo-02").json()["request_id"]
    # Another device cannot satisfy the cargo-01 request, even knowing its ID.
    client.post("/api/v1/batches", headers=AUTH, json=snapshot(request, "cargo-02"))
    assert latest(client)["request"]["request_id"] == request
    assert latest(client, "cargo-02")["request"]["request_id"] == other_request
    upload = snapshot(request)
    assert client.post("/api/v1/batches", headers=AUTH, json=upload).json()["inserted"] == 1
    result = latest(client)
    assert result["request"] is None
    assert result["record"]["data"]["fetch_request_id"] == request
    assert result["record"]["trip_id"] is None
    assert heartbeat(client).json()["telemetry_request_id"] is None
    # Lost HTTP acknowledgement may cause a retry; it remains idempotent.
    assert client.post("/api/v1/batches", headers=AUTH, json=upload).json()["inserted"] == 0
    next_request = fetch(client).json()["request_id"]
    assert next_request != request
    assert client.post("/api/v1/batches", headers=AUTH, json=upload).status_code == 200
    assert latest(client)["request"]["request_id"] == next_request


def test_conflicting_duplicate_cannot_forge_snapshot_acknowledgment(server):
    client, _ = server
    heartbeat(client)
    # First store a routine record without a live-request ID.
    original = snapshot(None)
    client.post("/api/v1/batches", headers=AUTH, json=original)
    request = fetch(client).json()["request_id"]
    forged_retry = snapshot(request)
    response = client.post("/api/v1/batches", headers=AUTH, json=forged_retry)
    assert response.json()["inserted"] == 0
    assert latest(client)["request"]["request_id"] == request
    assert latest(client)["record"]["data"]["fetch_request_id"] is None
    # The same marker on an event also cannot acknowledge a telemetry request.
    event = snapshot(request, seq=2)
    event["records"][0]["kind"] = "event"
    client.post("/api/v1/batches", headers=AUTH, json=event)
    assert latest(client)["request"]["request_id"] == request


def test_failed_batch_retains_pending_request_and_retries_after_recovery(server):
    client, path = server
    heartbeat(client)
    request = fetch(client).json()["request_id"]
    upload = snapshot(request)
    upload["records"].append({"seq": 2, "uptime_ms": 1001, "kind": "event", "data": {}})
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON records
            WHEN NEW.seq = 2 BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    assert client.post("/api/v1/batches", headers=AUTH, json=upload).status_code == 503
    assert latest(client)["record"] is None
    assert latest(client)["request"]["request_id"] == request
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER fail_second")
    assert client.post("/api/v1/batches", headers=AUTH, json=upload).json()["inserted"] == 2
    assert latest(client)["request"] is None


def test_pending_survives_device_reconnect_but_not_server_restart(tmp_path, monkeypatch):
    path = tmp_path / "requests.db"
    clock = [100.0]
    monkeypatch.setattr("backend.app.time.monotonic", lambda: clock[0])
    with TestClient(create_app(path, "test-secret")) as client:
        heartbeat(client)
        request = fetch(client).json()["request_id"]
        clock[0] += 30
        assert fetch(client).status_code == 409
        assert latest(client)["request"]["request_id"] == request
        assert heartbeat(client).json()["telemetry_request_id"] == request
        assert fetch(client).json()["request_id"] == request
    with TestClient(create_app(path, "test-secret")) as client:
        assert latest(client)["request"] is None
        assert fetch(client).status_code == 409
        assert heartbeat(client).json()["telemetry_request_id"] is None


def test_snapshot_preserves_explicit_original_trip_and_latest_ignores_events(server):
    client, _ = server
    old_trip = start(client).json()["id"]
    client.post(f"/api/v1/trips/{old_trip}/finish", headers=AUTH, json={"status": "completed"})
    new_trip = start(client, name="New shipment").json()["id"]
    heartbeat(client)
    request = fetch(client).json()["request_id"]
    upload = snapshot(request)
    upload["records"][0]["trip_id"] = old_trip
    client.post("/api/v1/batches", headers=AUTH, json=upload)
    client.post("/api/v1/batches", headers=AUTH, json=batch("later-events"))
    record = latest(client)["record"]
    assert record["kind"] == "telemetry"
    assert record["boot_id"] == "later-events"
    assert record["trip_id"] is None
    records = client.get("/api/v1/records", headers=AUTH).json()["records"]
    fetched = next(row for row in records if row["boot_id"] == "snapshot-boot")
    assert fetched["trip_id"] == old_trip
    new_detail = client.get(f"/api/v1/trips/{new_trip}", headers=AUTH).json()
    assert new_detail["telemetry"] == []
