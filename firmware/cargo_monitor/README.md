# Cargo monitor: first connected prototype

Classic ESP32 DevKit / WROOM32. Wiring is unchanged from
`../component_test/README.md`: MPU SDA21/SCL22, reed D0=27, DHT DATA=4,
LDR divider=34, common GND and 3V3. Use a 2.4GHz Wi-Fi hotspot.

## Configure and upload

The local `config.h` contains your Wi-Fi credentials and is ignored by Git.
For a new checkout copy `config.example.h` to `config.h`. Set SERVER_URL to
`http://YOUR_PC_WIFI_IP:8000/api/v1/batches` and match DEVICE_TOKEN with the server.
The current local URL uses the laptop Wi-Fi address at setup time; update it if
that address changes. Never use localhost on the ESP32. Wi-Fi credentials are
compiled into the firmware; do not share compiled binaries containing them.

Install ArduinoJson 7.x, Adafruit MPU6050, and DHT sensor library by Adafruit (with Adafruit Unified
Sensor dependency). Other libraries come with the ESP32 board package.
Open cargo_monitor.ino and upload with the same board/port as component_test.
Board used for compilation: esp32:esp32:esp32, default partition layout.

Open Serial Monitor at 115200 baud. Keep the board still for the initial
3-second reference capture. If flash reports STORAGE UNAVAILABLE on first use,
send uppercase F once to initialize it. This formats the LittleFS partition;
do not do this if it contains history you need. It is refused if already mounted.
The firmware does not auto-format on a mount failure.

Serial commands (send one letter; newline setting does not matter):

- `c`: capture stationary orientation reference and gyro bias; disarm first.
- `a`: arm after lid is closed and a valid reference exists.
- `d`: disarm. Every boot starts disarmed; this prototype does not persist arming.
- `i`: print detected I2C addresses and MPU identity/configuration registers.

For your bare LDR, light detection is disabled until a proper 10k divider is
installed. Then enable LDR_ENABLED and tune LIGHT_THRESHOLD inside the closed
box and when opened. Light output here is raw ADC, not lux.

## What it does

- MPU sampled around 100Hz using checked I2C burst reads. Failures mark motion
  unavailable, generate an event, and trigger reinitialization attempts.
- Relative tilt over 45 degrees for 3 seconds while relatively stable, total
  acceleration over 3g (3-second cooldown), armed reed opening, light exposure
  for 1 second, and environmental excursions for 15 seconds generate events.
- The local detector uses experimental rules, not trained ML or damage prediction.
  Neither opening nor an impact alone proves tampering or cargo damage.
- DHT is read every 3 seconds. Environmental limits default to 10–35C and <=75%RH.
- A telemetry summary is saved every 5 seconds while the server is reachable,
  every 60 seconds offline, and immediately on confirmed state changes.
- Upload runs in a separate task. When connected, it sends up to 25 queued
  records per batch; backlog drains in successive batches. Normal check interval
  is 5 seconds; failed requests back off to 60 seconds. Offline reconnect checks
  occur approximately every 10 seconds.
- Flash queue survives ordinary resets. Records retain boot IDs and sequence
  numbers; only server-acknowledged records are removed. Server deduplicates retry
  batches, including a power failure after acceptance but before local deletion.
- Each record is written to a temporary file then renamed. Power loss during an
  unfinished write may lose that record. A corrupted queued file blocks replay
  rather than silently discarding history.

## Storage and measurement limits

Queue holds at most 256 records, reserving the last 32 slots for events.
That is up to roughly 3.7 hours of one-minute telemetry with no events and no
uploads; actual capacity can be lower if flash fills. When telemetry capacity is
reached, new telemetry is rejected; events can use remaining slots. At full
capacity all new records are rejected, preserving the older records. Serial and
telemetry report dropped_records and storage_errors; these counters reset on
reboot. This is bounded buffering, not unlimited offline storage. For long
shipments, increase the telemetry interval or add larger flash/SD storage.

Only uptime and boot ID are guaranteed for offline event timing. Server receipt
time is not occurrence time. HTTPS validates ROOT_CA_PEM and needs synchronized
system time; the initial LAN test uses HTTP. Do not publish that HTTP test endpoint.

The reference capture estimates gyro bias and the box's initial direction;
it does NOT perform full accelerometer calibration. Raw g values retain your
observed offset. Tilt is approximate; short impact peaks may be missed by polling,
DHT timing, flash writes, or other sampling delays. sample_gap_count reports loop
sampling gaps over 30ms. A successful I2C read does not prove sensor integrity.
MPU faults invalidate the reference; disarm, fix wiring, send c, and arm again.
Lid and environment monitoring can continue while motion is unavailable.

## Quick test

1. Start backend (see ../../backend/README.md) on the same reachable network.
2. Upload firmware and keep board stationary through setup.
3. Open http://localhost:8000 and enter the matching development token.
4. Confirm boot/reference/DHT records, then close reed and send a.
5. Remove magnet: suspected_tamper event should appear after upload.
6. Turn hotspot off temporarily: queue count should increase, while local events
   still log. Re-enable it and verify queue drains with retained event uptimes.
7. For reboot persistence, create queued events offline, reset, then reconnect.
   Old boot IDs should upload alongside the new boot; no duplicate records.

Cloud migration: deploy backend on persistent storage behind HTTPS, replace token,
update SERVER_URL and ROOT_CA_PEM, and rebuild. No broker or separate database
service is needed for this prototype.

MPU startup uses Adafruit MPU6050 initialization, matching the original sensor
test, and waits for samples after configuration. Near-zero acceleration alone is not considered a
communication failure (it can occur in free fall); an entirely zero sensor frame
is rejected. Low-g lasting over 500ms is treated as a fault for this cargo prototype. Tilt calculation is skipped near zero acceleration.

The raw signed-byte decoder is named decodeSigned16: do not rename it to word,
which is an Arduino macro and would replace calls with makeWord(index), yielding
near-zero, incorrect acceleration. Actual sensor reads still check I2C byte counts.

HTTP transport clients are declared before HTTPClient so they outlive its
destructor. Connection reuse is disabled for this initial batch-upload prototype.

## Fetch live data

The dashboard's Fetch live data button requests a new snapshot through the next
heartbeat (normally within 5 seconds). Firmware advertises supports_fetch and
receives a request ID. The main sensor loop reads DHT again respecting its minimum
interval, captures the latest motion/lid readings, and saves a telemetry record
with fetch_request_id. The server clears the request only after matching telemetry
is committed. Pending commands are coalesced per device and not recaptured on
every heartbeat. Flash replay retains IDs; queued older records drain before the
new record. Server restart clears pending commands; click Fetch again if needed.

Fetching does not arm or disarm the cargo box. Disabled or failed sensors remain
marked unavailable. The returned snapshot is stored in the original trip context;
viewing live readings does not rewrite historical trip readings.

## Raw application logs and event latency

The sketch tees Serial application output into a bounded 96-line RAM buffer and
uploads it to /api/v1/console. The dashboard links to /console for search, pause,
trip filtering and download. USB boot-ROM/system-library logs are not captured by
this wrapper. Overflow drops oldest pending debug lines and reports a counter;
this does not replace persistent cargo events. Debug lines can be lost on reboot.

Cargo events and classified state changes queue a full state snapshot and notify
the network task immediately. The normal minute timer is not involved. Debounce,
light hysteresis and sustained tilt/vibration rules still qualify transitions.
When offline or the server fails, retry/backoff and persistent queue behavior apply.
Online periodic snapshots are 5 seconds, offline 60 seconds; dashboard polling is
2 seconds. The LDR is enabled in this local configuration following confirmation
of a proper 10k divider. Exposure threshold remains experimental.

### Default Wi-Fi recovery networks

The ignored `config.h` supports `WIFI_SSID` / `WIFI_PASSWORD` and optional
`WIFI_FALLBACK_SSID` / `WIFI_FALLBACK_PASSWORD`. Dashboard profiles are tried
first; missing default networks are appended as recovery options, even when
saved profiles exist. Duplicate SSIDs use the saved profile credentials. The
current network is retained while the server remains reachable. Wi-Fi joining
alone does not guarantee the configured server address is reachable from that
network. Keep actual credentials in ignored `config.h`.
