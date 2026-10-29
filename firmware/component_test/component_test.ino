// Classic ESP32 DevKit / ESP32-WROOM-32. See README.md for wiring.
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <math.h>

constexpr uint8_t SDA_PIN = 21, SCL_PIN = 22;
constexpr uint8_t DHT_PIN = 4, REED_PIN = 27, LDR_PIN = 34;
// Four-pin reed module: +=3V3, -=GND, D0=GPIO27, A0 unconnected.
// Default assumes magnet nearby gives LOW. Change to HIGH if reversed.
constexpr uint8_t REED_CLOSED_LEVEL = LOW;
constexpr float GRAVITY = 9.80665f;

Adafruit_MPU6050 mpu;
DHT dht(DHT_PIN, DHT11);
bool mpuReady = false, imuSampleValid = false;
bool dhtAttempted = false, dhtValid = false;
float temperatureC = NAN, humidity = NAN;
sensors_event_t accel, gyro, imuTemperature;
float magnitudeG = 0, peakG = 0;
uint32_t lastImu = 0, lastDht = 0, lastLog = 0;
int reedRaw = HIGH, reedStable = HIGH;
uint32_t reedChangedAt = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(LDR_PIN, INPUT);
  analogReadResolution(12);
  reedRaw = reedStable = digitalRead(REED_PIN);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setTimeOut(50);
  dht.begin();

  Serial.println("\nCARGO: ALL-COMPONENT TEST | 115200 baud");
  Serial.println("Pins: MPU SDA=21 SCL=22 | DHT=4 | REED=27 | LDR AO=34");
  mpuReady = mpu.begin(0x68, &Wire);
  if (!mpuReady) mpuReady = mpu.begin(0x69, &Wire);
  if (mpuReady) {
    mpu.setAccelerometerRange(MPU6050_RANGE_16_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_44_HZ);
    Serial.println("MPU6050: detected");
  } else {
    Serial.println("MPU6050: NOT FOUND at 0x68/0x69. Other sensors will still run.");
    Serial.println("Check wiring/power, then press RESET to retry.");
  }
  Serial.println("DHT11: first reading after 2.5 seconds");
  Serial.println("LDR is raw ADC (0-4095), not lux. Reed assumes magnet = lid closed.");
}

void loop() {
  const uint32_t now = millis();
  const int currentReed = digitalRead(REED_PIN);
  if (currentReed != reedRaw) {
    reedRaw = currentReed;
    reedChangedAt = now;
  }
  if (reedRaw != reedStable && now - reedChangedAt >= 40) {
    reedStable = reedRaw;
    Serial.printf("[%lu ms] REED CHANGE: %s\n", (unsigned long)now,
                  reedStable == REED_CLOSED_LEVEL ? "CLOSED / magnet near" : "OPEN / magnet away");
  }

  if (mpuReady && now - lastImu >= 10) {
    lastImu = now;
    imuSampleValid = mpu.getEvent(&accel, &gyro, &imuTemperature);
    if (imuSampleValid) {
      const float x = accel.acceleration.x, y = accel.acceleration.y, z = accel.acceleration.z;
      magnitudeG = sqrtf(x*x + y*y + z*z) / GRAVITY;
      if (magnitudeG > peakG) peakG = magnitudeG;
    }
  }

  if (now - lastDht >= 2500) {
    lastDht = now;
    dhtAttempted = true;
    humidity = dht.readHumidity();
    temperatureC = dht.readTemperature();
    dhtValid = !isnan(humidity) && !isnan(temperatureC);
  }

  if (now - lastLog >= 500) {
    lastLog = now;
    uint32_t ldrSum = 0;
    for (int i = 0; i < 8; ++i) ldrSum += analogRead(LDR_PIN);
    const unsigned int ldrRaw = ldrSum / 8;
    Serial.printf("[%lu ms] ", (unsigned long)now);
    if (dhtValid) {
      Serial.printf("DHT: %.1f C, %.1f %%RH (age %lu ms) | ",
                    temperatureC, humidity, (unsigned long)(now - lastDht));
    } else {
      Serial.print(dhtAttempted ? "DHT: READ ERROR | " : "DHT: waiting | ");
    }
    Serial.printf("Lid: %s (pin=%d) | LDR: %u/4095 | ",
                  reedStable == REED_CLOSED_LEVEL ? "CLOSED" : "OPEN", reedStable, ldrRaw);
    if (mpuReady && imuSampleValid) {
      Serial.printf("Accel[g]: %.2f,%.2f,%.2f | total=%.2fg peak=%.2fg | Gyro[rad/s]: %.2f,%.2f,%.2f\n",
                    accel.acceleration.x / GRAVITY, accel.acceleration.y / GRAVITY,
                    accel.acceleration.z / GRAVITY, magnitudeG, peakG,
                    gyro.gyro.x, gyro.gyro.y, gyro.gyro.z);
    } else {
      Serial.println(mpuReady ? "MPU: READ ERROR" : "MPU: NOT FOUND");
    }
    peakG = 0; // Highest sampled acceleration since the previous log line.
  }
  delay(1);
}
