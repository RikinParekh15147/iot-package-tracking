#pragma once
#define WIFI_SSID "YOUR_2_4_GHZ_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
// Optional second default network; actual credentials belong in ignored config.h.
#define WIFI_FALLBACK_SSID ""
#define WIFI_FALLBACK_PASSWORD ""
#define SERVER_URL "http://192.168.1.10:8000/api/v1/batches"
#define DEVICE_TOKEN "dev-token-change-me"
#define DEVICE_ID "cargo-esp32-01"
// HTTPS requires the server's root CA certificate here; never uses setInsecure().
#define ROOT_CA_PEM ""
// Enable only after wiring the bare LDR with a proper 10k resistor divider.
#define LDR_ENABLED false
// Starting experimental thresholds; tune for your cargo and enclosure.
#define IMPACT_G 3.0f
#define TILT_DEGREES 45.0f
#define LIGHT_THRESHOLD 2500
#define TEMP_MIN_C 10.0f
#define TEMP_MAX_C 35.0f
#define HUMIDITY_MAX 75.0f
