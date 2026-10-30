# Testing

Run from the project root. Tests use temporary SQLite databases and isolated
browser contexts; synthetic test trips/records never go into the live cargo DB.

## Install

```powershell
backend/.venv/Scripts/python.exe -m pip install -r requirements-test.txt
```

Windows browser tests use installed Microsoft Edge in headless mode. Override
with `CARGO_BROWSER_CHANNEL` if needed. On Linux, use a separate Linux virtual
environment, install these same requirements, and run `python -m playwright
install chromium` once. Replace the Windows Python path below with that venv's
Python. Zig supplies a native C++ compiler for firmware helpers; no global C++
compiler is required. Its first installation is a substantial download.

## Automated suite

```powershell
backend/.venv/Scripts/python.exe -m pytest backend tests -q --junitxml=build/test-results.xml
```

Coverage:

- Backend: authentication across API routes, strict validation and finite values,
  retry deduplication/immutability, device/boot identity, transaction rollback,
  persistence/migration, trip lifecycle, concurrent starts, trip attribution,
  heartbeat timeout/reconnect, CSV safety, controlled database failures.
- Native firmware: signed MPU frame decoding under Arduino's `word` macro,
  actual captured frame, arming prerequisites, queue capacity/event reservation,
  sustained-event duration/reset/rearming, startup at uptime zero, millisecond
  rollover, impact thresholds/cooldown, tilt geometry/invalid data, motion gating.
  The tested helpers are included directly by cargo_monitor.ino; tests do not
  duplicate production implementations. The C++ tests run as a native executable.
- Real browser: create trip, checkpoint, note, export CSV, finish trip; recover
  from invalid authentication; avoid showing healthy connection on server outage;
  raw record escaping/filtering; heartbeat offline/reconnect; mobile navigation;
  fresh device fetch, Wi-Fi configuration/revision confirmation, seal assessments,
  and single-device trip history; dedicated trip pages, stitched state durations,
  expanded original readings, deep links and per-device Wi-Fi actions.
  A JavaScript runtime error fails the browser test.

Compile the complete ESP32 sketch separately with the Arduino CLI from Arduino
IDE's installation (or through the IDE):

```powershell
& "C:/Program Files/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe" compile --fqbn esp32:esp32:esp32 firmware/cargo_monitor
```

Local `config.h` must exist; see the firmware README for library dependencies.
Unit-test extraction changes the firmware build. Upload the newly compiled sketch
before attributing native-tested helper code to the binary currently on the board.

## Live read-only smoke test

With backend running and ESP32 connected:

```powershell
backend/.venv/Scripts/python.exe tests/live_smoke.py --url http://localhost:8000
```

Use DEVICE_TOKEN in the environment if changed. Requires a live heartbeat and
recent telemetry with working MPU/reference/DHT and zero storage/drop counters.
This script does not arm, reset, format, or modify device/server records.

## Physical checks still require hands-on work

Native rule tests do not establish real sensor accuracy or physical alert quality.
Exercise reed opening/closing, sustained tilt, gentle impact, and environmental
limits on the actual box. Install the correct LDR resistor divider before enabling
light detection. Validate wireless loss independently from a server outage, storage
capacity, abrupt power loss, and long-duration battery operation. Existing bench
results and their limits are recorded in HARDWARE_VALIDATION.md.
