"""Raw application console protocol, retention and history isolation tests."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.test_app import AUTH, server
from backend.test_trips import start


def payload(device="cargo-01", boot="boot-1"):
    return {"device_id": device, "boot_id": boot, "dropped_lines": 0, "lines": [
        {"seq": 1, "uptime_ms": 100, "message": "MPU6050 ready"},
        {"seq": 2, "uptime_ms": 200, "message": "  [RAW] ax=-0.01\taz=1.15  "},
    ]}


def post(client, message=None, headers=AUTH):
    return client.post("/api/v1/console", headers=headers, json=message or payload())


def read(client, device="cargo-01", **parameters):
    return client.get("/api/v1/console", headers=AUTH, params={"device_id": device, **parameters})


@pytest.mark.parametrize("headers", [{}, {"X-Device-Token": "wrong"}])
def test_console_authentication(server, headers):
    client, _ = server
    assert post(client, headers=headers).status_code == 401
    assert client.get("/api/v1/console", headers=headers).status_code == 401
    assert read(client).json()["total"] == 0


def test_console_stores_exact_raw_message_and_immutable_retry(server):
    client, _ = server
    message = payload()
    assert post(client, message).json() == {"accepted": True, "received": 2, "inserted": 2}
    result = read(client).json()
    assert result["total"] == 2
    assert result["lines"][0]["message"] == message["lines"][1]["message"]
    assert result["lines"][0]["received_at"].endswith("+00:00")
    assert result["lines"][0]["trip_id"] is None
    message["lines"][1].update(message="Conflicting replacement", uptime_ms=999)
    assert post(client, message).json() == {"accepted": True, "received": 2, "inserted": 0}
    assert read(client).json()["lines"] == result["lines"]
    # Console traffic alone does not claim a live device connection.
    assert client.get("/api/v1/connection?device_id=cargo-01", headers=AUTH).json()["connected"] is False


def test_console_boot_device_and_trip_scoping(server):
    client, _ = server
    old = start(client).json()["id"]
    client.post(f"/api/v1/trips/{old}/finish", headers=AUTH, json={"status": "completed"})
    active = start(client, name="New cargo").json()["id"]
    old_lines = payload(boot="old-boot")
    old_lines["lines"][0]["trip_id"] = old
    assert post(client, old_lines).status_code == 200
    new_lines = payload(boot="new-boot")
    for line in new_lines["lines"]:
        line["trip_id"] = active
    assert post(client, new_lines).json()["inserted"] == 2
    assert post(client, payload(device="cargo-02")).json()["inserted"] == 2
    scoped = read(client, trip_id=old, boot_id="old-boot").json()
    assert scoped["total"] == 1
    assert scoped["lines"][0]["trip_id"] == old
    assert read(client, trip_id=active).json()["total"] == 2
    assert read(client, trip_id=old, boot_id="new-boot").json()["total"] == 0
    assert read(client, device="cargo-02", trip_id=old).json()["total"] == 0
    assert read(client).json()["total"] == 4
    # Retries cannot move historical lines to the currently active trip.
    old_lines["lines"][0]["trip_id"] = active
    assert post(client, old_lines).json()["inserted"] == 0
    assert read(client, trip_id=old).json()["total"] == 1
    assert read(client, trip_id=active).json()["total"] == 2


@pytest.mark.parametrize("bad_trip", ["missing", "different-device"])
def test_console_rejects_invalid_trip_owner_without_partial_write(server, bad_trip):
    client, _ = server
    if bad_trip == "different-device":
        bad_trip = start(client, device_id="cargo-02").json()["id"]
    message = payload()
    message["lines"][1]["trip_id"] = bad_trip
    message["dropped_lines"] = 7
    assert post(client, message).status_code == 422
    result = read(client).json()
    assert result["total"] == 0 and result["dropped_lines"] == 0


@pytest.mark.parametrize("field,value", [
    ("seq", 0), ("seq", -1), ("seq", True), ("seq", "1"), ("seq", 2**63),
    ("uptime_ms", -1), ("uptime_ms", True), ("uptime_ms", 1.5), ("uptime_ms", 2**63),
    ("message", "x" * 1025), ("message", 123), ("trip_id", ""), ("trip_id", "x" * 65),
])
def test_console_line_validation_rejects_entire_batch(server, field, value):
    client, _ = server
    message = payload()
    message["lines"][1][field] = value
    assert post(client, message).status_code == 422
    assert read(client).json()["total"] == 0


@pytest.mark.parametrize("field,value", [
    ("device_id", ""), ("device_id", "d" * 129), ("boot_id", ""), ("boot_id", "b" * 129),
    ("dropped_lines", -1), ("dropped_lines", True), ("dropped_lines", "3"), ("dropped_lines", 2**63),
    ("lines", []), ("lines", [{"seq": 1, "uptime_ms": 0, "message": "a"}] * 33),
])
def test_console_batch_validation(server, field, value):
    client, _ = server
    message = payload()
    message[field] = value
    assert post(client, message).status_code == 422


def test_console_boundaries_and_drop_report(server):
    client, _ = server
    message = payload(device="d" * 128, boot="b" * 128)
    message["dropped_lines"] = 2**63 - 1
    message["lines"] = [{"seq": 2**63 - 32 + i, "uptime_ms": 2**63 - 1,
                         "message": "x" * 1024} for i in range(32)]
    assert post(client, message).json()["inserted"] == 32
    result = read(client, device="d" * 128, limit=1).json()
    assert result["total"] == 32 and len(result["lines"]) == 1
    assert result["dropped_lines"] == 2**63 - 1
    assert result["dropped_boot_id"] == "b" * 128


def test_drop_report_is_cumulative_per_boot_and_resets_on_new_boot(server):
    client, _ = server
    message = payload()
    message["dropped_lines"] = 9
    post(client, message)
    message["dropped_lines"] = 3
    post(client, message)
    assert read(client).json()["dropped_lines"] == 9
    post(client, payload(boot="boot-2"))
    result = read(client).json()
    assert result["dropped_lines"] == 0 and result["dropped_boot_id"] == "boot-2"


def test_console_write_failure_rolls_back_lines_drop_status_and_retention(server):
    client, path = server
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER fail_console_second BEFORE INSERT ON raw_console
            WHEN NEW.seq=2 BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END""")
    message = payload()
    message["dropped_lines"] = 5
    assert post(client, message).status_code == 503
    result = read(client).json()
    assert result["total"] == 0 and result["dropped_lines"] == 0
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER fail_console_second")
    assert post(client, message).json()["inserted"] == 2
    assert read(client).json()["dropped_lines"] == 5


def test_console_retains_latest_5000_rows_per_device_transactionally(server):
    client, path = server
    with sqlite3.connect(path) as db:
        db.executemany("""INSERT INTO raw_console
            (device_id,boot_id,seq,uptime_ms,message,received_at) VALUES (?,?,?,?,?,?)""",
            [("cargo-01", "seed", seq, seq * 10, str(seq), "2026-01-01T00:00:00+00:00")
             for seq in range(1, 4996)])
    assert post(client, payload(device="cargo-02")).json()["inserted"] == 2
    message = payload(boot="seed")
    message["lines"] = [{"seq": seq, "uptime_ms": seq * 10, "message": str(seq)}
                        for seq in range(4996, 5028)]
    assert post(client, message).json()["inserted"] == 32
    result = read(client).json()
    assert result["total"] == 5000 and result["lines"][0]["seq"] == 5027
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MIN(seq) FROM raw_console WHERE device_id='cargo-01'").fetchone()[0] == 28
    assert read(client, device="cargo-02").json()["total"] == 2


def test_console_persists_lines_and_drop_status_after_server_restart(tmp_path):
    path = tmp_path / "console.db"
    message = payload()
    message["dropped_lines"] = 4
    with TestClient(create_app(path, "test-secret")) as client:
        post(client, message)
    with TestClient(create_app(path, "test-secret")) as client:
        assert read(client).json()["total"] == 2
        assert read(client).json()["dropped_lines"] == 4
        assert post(client, message).json()["inserted"] == 0
