# First cargo prototype

The original all-sensor test remains in firmware/component_test/.
New firmware is firmware/cargo_monitor/cargo_monitor.ino.

1. ESP32 and laptop must be on the same reachable 2.4GHz network.
2. Follow [Connection setup](CONNECTION_SETUP.md) to start the server. Open
   http://localhost:8000 for live connection status; `/records` shows sensor records.
   Enter token `dev-token-change-me` unless you configured a different token.
3. Copy config.example.h to config.h and set your Wi-Fi settings and current
   laptop LAN URL ending in `/api/v1/batches`. Update the URL when the laptop's
   address changes. Credentials are ignored by Git.
4. Upload cargo_monitor.ino, Serial Monitor 115200. Keep the box stationary during
   startup. First use: if storage cannot mount, send uppercase F to initialize it.
5. With the magnet near the reed, send `a` to arm. Remove the magnet and look for
   suspected_tamper on Serial and the dashboard. Send `d` to disarm.
6. Firmware reports every 5 seconds online, every minute offline, plus state changes. Local detection keeps running
   without Wi-Fi. Up to 256 records survive resets and replay after reconnection.
   Storage is bounded; counters report drops if it fills.

Every reset starts DISARMED. `c` captures a new reference while disarmed and
stationary. This is rule-based detection, not trained damage prediction. Bare LDR
alerts stay disabled until the proper resistor divider is installed and configured.

Backend restart (PowerShell from this folder):

```powershell
.venv/Scripts/python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

If another device cannot reach the laptop server, check Windows firewall/private
network access and hotspot client isolation. The server being reachable from the
laptop alone does not establish ESP32 reachability. Full instructions are in
backend/README.md and firmware/cargo_monitor/README.md.
