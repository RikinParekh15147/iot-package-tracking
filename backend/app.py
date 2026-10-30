"""Independent local/cloud cargo receiver. Run from the repository root."""
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
import asyncio
import json
import os
import secrets
import sqlite3
import time
from threading import Lock

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from backend.trips import init_trip_schema, install_trip_routes, save_heartbeat
from backend.wifi import heartbeat_wifi_config, init_wifi_schema, install_wifi_routes
from backend.console import init_console_schema, install_console_routes
from backend.analysis import init_analysis_schema, install_analysis_routes
from backend.performance import init_performance_schema, install_performance_routes


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
    uptime_ms: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
    kind: Annotated[str, Field(min_length=1, max_length=64)]
    data: dict[str, JsonValue]
    trip_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Annotated[str, Field(min_length=1, max_length=128)]
    boot_id: Annotated[str, Field(min_length=1, max_length=128)]
    records: Annotated[list[Record], Field(min_length=1, max_length=100)]


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Annotated[str, Field(min_length=1, max_length=128)]
    firmware: Annotated[str | None, Field(max_length=80)] = None
    ip: Annotated[str | None, Field(max_length=45)] = None
    rssi: Annotated[int | None, Field(ge=-127, le=0)] = None
    uptime_ms: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)] | None = None
    supports_fetch: Annotated[bool, Field(strict=True)] = False
    wifi_revision: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)] = 0
    wifi_ssid: Annotated[str | None, Field(max_length=32, strict=True)] = None


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]


def create_app(db_path: str | Path | None = None, token: str | None = None, performance_source: str | None = None) -> FastAPI:
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
            init_trip_schema(connection)
            init_wifi_schema(connection)
            init_console_schema(connection)
            init_analysis_schema(connection)
            init_performance_schema(connection)
        yield

    application = FastAPI(title="Cargo Integrity Test Server", lifespan=lifespan)

    @application.exception_handler(sqlite3.Error)
    async def storage_error(_request, _error):
        # Trip routes share this fallback; transaction contexts have already
        # rolled back before an error reaches the handler. Explicit 409 trip
        # conflicts and route-specific storage responses remain unchanged.
        return JSONResponse(status_code=503, content={"detail": "Storage unavailable; retry later"})

    heartbeats = {}
    heartbeat_networks = {}
    fetch_capabilities = {}
    # Transient control requests survive device reconnects, not server restarts.
    pending_fetches = {}
    heartbeat_lock = Lock()

    def authenticate(x_device_token: Annotated[str | None, Header()] = None):
        if x_device_token is None or not secrets.compare_digest(
            x_device_token.encode(), expected_token.encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Device-Token")

    # One held request per dashboard; writes wake it after committing. This is
    # deliberately process-local: run this prototype with one uvicorn worker.
    change_condition = asyncio.Condition()
    change_epoch = secrets.token_hex(8)
    change_number = 0

    @application.middleware("http")
    async def announce_changes(request, call_next):
        nonlocal change_number
        response = await call_next(request)
        if (request.url.path.startswith("/api/v1/")
                and request.url.path != "/api/v1/console"
                and not request.url.path.startswith("/api/v1/performance")
                and request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and 200 <= response.status_code < 300):
            async with change_condition:
                change_number += 1
                change_condition.notify_all()
        return response

    @application.get("/api/v1/changes", dependencies=[Depends(authenticate)])
    async def changes(request: Request, after: str = "", wait_ms: int = Query(default=2000, ge=0, le=20000)):
        def revision():
            return f"{change_epoch}:{change_number}"
        deadline = asyncio.get_running_loop().time() + wait_ms / 1000
        async with change_condition:
            while after == revision():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0 or await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(change_condition.wait(), min(remaining, .25))
                except asyncio.TimeoutError:
                    pass
            return {"revision": revision(), "changed": after != revision()}

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.post("/api/v1/heartbeat", dependencies=[Depends(authenticate)])
    def heartbeat(message: Heartbeat):
        try:
            with closing(connect()) as connection, connection:
                trip_id = save_heartbeat(connection, message.model_dump())
                wifi_config = heartbeat_wifi_config(connection, message.device_id, message.wifi_revision)
        except sqlite3.Error:
            raise HTTPException(status_code=503, detail="Storage unavailable; retry heartbeat")
        with heartbeat_lock:
            heartbeats[message.device_id] = (time.monotonic(), datetime.now(timezone.utc).isoformat())
            heartbeat_networks[message.device_id] = message.wifi_ssid
            fetch_capabilities[message.device_id] = message.supports_fetch
            telemetry_request_id = pending_fetches.get(message.device_id)
        return {"accepted": True, "active_trip_id": trip_id,
                "telemetry_request_id": telemetry_request_id, "wifi_config": wifi_config}

    @application.post("/api/v1/fetch", dependencies=[Depends(authenticate)], status_code=202)
    def request_telemetry(message: FetchRequest):
        with heartbeat_lock:
            last = heartbeats.get(message.device_id)
            if last is None or time.monotonic() - last[0] >= 20:
                raise HTTPException(status_code=409, detail="Device offline. Reconnect the ESP32 before fetching live data.")
            if not fetch_capabilities.get(message.device_id, False):
                raise HTTPException(status_code=409, detail="Device firmware does not support live fetch. Upload the updated firmware first.")
            request_id = pending_fetches.setdefault(message.device_id, secrets.token_hex(16))
        return {"request_id": request_id, "status": "pending", "device_id": message.device_id}

    @application.get("/api/v1/latest", dependencies=[Depends(authenticate)])
    def latest_telemetry(device_id: Annotated[str, Query(min_length=1, max_length=128)] = "cargo-esp32-01"):
        with closing(connect()) as connection:
            row = connection.execute("""SELECT * FROM records
                WHERE device_id = ? AND kind = 'telemetry' ORDER BY id DESC LIMIT 1""", (device_id,)).fetchone()
        with heartbeat_lock:
            request_id = pending_fetches.get(device_id)
        return {"device_id": device_id,
                "record": {**dict(row), "data": json.loads(row["data"])} if row else None,
                "request": {"request_id": request_id, "status": "pending"} if request_id else None}

    @application.get("/api/v1/connection", dependencies=[Depends(authenticate)])
    def connection_status(device_id: Annotated[str, Query(min_length=1, max_length=128)] = "cargo-esp32-01"):
        with heartbeat_lock:
            last = heartbeats.get(device_id)
            ssid = heartbeat_networks.get(device_id)
        age = max(0, time.monotonic() - last[0]) if last else None
        return {"device_id": device_id, "connected": age is not None and age < 20,
                "last_seen": last[1] if last else None, "age_seconds": age,
                "timeout_seconds": 20, "wifi_ssid": ssid}

    @application.get("/records", include_in_schema=False)
    def record_dashboard():
        return FileResponse(Path(__file__).with_name("records.html"))

    install_trip_routes(application, connect, authenticate, connection_status)
    install_wifi_routes(application, connect, authenticate, connection_status)
    install_console_routes(application, connect, authenticate)
    install_analysis_routes(application, connect, authenticate, connection_status)
    install_performance_routes(application, connect, authenticate, connection_status, expected_token,
                               source_url=performance_source if performance_source is not None else "" if db_path is not None else None)

    @application.get("/console", include_in_schema=False)
    def console_page():
        return FileResponse(Path(__file__).with_name("console.html"))

    @application.post("/api/v1/batches", dependencies=[Depends(authenticate)])
    def receive_batch(batch: Batch):
        received_at = datetime.now(timezone.utc).isoformat()
        try:
            # Validate/serialize the complete batch before writing. Non-finite
            # sensor values must be sent as JSON null, never NaN/Infinity.
            rows = [
                (batch.device_id, batch.boot_id, item.seq, item.uptime_ms,
                 item.kind, json.dumps(item.data, allow_nan=False), received_at, item.trip_id)
                for item in batch.records
            ]
        except ValueError:
            raise HTTPException(status_code=422, detail="Sensor data must use finite JSON numbers or null")
        try:
            with closing(connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                for trip_id in {item.trip_id for item in batch.records if item.trip_id}:
                    trip = connection.execute("SELECT device_id FROM trips WHERE id = ?", (trip_id,)).fetchone()
                    if trip is None or trip["device_id"] != batch.device_id:
                        raise HTTPException(status_code=422, detail="Record trip must exist and belong to this device")
                before = connection.total_changes
                connection.executemany("""
                    INSERT INTO records
                    (device_id, boot_id, seq, uptime_ms, kind, data, received_at, trip_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, boot_id, seq) DO NOTHING
                """, rows)
                inserted = connection.total_changes - before
                acknowledged_fetches = set()
                for item in batch.records:
                    request_id = item.data.get("fetch_request_id")
                    if item.kind != "telemetry" or not isinstance(request_id, str):
                        continue
                    # A conflicting retry must not acknowledge a new request
                    # using data that INSERT ... DO NOTHING did not store.
                    stored = connection.execute("""SELECT kind, data FROM records
                        WHERE device_id = ? AND boot_id = ? AND seq = ?""",
                        (batch.device_id, batch.boot_id, item.seq)).fetchone()
                    if stored["kind"] == "telemetry" and json.loads(stored["data"]).get("fetch_request_id") == request_id:
                        acknowledged_fetches.add(request_id)
        except sqlite3.Error:
            raise HTTPException(status_code=503, detail="Storage unavailable; retry the complete batch")
        # Only clear after the transaction context has successfully committed.
        with heartbeat_lock:
            if pending_fetches.get(batch.device_id) in acknowledged_fetches:
                pending_fetches.pop(batch.device_id, None)
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

    @application.get("/connection", include_in_schema=False)
    def connection_page():
        return FileResponse(Path(__file__).with_name("connection.html"))

    @application.get("/assets/{name}", include_in_schema=False)
    def asset(name: str):
        if name not in {"dashboard.js", "dashboard.css", "performance.js", "performance.css"}:
            raise HTTPException(status_code=404)
        return FileResponse(Path(__file__).with_name(name))

    return application


app = create_app()
