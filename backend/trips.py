"""Trip lifecycle, device presence and trip-scoped activity for the local dashboard."""
from contextlib import closing
from datetime import datetime, timezone
from typing import Annotated, Literal
import csv
import io
import json
import sqlite3
import uuid

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from backend.state_runs import build_state_sessions, build_state_timeline, decode_record

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
DeviceID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class TripStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    device_id: DeviceID = "cargo-esp32-01"
    origin: Address
    destination: Address
    cargo: Annotated[str, Field(max_length=300)] = ""
    recipient: Annotated[str, Field(max_length=160)] = ""
    notes: Annotated[str, Field(max_length=2000)] = ""


class TripLog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["note", "checkpoint"]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    location: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class TripFinish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "cancelled"] = "completed"


def now():
    return datetime.now(timezone.utc).isoformat()


def init_trip_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS trips (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, device_id TEXT NOT NULL,
            origin TEXT NOT NULL, destination TEXT NOT NULL, cargo TEXT NOT NULL,
            recipient TEXT NOT NULL, notes TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','completed','cancelled')),
            started_at TEXT NOT NULL, ended_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_trip_per_device
            ON trips(device_id) WHERE status = 'active';
        CREATE TABLE IF NOT EXISTS trip_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id TEXT NOT NULL,
            kind TEXT NOT NULL, message TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS trip_logs_trip ON trip_logs(trip_id, id);
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY, last_seen TEXT NOT NULL,
            firmware TEXT, ip TEXT, rssi INTEGER, uptime_ms INTEGER
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(records)")}
    if "trip_id" not in columns:
        db.execute("ALTER TABLE records ADD COLUMN trip_id TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS records_trip ON records(trip_id, id)")


def add_log(db, trip_id, kind, message, location="", data=None, timestamp=None):
    db.execute("""INSERT INTO trip_logs(trip_id,kind,message,location,created_at,data)
                  VALUES (?,?,?,?,?,?)""",
               (trip_id, kind, message, location, timestamp or now(), json.dumps(data or {})))


def save_heartbeat(db, message):
    # Serialize with start/finish so a heartbeat has one unambiguous trip owner.
    db.execute("BEGIN IMMEDIATE")
    timestamp = now()
    previous = db.execute("SELECT last_seen FROM devices WHERE device_id=?", (message["device_id"],)).fetchone()
    db.execute("""INSERT INTO devices(device_id,last_seen,firmware,ip,rssi,uptime_ms)
        VALUES (:device_id,:last_seen,:firmware,:ip,:rssi,:uptime_ms)
        ON CONFLICT(device_id) DO UPDATE SET last_seen=excluded.last_seen,
        firmware=COALESCE(excluded.firmware,devices.firmware),
        ip=COALESCE(excluded.ip,devices.ip),rssi=COALESCE(excluded.rssi,devices.rssi),
        uptime_ms=COALESCE(excluded.uptime_ms,devices.uptime_ms)""", {**message, "last_seen": timestamp})
    trip = db.execute("SELECT id FROM trips WHERE device_id=? AND status='active'", (message["device_id"],)).fetchone()
    if trip:
        latest = db.execute("""SELECT created_at FROM trip_logs WHERE trip_id=?
            AND kind IN ('device_online','heartbeat') ORDER BY id DESC LIMIT 1""", (trip["id"],)).fetchone()
        restored = not previous or (datetime.fromisoformat(timestamp) - datetime.fromisoformat(previous["last_seen"])).total_seconds() >= 20
        if not latest or restored:
            add_log(db, trip["id"], "device_online", "Device connection confirmed", data=message, timestamp=timestamp)
        elif (datetime.fromisoformat(timestamp) - datetime.fromisoformat(latest["created_at"])).total_seconds() >= 60:
            add_log(db, trip["id"], "heartbeat", "Device heartbeat received", data=message, timestamp=timestamp)
    return trip["id"] if trip else None


ALERT_TYPES = {"suspected_tamper", "impact", "unsafe_tilt", "unexpected_light", "environment_warning", "imu_fault", "dht_fault", "network_task_failed",
               "strong_vibration", "packaging_separation", "likely_opened", "unsafe_environment"}


def record_entry(row):
    data = json.loads(row["data"])
    event_type = str(data.get("type", row["kind"]))
    return {"id": "record-" + str(row["id"]), "kind": "alert" if event_type in ALERT_TYPES else row["kind"],
            "message": str(data.get("detail", event_type.replace("_", " "))), "location": "",
            "created_at": row["received_at"], "source": "device", "data": data,
            "boot_id": row["boot_id"], "seq": row["seq"], "uptime_ms": row["uptime_ms"]}


def activity(db, trip_id):
    # Export and detail share the same complete history; UI paginates below.
    logs = [{**dict(r), "id": "log-" + str(r["id"]), "data": json.loads(r["data"]), "source": "trip"}
            for r in db.execute("SELECT * FROM trip_logs WHERE trip_id=? ORDER BY id DESC", (trip_id,))]
    logs += [record_entry(r) for r in db.execute("SELECT * FROM records WHERE trip_id=? ORDER BY id DESC", (trip_id,))]
    return sorted(logs, key=lambda item: (item["created_at"], item["id"]), reverse=True)


def handling_summary(entries):
    # Counts describe recorded events, not a continuously measured exposure
    # duration. Receipt times can be later than occurrence after offline travel.
    counts = {}
    maximum = None
    maximum_entry = None
    for entry in entries:
        if entry["source"] != "device":
            continue
        event_type = str(entry["data"].get("type", ""))
        if event_type in ALERT_TYPES:
            counts[event_type] = counts.get(event_type, 0) + 1
        value = entry["data"].get("value")
        if event_type == "impact" and type(value) in {int, float} and value >= 0 and (maximum is None or value > maximum):
            maximum, maximum_entry = value, entry
    return {"alert_event_counts": counts, "max_impact_g": maximum,
            "max_impact_received_at": maximum_entry["created_at"] if maximum_entry else None,
            "max_impact_boot_id": maximum_entry["boot_id"] if maximum_entry else None,
            "max_impact_uptime_ms": maximum_entry["uptime_ms"] if maximum_entry else None}


def install_trip_routes(app, connect, authenticate, connection_status):
    auth = [Depends(authenticate)]

    def get_trip(db, trip_id):
        row = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Trip not found")
        return dict(row)

    @app.get("/api/v1/trips", dependencies=auth)
    def list_trips():
        with closing(connect()) as db:
            trips = [dict(r) for r in db.execute("""SELECT t.*,
                (SELECT COUNT(*) FROM records r WHERE r.trip_id=t.id) AS record_count,
                (SELECT COUNT(*) FROM trip_logs l WHERE l.trip_id=t.id) AS log_count,
                (SELECT COUNT(*) FROM trip_logs l WHERE l.trip_id=t.id AND kind='checkpoint') AS checkpoint_count
                FROM trips t ORDER BY started_at DESC""")]
            unassigned = db.execute("SELECT COUNT(*) FROM records WHERE trip_id IS NULL").fetchone()[0]
        return {"trips": trips, "unassigned_records": unassigned}

    @app.post("/api/v1/trips", dependencies=auth, status_code=201)
    def start_trip(message: TripStart):
        trip_id = uuid.uuid4().hex
        timestamp = now()
        try:
            with closing(connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""INSERT INTO trips(id,name,device_id,origin,destination,cargo,recipient,notes,status,started_at)
                    VALUES (?,?,?,?,?,?,?,?,'active',?)""",
                    (trip_id, message.name, message.device_id, message.origin, message.destination,
                     message.cargo.strip(), message.recipient.strip(), message.notes.strip(), timestamp))
                add_log(db, trip_id, "started", "Trip started", message.origin, timestamp=timestamp)
                return get_trip(db, trip_id)
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="This device already has an active trip. Complete or cancel it first.")

    @app.get("/api/v1/trips/{trip_id}", dependencies=auth)
    def trip_detail(trip_id: str, offset: Annotated[int, Query(ge=0)] = 0,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50,
                    kind: Literal["all", "alert", "checkpoint", "note", "device", "other"] = "all"):
        with closing(connect()) as db:
            trip = get_trip(db, trip_id)
            entries = activity(db, trip_id)
            telemetry = db.execute("SELECT * FROM records WHERE trip_id=? AND kind='telemetry' ORDER BY id DESC LIMIT 120", (trip_id,)).fetchall()
            state_records = db.execute("SELECT * FROM records WHERE trip_id=? AND kind='telemetry'", (trip_id,)).fetchall()
            checkpoints = [e for e in entries if e["kind"] == "checkpoint"]
            alerts = sum(e["kind"] == "alert" for e in entries)
            filtered = [e for e in entries if kind == "all" or e["kind"] == kind
                        or (kind == "other" and e["kind"] != "telemetry")
                        or (kind == "device" and (e["source"] == "device" or e["kind"] in {"heartbeat", "device_online"}))]
            state_runs = build_state_sessions(state_records)
            return {"trip": trip, "activity": filtered[offset:offset+limit], "total": len(filtered),
                    "checkpoints": list(reversed(checkpoints)), "alert_count": alerts, "activity_count": len(entries),
                    "handling_summary": handling_summary(entries),
                    "state_runs": state_runs, "run_count": len(state_runs),
                    "total_state_samples": sum(run["sample_count"] for run in state_runs),
                    "timeline": build_state_timeline(state_records, entries, state_runs),
                    "telemetry": [{"received_at": r["received_at"], "data": json.loads(r["data"])} for r in reversed(telemetry)]}

    @app.get("/api/v1/trips/{trip_id}/state-runs/{run_id}", dependencies=auth)
    def state_run_records(trip_id: str, run_id: str, offset: Annotated[int, Query(ge=0)] = 0,
                          limit: Annotated[int, Query(ge=1, le=200)] = 50):
        with closing(connect()) as db:
            get_trip(db, trip_id)
            records = [decode_record(row) for row in db.execute(
                "SELECT * FROM records WHERE trip_id=? AND kind='telemetry'", (trip_id,))]
        run = next((run for run in build_state_sessions(records) if run["id"] == run_id), None)
        if run is None:
            raise HTTPException(status_code=404, detail="State run not found; refresh trip history after new uploads")
        by_id = {record["id"]: record for record in records}
        return {"trip_id": trip_id, "run": run, "total": run["sample_count"], "offset": offset,
                "limit": limit, "records": [by_id[identifier] for identifier in run["record_ids"][offset:offset + limit]]}

    @app.post("/api/v1/trips/{trip_id}/logs", dependencies=auth, status_code=201)
    def create_log(trip_id: str, message: TripLog):
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            trip = get_trip(db, trip_id)
            if message.kind == "checkpoint" and trip["status"] != "active":
                raise HTTPException(status_code=409, detail="Checkpoints can only be added to an active trip")
            if message.kind == "checkpoint" and not message.location:
                raise HTTPException(status_code=422, detail="A checkpoint needs a location")
            add_log(db, trip_id, message.kind, message.message, message.location)
        return {"saved": True}

    @app.post("/api/v1/trips/{trip_id}/finish", dependencies=auth)
    def finish_trip(trip_id: str, message: TripFinish):
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            trip = get_trip(db, trip_id)
            if trip["status"] != "active":
                if trip["status"] == message.status:
                    return trip
                raise HTTPException(status_code=409, detail="This trip has already ended")
            timestamp = now()
            db.execute("UPDATE trips SET status=?,ended_at=? WHERE id=?", (message.status, timestamp, trip_id))
            add_log(db, trip_id, message.status, "Delivery confirmed" if message.status == "completed" else "Trip cancelled", timestamp=timestamp)
            return get_trip(db, trip_id)

    @app.get("/api/v1/devices", dependencies=auth)
    def devices():
        with closing(connect()) as db:
            rows = {r["device_id"]: dict(r) for r in db.execute("SELECT * FROM devices")}
            for r in db.execute("SELECT DISTINCT device_id FROM trips"):
                rows.setdefault(r[0], {"device_id": r[0]})
        return {"devices": [{**row, **connection_status(device_id=identifier), "last_received_at": row.get("last_seen")}
                             for identifier, row in rows.items()]}

    @app.get("/api/v1/trips/{trip_id}/export", dependencies=auth)
    def export_trip(trip_id: str):
        with closing(connect()) as db:
            trip = get_trip(db, trip_id)
            entries = activity(db, trip_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["trip_id", "trip_name", "origin", "destination", "status", "received_or_logged_at", "kind", "message", "location", "source", "boot_id", "seq", "uptime_ms", "data"])
        def safe(value):
            text = str(value if value is not None else "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text
        for entry in reversed(entries):
            writer.writerow([safe(v) for v in [trip_id, trip["name"], trip["origin"], trip["destination"], trip["status"],
                entry["created_at"], entry["kind"], entry["message"], entry["location"], entry["source"],
                entry.get("boot_id", ""), entry.get("seq", ""), entry.get("uptime_ms", ""), json.dumps(entry["data"]) ]])
        return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="trip-{trip_id[:8]}.csv"'})
