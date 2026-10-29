"""Independent local/cloud cargo receiver. Run from the repository root."""
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
import json
import os
import secrets
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
    uptime_ms: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
    kind: Annotated[str, Field(min_length=1, max_length=64)]
    data: dict[str, JsonValue]


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Annotated[str, Field(min_length=1, max_length=128)]
    boot_id: Annotated[str, Field(min_length=1, max_length=128)]
    records: Annotated[list[Record], Field(min_length=1, max_length=100)]


def create_app(db_path: str | Path | None = None, token: str | None = None) -> FastAPI:
    path = Path(db_path or os.environ.get("DB_PATH", str(Path(__file__).parent / "data" / "cargo.db")))
    expected_token = token if token is not None else os.environ.get("DEVICE_TOKEN", "dev-token-change-me")
    if not expected_token:
        raise ValueError("DEVICE_TOKEN must not be empty")

    def connect():
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    boot_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    uptime_ms INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    data TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    UNIQUE(device_id, boot_id, seq)
                )
            """)
        yield

    application = FastAPI(title="Cargo Integrity Test Server", lifespan=lifespan)

    def authenticate(x_device_token: Annotated[str | None, Header()] = None):
        if x_device_token is None or not secrets.compare_digest(
            x_device_token.encode(), expected_token.encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Device-Token")

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post("/api/v1/batches", dependencies=[Depends(authenticate)])
    def receive_batch(batch: Batch):
        received_at = datetime.now(timezone.utc).isoformat()
        try:
            # Validate/serialize the complete batch before writing. Non-finite
            # sensor values must be sent as JSON null, never NaN/Infinity.
            rows = [
                (batch.device_id, batch.boot_id, item.seq, item.uptime_ms,
                 item.kind, json.dumps(item.data, allow_nan=False), received_at)
                for item in batch.records
            ]
        except ValueError:
            raise HTTPException(status_code=422, detail="Sensor data must use finite JSON numbers or null")
        try:
            with closing(connect()) as connection, connection:
                before = connection.total_changes
                connection.executemany("""
                    INSERT INTO records
                    (device_id, boot_id, seq, uptime_ms, kind, data, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, boot_id, seq) DO NOTHING
                """, rows)
                inserted = connection.total_changes - before
        except sqlite3.Error:
            raise HTTPException(status_code=503, detail="Storage unavailable; retry the complete batch")
        # 'received' acknowledges the entire committed batch, including retry
        # duplicates. Duplicate IDs preserve the original stored record.
        return {"accepted": True, "received": len(batch.records), "inserted": inserted}

    @application.get("/api/v1/records", dependencies=[Depends(authenticate)])
    def read_records(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        device_id: Annotated[str | None, Query(max_length=128)] = None,
    ):
        where = " WHERE device_id = ?" if device_id is not None else ""
        parameters = [device_id] if device_id is not None else []
        try:
            with closing(connect()) as connection:
                rows = connection.execute(
                    "SELECT * FROM records" + where + " ORDER BY id DESC LIMIT ?",
                    [*parameters, limit],
                ).fetchall()
                total = connection.execute("SELECT COUNT(*) FROM records" + where, parameters).fetchone()[0]
        except sqlite3.Error:
            raise HTTPException(status_code=503, detail="Storage unavailable")
        return {"total": total, "records": [{**dict(row), "data": json.loads(row["data"])} for row in rows]}

    @application.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(Path(__file__).with_name("dashboard.html"))

    return application


app = create_app()
