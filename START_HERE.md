# First cargo prototype

The original all-sensor test remains in firmware/component_test/.
New firmware is firmware/cargo_monitor/cargo_monitor.ino.

1. ESP32 and laptop must be on the same reachable 2.4GHz network.
2. Open http://localhost:8000 on the laptop. Enter token `dev-token-change-me`.
3. Firmware local config.h already contains the supplied Wi-Fi settings and
   http://10.177.148.229:8000/api/v1/batches. That IP is the laptop Wi-Fi IP at setup;
   update it if the laptop changes network/address. Credentials are ignored by Git.
4. Upload cargo_monitor.ino, Serial Monitor 115200. Keep the box stationary during
   startup. First use: if storage cannot mount, send uppercase F to initialize it.
5. With the magnet near the reed, send `a` to arm. Remove the magnet and look for
   suspected_tamper on Serial and the dashboard. Send `d` to disarm.
6. Firmware reports once per minute plus events. Local detection keeps running
   without Wi-Fi. Up to 256 records survive resets and replay after reconnection.
   Storage is bounded; counters report drops if it fills.

Every reset starts DISARMED. `c` captures a new reference while disarmed and
stationary. This is rule-based detection, not trained damage prediction. Bare LDR
alerts stay disabled until the proper resistor divider is installed and configured.

Backend restart (PowerShell from this folder):

```powershell
backend/.venv/Scripts/python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

If another device cannot reach the laptop server, check Windows firewall/private
network access and hotspot client isolation. The server being reachable from the
laptop alone does not establish ESP32 reachability. Full instructions are in
backend/README.md and firmware/cargo_monitor/README.md.
