import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "cargo.db"
    with TestClient(create_app(path, "test-secret")) as client:
        yield client, path


def batch(boot="boot-1"):
    return {"device_id": "cargo-01", "boot_id": boot, "records": [
        {"seq": 1, "uptime_ms": 100, "kind": "telemetry", "data": {"temperature_c": None}},
        {"seq": 2, "uptime_ms": 200, "kind": "lid_open", "data": {"armed": True}},
    ]}


AUTH = {"X-Device-Token": "test-secret"}


def test_live_connection_requires_authenticated_heartbeat(server, monkeypatch):
    client, _ = server
    clock = [100.0]
    monkeypatch.setattr("backend.app.time.monotonic", lambda: clock[0])
    url = "/api/v1/connection?device_id=cargo-01"
    assert client.get(url).status_code == 401
    assert client.post("/api/v1/heartbeat", json={"device_id": "cargo-01"}).status_code == 401
    assert client.get(url, headers=AUTH).json()["last_seen"] is None
    client.post("/api/v1/batches", json=batch(), headers=AUTH)
    assert client.get(url, headers=AUTH).json()["connected"] is False
    assert client.post("/api/v1/heartbeat", json={"device_id": "cargo-01"}, headers=AUTH).json()["accepted"]
    assert client.get(url, headers=AUTH).json()["connected"] is True
    assert client.get("/api/v1/connection?device_id=another-board", headers=AUTH).json()["connected"] is False
    clock[0] += 20
    assert client.get(url, headers=AUTH).json()["connected"] is False
    client.post("/api/v1/heartbeat", json={"device_id": "cargo-01"}, headers=AUTH)
    assert client.get(url, headers=AUTH).json()["connected"] is True


def test_authentication(server):
    client, _ = server
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert client.post("/api/v1/batches", json=batch()).status_code == 401
    assert client.get("/api/v1/records").status_code == 401
    assert client.post("/api/v1/batches", json=batch(), headers={"X-Device-Token": "wrong"}).status_code == 401


def test_retry_dedup_and_boot_identity(server):
    client, _ = server
    first = client.post("/api/v1/batches", json=batch(), headers=AUTH)
    assert first.json() == {"accepted": True, "received": 2, "inserted": 2}
    second = client.post("/api/v1/batches", json=batch(), headers=AUTH)
    assert second.json() == {"accepted": True, "received": 2, "inserted": 0}
    result = client.get("/api/v1/records", headers=AUTH).json()
    assert result["total"] == 2
    assert result["records"][0]["seq"] == 2
    assert result["records"][0]["uptime_ms"] == 200
    assert result["records"][0]["received_at"].endswith("+00:00")
    assert result["records"][1]["data"]["temperature_c"] is None
    assert client.post("/api/v1/batches", json=batch("boot-2"), headers=AUTH).json()["inserted"] == 2
    assert client.get("/api/v1/records?device_id=absent", headers=AUTH).json()["total"] == 0


def test_validation_writes_nothing(server):
    client, _ = server
    invalid = batch()
    invalid["records"][1]["seq"] = -1
    assert client.post("/api/v1/batches", json=invalid, headers=AUTH).status_code == 422
    oversized = batch()
    oversized["records"] = oversized["records"][:1] * 101
    assert client.post("/api/v1/batches", json=oversized, headers=AUTH).status_code == 422
    empty = batch()
    empty["records"] = []
    assert client.post("/api/v1/batches", json=empty, headers=AUTH).status_code == 422
    assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 0


def test_database_failure_rolls_back_entire_batch(server):
    client, path = server
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON records
            WHEN NEW.seq = 2 BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    assert client.post("/api/v1/batches", json=batch(), headers=AUTH).status_code == 503
    assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 0


def test_data_persists_after_restart(tmp_path):
    path = tmp_path / "persistent.db"
    with TestClient(create_app(path, "test-secret")) as client:
        assert client.post("/api/v1/batches", json=batch(), headers=AUTH).status_code == 200
    with TestClient(create_app(path, "test-secret")) as client:
        assert client.get("/api/v1/records", headers=AUTH).json()["total"] == 2
