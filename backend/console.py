"""Bounded raw device application logs, independent of USB/boot-ROM output."""
from contextlib import closing
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
TripID = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
Counter = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
MAX_LINES_PER_DEVICE = 5000


class ConsoleLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
    uptime_ms: Counter
    message: Annotated[str, Field(strict=True, max_length=1024)]
    trip_id: TripID | None = None


class ConsoleBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Identifier
    boot_id: Identifier
    dropped_lines: Counter = 0
    lines: Annotated[list[ConsoleLine], Field(min_length=1, max_length=32)]


def init_console_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS raw_console (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            uptime_ms INTEGER NOT NULL,
            message TEXT NOT NULL,
            trip_id TEXT,
            received_at TEXT NOT NULL,
            UNIQUE(device_id,boot_id,seq)
        );
        CREATE INDEX IF NOT EXISTS raw_console_device ON raw_console(device_id,id);
        CREATE INDEX IF NOT EXISTS raw_console_trip ON raw_console(device_id,trip_id,id);
        CREATE TABLE IF NOT EXISTS raw_console_status (
            device_id TEXT PRIMARY KEY,
            boot_id TEXT NOT NULL,
            dropped_lines INTEGER NOT NULL,
            received_at TEXT NOT NULL
        );
    """)


def install_console_routes(app, connect, authenticate):
    auth = [Depends(authenticate)]

    @app.post("/api/v1/console", dependencies=auth)
    def receive_console(batch: ConsoleBatch):
        timestamp = datetime.now(timezone.utc).isoformat()
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for trip_id in {line.trip_id for line in batch.lines if line.trip_id}:
                trip = db.execute("SELECT device_id FROM trips WHERE id=?", (trip_id,)).fetchone()
                if trip is None or trip["device_id"] != batch.device_id:
                    raise HTTPException(status_code=422, detail="Console line trip must exist and belong to this device")
            before = db.total_changes
            db.executemany("""INSERT INTO raw_console
                (device_id,boot_id,seq,uptime_ms,message,trip_id,received_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(device_id,boot_id,seq) DO NOTHING""",
                [(batch.device_id, batch.boot_id, line.seq, line.uptime_ms,
                  line.message, line.trip_id, timestamp) for line in batch.lines])
            inserted = db.total_changes - before
            db.execute("""INSERT INTO raw_console_status(device_id,boot_id,dropped_lines,received_at)
                VALUES (?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
                boot_id=excluded.boot_id,
                dropped_lines=CASE WHEN raw_console_status.boot_id=excluded.boot_id
                    THEN MAX(raw_console_status.dropped_lines,excluded.dropped_lines)
                    ELSE excluded.dropped_lines END,
                received_at=excluded.received_at""",
                (batch.device_id, batch.boot_id, batch.dropped_lines, timestamp))
            # Retention follows receipt/insertion order. Unassigned and ended-trip
            # rows share the same bounded device history; no trip is reassigned.
            db.execute("""DELETE FROM raw_console WHERE device_id=? AND id NOT IN
                (SELECT id FROM raw_console WHERE device_id=? ORDER BY id DESC LIMIT ?)""",
                (batch.device_id, batch.device_id, MAX_LINES_PER_DEVICE))
        return {"accepted": True, "received": len(batch.lines), "inserted": inserted}

    @app.get("/api/v1/console", dependencies=auth)
    def read_console(
        device_id: Annotated[Identifier, Query()] = "cargo-esp32-01",
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        trip_id: Annotated[TripID | None, Query()] = None,
        boot_id: Annotated[Identifier | None, Query()] = None,
    ):
        clauses, values = ["device_id=?"], [device_id]
        for column, value in (("trip_id", trip_id), ("boot_id", boot_id)):
            if value is not None:
                clauses.append(column + "=?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses)
        with closing(connect()) as db:
            rows = db.execute("SELECT * FROM raw_console" + where + " ORDER BY id DESC LIMIT ?",
                              [*values, limit]).fetchall()
            total = db.execute("SELECT COUNT(*) FROM raw_console" + where, values).fetchone()[0]
            status = db.execute("SELECT * FROM raw_console_status WHERE device_id=?", (device_id,)).fetchone()
        return {"device_id": device_id, "total": total, "lines": [dict(row) for row in rows],
                "dropped_lines": status["dropped_lines"] if status else 0,
                "dropped_boot_id": status["boot_id"] if status else None}
