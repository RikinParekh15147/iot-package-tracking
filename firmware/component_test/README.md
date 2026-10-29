# All-component Serial Monitor test

Target: classic ESP32 DevKit / ESP32-WROOM-32 (not ESP32-C3/S3).
This is a hardware bring-up sketch; no Wi-Fi or backend is needed.

## Connections

Unplug USB before wiring. All grounds must connect together. Power the
sensor modules from 3V3 for this test; never feed a 5V output into an ESP32 GPIO.
Check the actual printed pin labels: module pin orders vary.

| Component pin | ESP32 pin |
| --- | --- |
| MPU6050 breakout VCC | 3V3 |
| MPU6050 GND | GND |
| MPU6050 SDA | GPIO21 |
| MPU6050 SCL | GPIO22 |
| MPU6050 AD0 | GND (address 0x68); code also tries 0x69 |
| MPU6050 INT / XDA / XCL | Unconnected |
| DHT11 VCC / + | 3V3 |
| DHT11 DATA / S / OUT | GPIO4 |
| DHT11 GND / - | GND |
| Reed module + | 3V3 |
| Reed module - | GND |
| Reed module D0 / DO | GPIO27 |
| Reed module A0 / AO | Unconnected |
| LDR module VCC / + | 3V3 |
| LDR module GND / - | GND |
| LDR module AO / A0 | GPIO34 |
| LDR module DO / D0 | Unconnected |

### Bare DHT11 (four pins)

With the vented front facing you and pins pointing down, the usual pin order
is VCC, DATA, unused, GND. Verify your sensor's datasheet. Add a 10k ohm resistor
between DATA and 3V3. Three-pin modules usually already contain this pull-up;
follow their printed labels rather than the four-pin order.

### Four-pin reed module (your hardware)

Connect + to 3V3, - to GND, and D0 to GPIO27. Leave A0 disconnected.
The sketch already reads this digital output; no code change is needed.
Place the magnet near the reed to represent a closed lid. Watch the logged
pin=0 / pin=1 value while moving the magnet. If magnet-near reads OPEN,
change REED_CLOSED_LEVEL from LOW to HIGH and upload again.
If the module has an adjustment potentiometer and D0 never changes, adjust
it gradually while moving the magnet. Keep the module powered at 3.3V so
its output remains compatible with the ESP32; do not connect a 5V D0 signal.

For a future bare reed switch, connect its two contacts between GPIO27 and
GND; the sketch enables the ESP32's internal pull-up.

### Bare LDR instead of an analog module

Use a 10k ohm resistor to make a voltage divider:

```text
3V3 ---- LDR ----+---- GPIO34
                |
               10k
                |
               GND
```

With this divider, more light normally means a higher ADC reading. Module
direction can vary. GPIO34 has no internal pull resistor: do not connect a
bare LDR alone. A DO-only module provides only a light/dark threshold; use
the bare-LDR divider to get an analog value with this sketch.

## Upload

1. Install Arduino IDE and the **esp32 by Espressif Systems** board package.
2. In Library Manager install **Adafruit MPU6050**, **DHT sensor library by
   Adafruit**, **Adafruit Unified Sensor**, and **Adafruit BusIO**. Accept dependencies.
3. Open `component_test.ino`; select your classic ESP32 board (typically
   **ESP32 Dev Module**) and its USB port.
4. Upload, then open Serial Monitor at **115200 baud**. Press RESET/EN if needed.

## Expected behavior

- One combined status line every 500 ms; DHT values refresh every 2.5 seconds.
- Move the magnet: CLOSED/OPEN changes, with an extra reed-change line.
- Cover/uncover the LDR: raw value changes. This value is not calibrated lux.
- Leave MPU flat: total acceleration is roughly 1g, gyro near zero.
- Rotate the MPU: acceleration shifts between axes and gyro changes.
- Tap the mounting surface gently: interval peak acceleration rises. Do not
  drop equipment for this test. This polling test can miss very short impacts.
- Warm the DHT gently with a hand nearby: temperature/humidity should change
  slowly. Do not wet the sensor or use a heat gun.

Illustrative output (numbers will vary):

```text
[3000 ms] DHT: 27.0 C, 54.0 %RH (age 500 ms) | Lid: CLOSED (pin=0) | LDR: 850/4095 | Accel[g]: 0.01,0.02,1.00 | total=1.00g peak=1.01g | Gyro[rad/s]: 0.00,0.01,0.00
```

If MPU is missing, inspect SDA/SCL and power, then reset. Other sensors continue
logging. DHT READ ERROR usually indicates wiring, pull-up, or supply trouble.
A constant LDR value of 0 or 4095 suggests a wiring/divider issue or saturation.
An unconnected reed input reads OPEN, so exercise it with a magnet to confirm it.
Firmware/USB output has not been verified on physical hardware in this workspace.

## Reference documentation

- https://learn.adafruit.com/mpu6050-6-dof-accelerometer-and-gyro/arduino
- https://github.com/adafruit/DHT-sensor-library/tree/master/examples/DHTtester
- https://documentation.espressif.com/esp32_datasheet_en.pdf
