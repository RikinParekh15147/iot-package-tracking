"""Read-only check of the live board/server. Run explicitly, not in unit tests."""
import argparse
from datetime import datetime, timezone
import json
import os
import time
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--device", default="cargo-esp32-01")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    latest = {}
    with httpx.Client(base_url=args.url, timeout=5,
                      headers={"X-Device-Token": os.environ.get("DEVICE_TOKEN", "dev-token-change-me")}) as client:
        response = client.get("/health")
        response.raise_for_status()
        assert response.json()["status"] == "ok"
        while time.monotonic() < deadline:
            connection = client.get("/api/v1/connection", params={"device_id": args.device})
            connection.raise_for_status()
            records = client.get("/api/v1/records", params={"device_id": args.device, "limit": 100})
            records.raise_for_status()
            telemetry = next((r for r in records.json()["records"] if r["kind"] == "telemetry"), None)
            latest = {"connection": connection.json(), "telemetry": telemetry}
            if telemetry:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(telemetry["received_at"])).total_seconds()
                data = telemetry["data"]
                if (connection.json()["connected"] and 0 <= age <= 120 and data.get("imu_ok") is True
                        and data.get("reference_valid") is True and data.get("dht_ok") is True
                        and data.get("storage_errors") == 0 and data.get("dropped_records") == 0):
                    print(json.dumps({"result": "PASS", "device_id": args.device,
                        "connected": True, "telemetry_age_s": round(age, 1), "total_g": data["total_g"],
                        "temperature_c": data["temperature_c"], "humidity_pct": data["humidity_pct"],
                        "storage_errors": 0, "dropped_records": 0}))
                    return
            time.sleep(2)
    raise SystemExit("FAIL: no fresh healthy device telemetry within timeout: " + json.dumps(latest))


if __name__ == "__main__":
    main()
