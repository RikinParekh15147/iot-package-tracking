"""Persisted per-device Wi-Fi profiles, redacted for browser reads."""
from contextlib import closing
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


DeviceID = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)]


class WifiProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ssid: Annotated[str, Field(strict=True, min_length=1)]
    password: Annotated[str, Field(strict=True, min_length=8, max_length=63)] | None = None
    priority: Annotated[int, Field(strict=True, ge=0, le=999)]

    @field_validator("ssid")
    @classmethod
    def ssid_fits_wifi(cls, value):
        if len(value.encode("utf-8")) > 32 or "\x00" in value:
            raise ValueError("SSID must fit 32 UTF-8 bytes and contain no NUL")
        return value

    @field_validator("password")
    @classmethod
    def valid_passphrase(cls, value):
        # WPA passphrases for this prototype are printable ASCII, 8–63 chars.
        # Null/missing means preserve a stored password; it never means erase.
        if value is not None and any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError("Use an 8–63 character printable ASCII Wi-Fi password")
        return value


class WifiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: DeviceID
    profiles: Annotated[list[WifiProfile], Field(max_length=8)]

    @model_validator(mode="after")
    def unique_ssids(self):
        names = [profile.ssid for profile in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("Each SSID may appear only once")
        return self


def init_wifi_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS wifi_configs (
            device_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            profiles TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wifi_applied (
            device_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL
        );
    """)


def read_config(db, device_id):
    row = db.execute("SELECT revision,profiles FROM wifi_configs WHERE device_id=?", (device_id,)).fetchone()
    return {"revision": row["revision"], "profiles": json.loads(row["profiles"])} if row else None


def redacted_config(db, device_id, connection_status=None):
    config = read_config(db, device_id)
    applied = db.execute("SELECT revision FROM wifi_applied WHERE device_id=?", (device_id,)).fetchone()
    row = db.execute("SELECT data,received_at FROM records WHERE device_id=? AND kind='telemetry' ORDER BY id DESC LIMIT 1", (device_id,)).fetchone()
    data = json.loads(row["data"]) if row else {}
    ssid = data.get("wifi_ssid")
    ssid = ssid if isinstance(ssid, str) and ssid else None
    recent = row is not None and 0 <= (datetime.now(timezone.utc) - datetime.fromisoformat(row["received_at"])).total_seconds() < 20
    presence = connection_status(device_id=device_id) if connection_status else {}
    online = bool(presence.get("connected"))
    network = {"ssid": ssid, "connected": bool(ssid and recent and online and data.get("wifi_connected") is not False),
               "reported_at": row["received_at"] if row and ssid else None}
    if presence.get("wifi_ssid"):
        network = {"ssid": presence["wifi_ssid"], "connected": online,
                   "reported_at": presence.get("last_seen")}
    return {"network": network, "device_id": device_id, "revision": config["revision"] if config else 0,
            "applied_revision": applied["revision"] if applied else None,
            "profiles": [{"ssid": p["ssid"], "priority": p["priority"], "has_password": bool(p["password"])}
                         for p in config["profiles"]] if config else []}


def heartbeat_wifi_config(db, device_id, revision):
    # Called inside the heartbeat's existing write transaction. A revision is
    # considered applied only when reported by an authenticated device.
    db.execute("""INSERT INTO wifi_applied(device_id,revision) VALUES (?,?)
        ON CONFLICT(device_id) DO UPDATE SET revision=excluded.revision""", (device_id, revision))
    config = read_config(db, device_id)
    return config if config and config["revision"] != revision else None


def install_wifi_routes(app, connect, authenticate, connection_status=None):
    auth = [Depends(authenticate)]

    @app.get("/api/v1/wifi", dependencies=auth)
    def get_wifi(device_id: Annotated[DeviceID, Query()] = "cargo-esp32-01"):
        with closing(connect()) as db:
            return redacted_config(db, device_id, connection_status)

    @app.put("/api/v1/wifi", dependencies=auth)
    def update_wifi(message: WifiUpdate):
        with closing(connect()) as db, db:
            # Serialize concurrent saves and password preservation together.
            db.execute("BEGIN IMMEDIATE")
            old = read_config(db, message.device_id)
            old_passwords = {p["ssid"]: p["password"] for p in old["profiles"]} if old else {}
            profiles = []
            for profile in message.profiles:
                password = profile.password if profile.password is not None else old_passwords.get(profile.ssid)
                if password is None:
                    raise HTTPException(status_code=422, detail="Every new SSID requires a password")
                profiles.append({"ssid": profile.ssid, "password": password, "priority": profile.priority})
            profiles.sort(key=lambda profile: (profile["priority"], profile["ssid"]))
            revision = old["revision"] + 1 if old else 1
            db.execute("""INSERT INTO wifi_configs(device_id,revision,profiles) VALUES (?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET revision=excluded.revision,profiles=excluded.profiles""",
                (message.device_id, revision, json.dumps(profiles)))
            return redacted_config(db, message.device_id, connection_status)
