"""Protocol, authorization and outage regressions, all using temporary databases."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.test_app import AUTH, batch, server
from backend.test_trips import start


PROTECTED_REQUESTS = [
    ("GET", "/api/v1/records", None),
    ("GET", "/api/v1/connection", None),
    ("GET", "/api/v1/devices", None),
    ("GET", "/api/v1/trips", None),
    ("GET", "/api/v1/trips/nonexistent", None),
    ("GET", "/api/v1/trips/nonexistent/export", None),
    ("POST", "/api/v1/batches", batch()),
    ("POST", "/api/v1/heartbeat", {"device_id": "cargo-01"}),
    ("POST", "/api/v1/trips", {"name": "Test", "origin": "A", "destination": "B"}),
    ("POST", "/api/v1/trips/nonexistent/logs", {"kind": "note", "message": "Test"}),
    ("POST", "/api/v1/trips/nonexistent/finish", {"status": "completed"}),
]


@pytest.mark.parametrize("method,url,payload", PROTECTED_REQUESTS)
@pytest.mark.parametrize("headers", [{}, {"X-Device-Token": "wrong"}])
def test_every_data_route_requires_token(server, method, url, payload, headers):
    client, _ = server
    response = client.request(method, url, json=payload, headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
def test_nested_nonfinite_numbers_reject_whole_batch(server, number):
    client, _ = server
    payload = batch()
    payload["records"][1]["data"] = {"nested": [{"reading": number}]}
    # Send deliberately invalid JSON number tokens: the transport helper itself
    # refuses them when json= is used. The API must still reject without writing.
    response = client.post("/api/v1/batches", headers={**AUTH, "Content-Type": "application/json"},
                           content=json.dumps(payload, allow_nan=True))
    assert response.status_code == 422
    assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 0


@pytest.mark.parametrize("field", ["seq", "uptime_ms"])
@pytest.mark.parametrize("value", [True, "12", 12.5, -1, 2**63])
def test_record_integer_fields_are_strict_and_sqlite_safe(server, field, value):
    client, _ = server
    payload = batch()
    payload["records"][1][field] = value
    assert client.post("/api/v1/batches", headers=AUTH, json=payload).status_code == 422
    assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 0


def test_batch_and_integer_upper_boundaries_are_accepted(server):
    client, _ = server
    payload = batch()
    payload["device_id"] = "d" * 128
    payload["boot_id"] = "b" * 128
    payload["records"] = [{"seq": 2**63 - 100 + i, "uptime_ms": 2**63 - 1,
                            "kind": "t" * 64, "data": {}} for i in range(100)]
    response = client.post("/api/v1/batches", headers=AUTH, json=payload)
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "received": 100, "inserted": 100}
    assert len(client.get("/api/v1/records?limit=500", headers=AUTH).json()["records"]) == 100


@pytest.mark.parametrize("change", ["empty_device", "long_device", "empty_boot", "long_boot",
                                   "empty_kind", "long_kind", "batch_extra", "record_extra"])
def test_protocol_string_boundaries_and_unknown_fields(server, change):
    client, _ = server
    payload = batch()
    if change == "empty_device": payload["device_id"] = ""
    if change == "long_device": payload["device_id"] = "d" * 129
    if change == "empty_boot": payload["boot_id"] = ""
    if change == "long_boot": payload["boot_id"] = "b" * 129
    if change == "empty_kind": payload["records"][0]["kind"] = ""
    if change == "long_kind": payload["records"][0]["kind"] = "k" * 65
    if change == "batch_extra": payload["unknown"] = "unexpected"
    if change == "record_extra": payload["records"][0]["unknown"] = "unexpected"
    assert client.post("/api/v1/batches", headers=AUTH, json=payload).status_code == 422
    assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 0


def test_conflicting_retry_preserves_original_record_and_receipt(server):
    client, _ = server
    payload = batch()
    client.post("/api/v1/batches", headers=AUTH, json=payload)
    original = client.get("/api/v1/records", headers=AUTH).json()["records"]
    payload["records"][0].update(uptime_ms=99999, kind="event", data={"type": "impact"})
    payload["records"].append({"seq": 3, "uptime_ms": 300, "kind": "event", "data": {}})
    assert client.post("/api/v1/batches", headers=AUTH, json=payload).json() == {
        "accepted": True, "received": 3, "inserted": 1}
    stored = client.get("/api/v1/records", headers=AUTH).json()
    assert stored["total"] == 3
    assert stored["records"][1:] == original


def test_same_boot_and_sequences_belong_to_separate_devices(server):
    client, _ = server
    first = batch()
    other = batch()
    other["device_id"] = "cargo-02"
    other["records"][0]["data"] = {"temperature_c": 40}
    assert client.post("/api/v1/batches", headers=AUTH, json=first).json()["inserted"] == 2
    assert client.post("/api/v1/batches", headers=AUTH, json=other).json()["inserted"] == 2
    first_rows = client.get("/api/v1/records?device_id=cargo-01", headers=AUTH).json()
    other_rows = client.get("/api/v1/records?device_id=cargo-02", headers=AUTH).json()
    assert first_rows["total"] == other_rows["total"] == 2
    assert all(row["device_id"] == "cargo-01" for row in first_rows["records"])
    assert all(row["device_id"] == "cargo-02" for row in other_rows["records"])
    assert other_rows["records"][1]["data"]["temperature_c"] == 40


def test_connection_timeout_exact_boundary_and_recovery(server, monkeypatch):
    client, _ = server
    clock = [100.0]
    monkeypatch.setattr("backend.app.time.monotonic", lambda: clock[0])
    payload = {"device_id": "cargo-01", "firmware": "test", "ip": "192.0.2.1", "rssi": -70, "uptime_ms": 1234}
    client.post("/api/v1/heartbeat", headers=AUTH, json=payload)
    clock[0] = 119.999
    assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is True
    clock[0] = 120.0
    status = client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()
    assert status["connected"] is False and status["age_seconds"] == 20
    # Bad credentials must not refresh device presence.
    assert client.post("/api/v1/heartbeat", headers={"X-Device-Token": "wrong"}, json=payload).status_code == 401
    assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is False
    client.post("/api/v1/heartbeat", headers=AUTH, json={"device_id": "cargo-01"})
    device = client.get("/api/v1/devices", headers=AUTH).json()["devices"][0]
    assert device["connected"] is True
    assert device["firmware"] == "test" and device["rssi"] == -70


def test_restart_requires_fresh_heartbeat_without_losing_metadata(tmp_path):
    path = tmp_path / "presence.db"
    with TestClient(create_app(path, "test-secret")) as client:
        client.post("/api/v1/heartbeat", headers=AUTH, json={"device_id": "cargo-01", "firmware": "v1", "rssi": -60})
        assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is True
    with TestClient(create_app(path, "test-secret")) as client:
        device = client.get("/api/v1/devices", headers=AUTH).json()["devices"][0]
        assert device["connected"] is False
        assert device["last_seen"] is None
        assert device["last_received_at"] is not None and device["firmware"] == "v1"
        assert client.post("/api/v1/heartbeat", headers=AUTH, json={"device_id": "cargo-01"}).status_code == 200
        assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is True


@pytest.mark.parametrize("uptime", [-1, 2**63, True, "1234", 12.5])
def test_heartbeat_uptime_rejects_values_outside_sqlite_range(server, uptime):
    client, _ = server
    response = client.post("/api/v1/heartbeat", headers=AUTH, json={"device_id": "cargo-01", "uptime_ms": uptime})
    assert response.status_code == 422
    assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is False


@pytest.mark.parametrize("method,url,payload", [
    ("GET", "/api/v1/records", None),
    ("POST", "/api/v1/batches", batch()),
    ("POST", "/api/v1/heartbeat", {"device_id": "cargo-01"}),
    ("GET", "/api/v1/trips", None),
    ("POST", "/api/v1/trips", {"name": "Test", "origin": "A", "destination": "B"}),
    ("GET", "/api/v1/devices", None),
    ("GET", "/api/v1/trips/nonexistent", None),
    ("GET", "/api/v1/trips/nonexistent/export", None),
    ("POST", "/api/v1/trips/nonexistent/logs", {"kind": "note", "message": "Test"}),
    ("POST", "/api/v1/trips/nonexistent/finish", {"status": "completed"}),
])
def test_storage_outage_returns_retryable_error(server, monkeypatch, method, url, payload):
    client, _ = server

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("Simulated unavailable storage")

    monkeypatch.setattr("backend.app.sqlite3.connect", unavailable)
    response = client.request(method, url, json=payload, headers=AUTH)
    assert response.status_code == 503
    # Presence and basic process health remain available without storage.
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/connection", headers=AUTH).json()["connected"] is False


def test_invalid_mixed_trip_batch_cannot_partially_commit(server):
    client, _ = server
    trip = start(client).json()["id"]
    payload = batch()
    payload["records"][0]["trip_id"] = trip
    payload["records"][1]["trip_id"] = "nonexistent"
    assert client.post("/api/v1/batches", headers=AUTH, json=payload).status_code == 422
    assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 0
    assert client.get(f"/api/v1/trips/{trip}", headers=AUTH).json()["activity_count"] == 1
