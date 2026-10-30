# IoT Cargo Integrity & Tamper Detection

An ESP32 prototype that monitors a cargo box using an MPU6050, DHT11,
reed-switch module, and bare LDR. An independent FastAPI server receives
uploads, stores event history in SQLite, and serves a small test dashboard.

The firmware applies onboard rules for suspected opening, impact, sustained
tilt, and environmental excursions. It queues records in flash during network
outages and sends batches when connectivity returns. Server deduplication
prevents retries from creating duplicate records.

## Start here

- [Quick-start checklist](START_HERE.md)
- [All-sensor Serial Monitor test and wiring](firmware/component_test/README.md)
- [Connected firmware setup and limitations](firmware/cargo_monitor/README.md)
- [FastAPI setup, API contract, and tests](backend/README.md)
- [Complete test suite and live smoke check](TESTING.md)
- [Fetch, Wi-Fi priority, trip rules, and condition states](FEATURES.md)
- [Live ESP32 performance dashboard and delivery coverage](PERFORMANCE.md)

Copy `firmware/cargo_monitor/config.example.h` to `config.h` and set your Wi-Fi,
server URL, and token before compiling. The local config is excluded from Git.
Use the laptop's reachable LAN IP for local uploads, not `localhost`.

## Hardware

Targets the classic ESP32 DevKit / ESP32-WROOM-32:

| Component | Signal pins |
| --- | --- |
| MPU6050 | SDA GPIO21, SCL GPIO22 |
| DHT11 | DATA GPIO4 |
| Reed module | D0 GPIO27; A0 unused |
| Bare LDR with 10k resistor divider | GPIO34 |

See the wiring guide before connecting power. Bare-LDR detection is disabled
by default until the divider is installed and its threshold is configured.

## Prototype status

Firmware has compiled for `esp32:esp32:esp32`. Backend tests cover authentication,
batch validation, transactional storage, persistence, and retry deduplication.
End-to-end hardware validation is ongoing; compilation and backend tests do not
establish sensor accuracy or successful device-to-server communication.

This is experimental rule-based detection, not a trained damage prediction model.
Accelerometer calibration remains incomplete. Flash buffering is finite (up to
256 records), and every restart begins disarmed. See firmware documentation for
overflow, sampling, offline timing, and sensor-fault behavior.

Local development uses HTTP and a sample token. Cloud hosting requires HTTPS,
certificate validation, a private token, and persistent database storage.

## Guided analysis

Open http://localhost:8000/analysis for guided physical trials, independent observations, preserved original logs and evidence export. Follow [ANALYSIS_START_HERE.md](ANALYSIS_START_HERE.md) for preparation and the first pilot; [QUANTITATIVE_ANALYSIS_PLAN.md](QUANTITATIVE_ANALYSIS_PLAN.md) defines the evaluation methodology. No real trial starts automatically.
