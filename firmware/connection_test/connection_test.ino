// First connection test. Reuses Wi-Fi credentials already saved on this ESP32.
#include <WiFi.h>
#include <HTTPClient.h>

const char* HEARTBEAT_URL = "http://10.177.148.254:8000/api/v1/heartbeat";
const char* DEVICE_TOKEN = "dev-token-change-me";
const char* DEVICE_ID = "cargo-esp32-01";
uint32_t lastAttempt = 0;
bool attempted = false;

void setup() {
  Serial.begin(115200);
  delay(1000);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin();
  Serial.println("ESP32 connection test: using saved Wi-Fi credentials.");
}

void loop() {
  if (attempted && millis() - lastAttempt < 5000) { delay(20); return; }
  attempted = true;
  lastAttempt = millis();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Waiting for saved Wi-Fi. If credentials are missing, configure WiFi.begin(ssid, password).");
    WiFi.reconnect();
    return;
  }
  Serial.print("Wi-Fi connected. ESP32 IP: ");
  Serial.println(WiFi.localIP());
  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, HEARTBEAT_URL)) { Serial.println("Invalid server URL"); return; }
  http.setConnectTimeout(1500);
  http.setTimeout(2000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  String payload = String("{\"device_id\":\"") + DEVICE_ID +
      "\",\"firmware\":\"connection-test\",\"ip\":\"" + WiFi.localIP().toString() +
      "\",\"rssi\":" + String(WiFi.RSSI()) + ",\"uptime_ms\":" + String(millis()) + "}";
  const int code = http.POST(payload);
  Serial.printf("HEARTBEAT HTTP=%d%s\n", code, code == 200 ? " : dashboard acknowledged" : " : check server URL, token and firewall");
  http.end();
}
