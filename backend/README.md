# Cargo rapid-test server

An independent FastAPI + SQLite server. It does not run on the ESP32.
The page at `/` checks live ESP32 heartbeats; `/records` lists received readings
and onboard events. See [first connection setup](../CONNECTION_SETUP.md). `/docs` provides
interactive API documentation. No frontend build or database service is needed.

## Start on Windows (PowerShell, from project root)

```powershell
py -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
$env:DEVICE_TOKEN = "dev-token-change-me"
backend/.venv/Scripts/python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

## Start on Linux / WSL (from project root)

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
export DEVICE_TOKEN='dev-token-change-me'
backend/.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Use a separate virtual environment per OS; do not reuse a Windows venv in WSL.
Open http://localhost:8000 on the server computer. Enter the configured token in
the dashboard and press Connect. The token is not saved in browser storage.

For the ESP32, set its server base URL to `http://YOUR_PC_LAN_IP:8000`, e.g.
`http://192.168.1.50:8000`. **Do not use localhost or 127.0.0.1 on the ESP32**:
those refer to the ESP32 itself. Use the same token in firmware and server.
Both devices must be on networks that can reach one another; guest Wi-Fi may
block client-to-client traffic. Allow incoming TCP port 8000 on your private
Windows network when prompted. Windows hosting is usually easiest for a first
ESP32 LAN test. WSL's default NAT networking may require port forwarding or
mirrored networking for access from another device; opening localhost on the PC
alone does not demonstrate LAN access. Verify the dashboard from a phone on the
same Wi-Fi first. Do not expose this development server directly to the internet.

`DB_PATH` optionally selects another SQLite file; the default is
`backend/data/cargo.db` next to the application source. Keep that directory on
persistent storage when deploying. A cloud deployment should terminate HTTPS
and use a new private token; firmware must validate the server certificate.
This test server uses one shared token, not per-device authentication or user roles.

## Device upload contract

`POST /api/v1/batches`, with header `X-Device-Token: dev-token-change-me`:

```json
{
  "device_id": "cargo-01",
  "boot_id": "unique-per-boot",
  "records": [
    {
      "seq": 1,
      "uptime_ms": 5000,
      "kind": "telemetry",
      "data": {"temperature_c": 27.5, "humidity_pct": 44.0}
    },
    {
      "seq": 2,
      "uptime_ms": 6100,
      "kind": "lid_open",
      "data": {"armed": true}
    }
  ]
}
```

Send 1–100 records per batch. Use JSON `null` for missing sensor values.
NaN/Infinity are rejected. Each record must have a unique, nonnegative sequence
number within `(device_id, boot_id)`. Retries must reuse the same identity and
data; duplicates preserve the first stored record. Different boot IDs allow
sequence counters to restart. Records queued across reboot must retain their
original boot ID and be grouped into separate batches by boot ID.

Success is HTTP 200:

```json
{"accepted": true, "received": 2, "inserted": 2}
```

`received` acknowledges all records in this batch, including duplicates;
`inserted` counts newly stored records. A repeated upload can return
`received: 2, inserted: 0`. The device may remove a batch from its queue only
after validating this acknowledgment. Storage writes are transactional;
HTTP 503 means retry later. HTTP 401 means fix the token. HTTP 422 means fix
the payload. Server receipt time is UTC and differs from event time: an offline
event's `uptime_ms` is relative to its device boot, not an absolute timestamp.
Any synchronized event timestamp can be included separately in `data`.

`GET /api/v1/records?limit=100&device_id=cargo-01` uses the same token header.
It returns `{total, records}` in reverse insertion order, with device ID,
boot ID, sequence, uptime, kind, data, and `received_at`. Backfilled events
are not necessarily in occurrence order. Limit is 1–500. `/health` is public.

## Tests

```bash
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
backend/.venv/bin/python -m pytest backend/test_app.py -q
```

On Windows replace `backend/.venv/bin/python` with
`backend/.venv/Scripts/python.exe`. Tests use temporary SQLite files and verify
authentication, replay deduplication, batch validation, transactional rollback,
and persistence after restart.
