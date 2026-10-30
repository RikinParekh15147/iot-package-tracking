# Prototype hardware check — 29 October 2026

Classic ESP32 on COM4, ESP32 Arduino core 3.3.8, FastAPI receiver on the local
Wi-Fi network. This is a bench check, not validation for safety-critical cargo.

## Fixes

- Renamed the raw-byte decoder from `word` to `decodeSigned16`. Arduino defines
  `word(...)` as a macro, so the old calls converted register indexes instead
  of decoding the sensor bytes. This caused the near-zero acceleration fault.
- Use the Adafruit MPU6050 initialization path from the working component test,
  with checked I2C burst reads for actual sampling and startup settling time.
- Reject persistently near-zero acceleration for this cargo prototype and avoid
  calculating tilt near zero acceleration.
- Declare transport clients before HTTPClient so they remain alive during its
  destructor. Disable HTTP connection reuse following a network assertion seen
  in an earlier build.
- Add Serial `i` diagnostics and explicit storage initialization messages.

## Observed results

- Firmware compiled, uploaded, and flash hash verified.
- 145-second run: 24 successful heartbeats, 3 acknowledged upload batches,
  one expected startup boot, no observed assertions or unexpected resets.
- MPU acceleration approximately 0.95g while stationary; reference capture
  successful. Raw acceleration, corrected gyro, and relative tilt reached SQLite.
- DHT temperature/humidity readings reached SQLite.
- Flash queue mounted successfully, with zero new drops or storage errors.
- Server-outage test: generated a disarm event while the server was stopped;
  failed upload retained the record. A controlled ESP32 restart recovered that
  pending record. After server restart, the old-boot event and four new-boot
  records uploaded in separate batches; queue drained to zero with zero drops.
- Backend suite: 12 tests passed.

The outage test interrupted the server, not Wi-Fi itself. Long-duration operation,
full accelerometer calibration, physical impact/tilt alert validation, and queue
capacity endurance were not established by this check. LDR alerts remain disabled
until the bare sensor's proper resistor divider is installed. The board is left
disarmed; close the lid/magnet and send `a` for an armed demonstration.
