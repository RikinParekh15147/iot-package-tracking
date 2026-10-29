# First ESP32 connection

Start the local receiver from the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 and enter the token from your firmware configuration
(development default: `dev-token-change-me`). The device ID defaults to
`cargo-esp32-01`. The existing readings page is at `/records`.

Copy `firmware/cargo_monitor/config.example.h` to `config.h` in that same folder.
Set the Wi-Fi credentials and `SERVER_URL` to your laptop's current LAN address
followed by `/api/v1/batches`. At setup, the laptop address was `10.177.148.254`,
so the URL was `http://10.177.148.254:8000/api/v1/batches`. This address can change.
Keep the configured token and device ID consistent with the page and server.
Do not commit `config.h`; it is ignored by Git.

For the first connection-only test, upload
`firmware/connection_test/connection_test.ino` on COM5 (ESP32 Dev Module).
It uses the Wi-Fi credentials already saved on the board and does not need any
sensors. Update `HEARTBEAT_URL` if your laptop's IP changes. If saved credentials
are unavailable, set `WiFi.begin("your-ssid", "your-password")` locally; do not
commit credentials. This replaces the running sketch with the connection test.

For the complete sensor firmware instead, upload the updated `cargo_monitor.ino` using the ESP32 board and port selected
in Arduino IDE. Serial Monitor uses 115200 baud. `HEARTBEAT HTTP=200` confirms
the receiver acknowledged the live heartbeat. USB powers/programs the board;
the connection to the webpage uses Wi-Fi.

Keep both devices on the same reachable network. If heartbeat requests fail,
check the server address, Windows Firewall access for the Python server, and
hotspot client isolation. No sensor setup or arming is needed for heartbeats.

The page polls every two seconds. Heartbeats are sent approximately every five
seconds, independently of queued sensor records. After 20 seconds without one,
the board appears offline. Server restart clears live status until another
heartbeat arrives. Run a single server worker for this local test. To verify
offline detection, disconnect board power and wait 20 seconds, then reconnect.
