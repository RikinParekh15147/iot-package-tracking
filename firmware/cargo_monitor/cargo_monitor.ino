/* Cargo monitoring prototype: classic ESP32. See README.md before uploading. */
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <math.h>
#include <time.h>
#include <algorithm>
#include "config.h"
#include "monitor_logic.h"
#include "cargo_console.h"
CargoConsole cargoConsole;
#undef Serial
#define Serial cargoConsole

constexpr int REED_PIN=27, LDR_PIN=34, DHT_PIN=4;
constexpr uint16_t CAPACITY=256, EVENT_RESERVE=32, BATCH_SIZE=25;
constexpr uint32_t UPLOAD_MS=1000;
volatile uint32_t lastServerOkAt=0;
constexpr float G=9.80665f;
DHT dht(DHT_PIN,DHT11);
Adafruit_MPU6050 imuDriver;
uint32_t zeroAccelerationSince=0;
SemaphoreHandle_t queueMutex;
TaskHandle_t networkHandle=nullptr;
bool urgentTelemetry=false;
String lastCondition,lastMovement;
uint32_t queueIds[CAPACITY], queueCount=0, nextFileId=1;
uint32_t droppedRecords=0, storageErrors=0, sequenceNumber=0;
bool fsReady=false;
String bootId;
String activeTripId; // Updated by heartbeat; copied into each queued record at capture time.
String pendingSnapshotId,lastSnapshotId; // Guarded by queueMutex.
struct WifiProfile {String ssid,password;int priority;};
WifiProfile wifiProfiles[8];
unsigned wifiProfileCount=0;
uint32_t wifiRevision=0;
Preferences wifiPreferences;
cargo::VibrationWindow vibration;
cargo::TimedRule vibrationRule,sealRule,lightRiseRule,lightFallRule;
bool lightExposed=false;
cargo::SealState currentSeal=cargo::SealState::LikelyClosed,previousSeal=cargo::SealState::LikelyClosed;
float vibrationRms=0;
bool strongVibration=false;
uint32_t vibrationWindowStart=0,lastImpactAt=0;
bool impactSeen=false;

uint8_t imuAddress=0x68;
bool imuConfigured=false, imuValid=false, calibrated=false, armed=false;
float ax=0,ay=0,az=0,gx=0,gy=0,gz=0,totalG=0,peakG=0,angleDeg=0;
float refX=0,refY=0,refZ=1,refMagnitude=1, biasX=0,biasY=0,biasZ=0;
float temperatureC=NAN,humidity=NAN;
bool dhtValid=false;
uint32_t lastDht=0;
int ldrRaw=0, reedRaw=HIGH,reedStable=HIGH;
uint32_t reedChangedAt=0,lastImu=0,lastTelemetry=0,lastLog=0,lastRecovery=0;
cargo::TimedRule tiltRule,lightRule,environmentRule;
cargo::ImpactRule impactRule;
bool imuFaultReported=false;
uint8_t imuFailureStreak=0;
uint32_t missedSamples=0;

String recordPath(uint32_t id) {
  char path[32]; snprintf(path,sizeof(path),"/r%010lu.json",(unsigned long)id); return String(path);
}
void recoverQueue() {
  // Never auto-format: an ordinary mount failure must not erase cargo history.
  fsReady=LittleFS.begin(false);
  if(!fsReady) { Serial.println("STORAGE UNAVAILABLE: use Serial F once to format; history will be erased."); return; }
  File root=LittleFS.open("/");
  for(File f=root.openNextFile(); f; f=root.openNextFile()) {
    String name=f.name(); if(name.startsWith("/")) name.remove(0,1);
    if(name.length()==16 && name.startsWith("r") && name.endsWith(".json")) {
      uint32_t id=strtoul(name.substring(1,11).c_str(),nullptr,10);
      if(queueCount<CAPACITY) queueIds[queueCount++]=id;
      nextFileId=std::max(nextFileId,id+1);
    }
    f.close();
  }
  root.close();
  std::sort(queueIds,queueIds+queueCount);
  LittleFS.remove("/pending.tmp");
  Serial.printf("Recovered %lu pending records.\n",(unsigned long)queueCount);
}
bool saveRecord(const char* kind, JsonObject data) {
  JsonDocument envelope;
  envelope["boot_id"]=bootId;
  JsonObject record=envelope["record"].to<JsonObject>();
  record["seq"]=++sequenceNumber; record["uptime_ms"]=millis(); record["kind"]=kind;
  record["data"].set(data);
  const bool telemetry=strcmp(kind,"telemetry")==0;
  xSemaphoreTake(queueMutex,portMAX_DELAY);
  if(activeTripId.length()) record["trip_id"]=activeTripId;
  else record["trip_id"]=nullptr;
  if(!cargo::canQueue(fsReady,queueCount,telemetry,CAPACITY,EVENT_RESERVE)) {
    ++droppedRecords; xSemaphoreGive(queueMutex); return false;
  }
  File f=LittleFS.open("/pending.tmp","w");
  const size_t expected=measureJson(envelope);
  const size_t written=f ? serializeJson(envelope,f) : 0;
  if(f) { f.flush(); f.close(); }
  const bool saved=written==expected && LittleFS.rename("/pending.tmp",recordPath(nextFileId));
  if(!saved) {
    ++storageErrors; ++droppedRecords; LittleFS.remove("/pending.tmp");
  } else { queueIds[queueCount++]=nextFileId++; }
  xSemaphoreGive(queueMutex);
  if(saved && networkHandle)xTaskNotifyGive(networkHandle);
  return saved;
}
void event(const char* type,const char* detail,float value=0) {
  JsonDocument doc; doc["type"]=type; doc["detail"]=detail; doc["value"]=value; doc["armed"]=armed;
  saveRecord("event",doc.as<JsonObject>());urgentTelemetry=true;
  Serial.printf("EVENT %s: %s (%.2f)\n",type,detail,value);
}

// Checked burst reads prevent the earlier loose-wire fault being treated as valid motion.
bool readRegisters(uint8_t reg,uint8_t* buffer,size_t n) {
  Wire.beginTransmission(imuAddress); Wire.write(reg);
  if(Wire.endTransmission(false)!=0) return false;
  if(Wire.requestFrom(imuAddress,(uint8_t)n)!=(uint8_t)n) return false;
  for(size_t i=0;i<n;++i) buffer[i]=Wire.read();
  return true;
}
bool writeRegister(uint8_t reg,uint8_t value) {
  Wire.beginTransmission(imuAddress); Wire.write(reg); Wire.write(value);
  return Wire.endTransmission()==0;
}
bool configureImu() {
  // Same initialization library as the original, working component test.
  for(uint8_t address: {uint8_t(0x68),uint8_t(0x69)}) {
    if(!imuDriver.begin(address,&Wire)) continue;
    imuAddress=address;
    imuDriver.setAccelerometerRange(MPU6050_RANGE_16_G);
    imuDriver.setGyroRange(MPU6050_RANGE_500_DEG);
    imuDriver.setFilterBandwidth(MPU6050_BAND_44_HZ);
    imuDriver.setSampleRateDivisor(9);
    delay(150);
    zeroAccelerationSince=0;
    Serial.printf("MPU initialized with Adafruit driver at 0x%02x\n",address);
    return true;
  }
  return false;
}
bool readImu() {
  uint8_t b[14];
  if(!readRegisters(0x3B,b,sizeof(b))) return false;
  ax=cargo::decodeSigned16(b,0)/2048.0f; ay=cargo::decodeSigned16(b,2)/2048.0f; az=cargo::decodeSigned16(b,4)/2048.0f;
  const float toRad=PI/180.0f/65.5f;
  gx=cargo::decodeSigned16(b,8)*toRad; gy=cargo::decodeSigned16(b,10)*toRad; gz=cargo::decodeSigned16(b,12)*toRad;
  totalG=sqrtf(ax*ax+ay*ay+az*az);
  // Near-zero acceleration can be real free fall; it is not an I2C error.
  bool allZero=true;
  for(uint8_t value:b) if(value!=0) { allZero=false;break; }
  if(totalG<0.05f) {
    if(!zeroAccelerationSince) zeroAccelerationSince=millis();
  } else zeroAccelerationSince=0;
  // A short low-g interval can be free fall; persistent zero is a sensor fault.
  const bool stalled=zeroAccelerationSince && millis()-zeroAccelerationSince>500;
  return isfinite(totalG) && !allZero && !stalled;
}
bool calibrateReference() {
  Serial.println("Reference capture: keep the box stationary for 3 seconds.");
  float sx=0,sy=0,sz=0,sgx=0,sgy=0,sgz=0;
  float minA=100,maxA=0,minGX=100,maxGX=-100,minGY=100,maxGY=-100,minGZ=100,maxGZ=-100;
  for(int i=0;i<200;++i) {
    if(!readImu()) { Serial.println("Reference FAILED: MPU communication."); return false; }
    sx+=ax; sy+=ay; sz+=az; sgx+=gx; sgy+=gy; sgz+=gz;
    minA=std::min(minA,totalG);maxA=std::max(maxA,totalG);
    minGX=std::min(minGX,gx);maxGX=std::max(maxGX,gx);
    minGY=std::min(minGY,gy);maxGY=std::max(maxGY,gy);
    minGZ=std::min(minGZ,gz);maxGZ=std::max(maxGZ,gz);
    delay(15);
  }
  if(maxA-minA>0.10f || maxGX-minGX>0.10f || maxGY-minGY>0.10f || maxGZ-minGZ>0.10f) {
    Serial.println("Reference FAILED: movement detected. Rest securely and send c."); return false;
  }
  const float x=sx/200,y=sy/200,z=sz/200;
  refMagnitude=sqrtf(x*x+y*y+z*z);
  if(refMagnitude<0.7f || refMagnitude>1.3f) { Serial.println("Reference FAILED: implausible gravity."); return false; }
  refX=x/refMagnitude;refY=y/refMagnitude;refZ=z/refMagnitude;
  biasX=sgx/200;biasY=sgy/200;biasZ=sgz/200;
  calibrated=true;
  event("reference_captured","Relative tilt reference and gyro bias only; accelerometer remains uncalibrated",refMagnitude);
  return true;
}

bool uploadBatch() {
  JsonDocument batch;
  batch["device_id"]=DEVICE_ID;
  JsonArray records=batch["records"].to<JsonArray>();
  uint32_t ids[BATCH_SIZE]; size_t count=0;
  xSemaphoreTake(queueMutex,portMAX_DELAY);
  for(uint32_t i=0;i<queueCount && count<BATCH_SIZE;++i) {
    File f=LittleFS.open(recordPath(queueIds[i]),"r");
    JsonDocument item;
    DeserializationError err=deserializeJson(item,f); f.close();
    if(err) { Serial.println("QUEUE READ ERROR: retaining records; inspect storage."); break; }
    String itemBoot=item["boot_id"].as<String>();
    if(count && itemBoot!=batch["boot_id"].as<String>()) break;
    batch["boot_id"]=itemBoot;
    records.add(item["record"]);
    ids[count++]=queueIds[i];
  }
  xSemaphoreGive(queueMutex);
  if(!count) return false;
  String payload; serializeJson(batch,payload);
  // Transports must outlive HTTPClient: its destructor still references them.
  WiFiClient plain;
  WiFiClientSecure secure;
  HTTPClient http;
  http.setReuse(false);
  String url=SERVER_URL;
  if(url.startsWith("https://")) {
    if(strlen(ROOT_CA_PEM)==0) { Serial.println("HTTPS requires ROOT_CA_PEM; upload retained."); return false; }
    secure.setCACert(ROOT_CA_PEM);
    if(!http.begin(secure,url)) return false;
  } else {
    if(!url.startsWith("http://") || !http.begin(plain,url)) return false;
  }
  http.setConnectTimeout(2500);http.setTimeout(4000);
  http.addHeader("Content-Type","application/json");http.addHeader("X-Device-Token",DEVICE_TOKEN);
  const int code=http.POST(payload);
  JsonDocument response;
  bool confirmed=false;
  if(code>=200 && code<300) {
    const auto err=deserializeJson(response,http.getString());
    confirmed=!err && response["accepted"].is<bool>() && response["accepted"].as<bool>() &&
              response["received"].as<size_t>()==count;
  }
  http.end();
  if(!confirmed) { Serial.printf("UPLOAD failed/unconfirmed HTTP=%d; keeping %u records\n",code,(unsigned)count); return false; }
  lastServerOkAt=millis();
  xSemaphoreTake(queueMutex,portMAX_DELAY);
  size_t removed=0;
  for(size_t i=0;i<count;++i) {
    if(!LittleFS.remove(recordPath(ids[i]))) { ++storageErrors; break; }
    ++removed;
  }
  for(uint32_t i=removed;i<queueCount;++i) queueIds[i-removed]=queueIds[i];
  queueCount-=removed;
  xSemaphoreGive(queueMutex);
  Serial.printf("UPLOAD acknowledged %u records; removed %u\n",(unsigned)count,(unsigned)removed);
  return removed>0;
}
bool applyWifiConfig(JsonVariantConst config) {
  if(!config.is<JsonObjectConst>() || !config["revision"].is<uint32_t>() || !config["profiles"].is<JsonArrayConst>()) return false;
  const uint32_t revision=config["revision"].as<uint32_t>();
  if(revision==wifiRevision) return true;
  JsonArrayConst profiles=config["profiles"].as<JsonArrayConst>();
  if(profiles.size()>8) return false;
  for(JsonObjectConst profile:profiles) {
    if(!profile["ssid"].is<const char*>() || !profile["password"].is<const char*>() || !profile["priority"].is<int>())return false;
    const size_t ssidLength=strlen(profile["ssid"].as<const char*>()),passwordLength=strlen(profile["password"].as<const char*>());
    if(!ssidLength||ssidLength>32||passwordLength<8||passwordLength>63)return false;
  }
  String serialized;serializeJson(config,serialized);
  if(wifiPreferences.putString("config",serialized)!=serialized.length()) {Serial.println("Wi-Fi config could not be persisted");return false;}
  wifiProfileCount=0;
  for(JsonObjectConst profile:profiles) {
    wifiProfiles[wifiProfileCount++]={profile["ssid"].as<String>(),profile["password"].as<String>(),profile["priority"].as<int>()};
  }
  wifiRevision=revision;
  Serial.printf("Wi-Fi settings applied: revision %lu, %u profiles\n",(unsigned long)revision,wifiProfileCount);
  return true;
}
void loadWifiConfig() {
  wifiPreferences.begin("cargo-wifi",false);
  String saved=wifiPreferences.getString("config","");
  if(saved.length()) {JsonDocument doc;if(!deserializeJson(doc,saved)) applyWifiConfig(doc.as<JsonVariantConst>());}
}
bool sendHeartbeat(uint32_t& retryDelay,bool& uploadAttempted) {
  String url=SERVER_URL;const int suffix=url.indexOf("/api/v1/batches");
  if(suffix<0)return false;
  url=url.substring(0,suffix)+"/api/v1/heartbeat";
  WiFiClient plain;WiFiClientSecure secure;HTTPClient http;http.setReuse(false);
  bool ready=false;
  if(url.startsWith("https://")) {if(strlen(ROOT_CA_PEM)>0){secure.setCACert(ROOT_CA_PEM);ready=http.begin(secure,url);}}
  else if(url.startsWith("http://"))ready=http.begin(plain,url);
  if(!ready)return false;
  http.setConnectTimeout(1500);http.setTimeout(2000);
  http.addHeader("Content-Type","application/json");http.addHeader("X-Device-Token",DEVICE_TOKEN);
  JsonDocument message;message["device_id"]=DEVICE_ID;message["firmware"]="cargo-monitor";
  message["wifi_ssid"]=WiFi.SSID();message["supports_fetch"]=true;message["wifi_revision"]=wifiRevision;
  message["ip"]=WiFi.localIP().toString();message["rssi"]=WiFi.RSSI();message["uptime_ms"]=millis();
  String payload;serializeJson(message,payload);const int code=http.POST(payload);
  bool accepted=false;
  if(code==200) {
    JsonDocument response;
    if(!deserializeJson(response,http.getString()) && response["accepted"]==true) {
      accepted=true;lastServerOkAt=millis();
      if(response["active_trip_id"].isNull()||response["active_trip_id"].is<const char*>()) {
        const String trip=response["active_trip_id"].isNull()?String(""):response["active_trip_id"].as<String>();
        xSemaphoreTake(queueMutex,portMAX_DELAY);
        const bool changed=activeTripId!=trip;activeTripId=trip;
        if(response["telemetry_request_id"].is<const char*>()) {
          const String incoming=response["telemetry_request_id"].as<String>();
          if(incoming.length()&&incoming!=lastSnapshotId&&incoming!=pendingSnapshotId) {pendingSnapshotId=incoming;retryDelay=5000;uploadAttempted=false;}
        }
        xSemaphoreGive(queueMutex);
        cargoConsole.setTrip(trip);
        if(changed)wifiPreferences.putString("trip",trip);
      }
      if(!response["wifi_config"].isNull())applyWifiConfig(response["wifi_config"].as<JsonVariantConst>());
    }
  }
  Serial.printf("HEARTBEAT HTTP=%d\n",code);http.end();return accepted;
}
bool connectPreferredWifi(uint32_t& retryDelay,bool& uploadAttempted,const String& failedSsid) {
  WifiProfile candidates[10];unsigned count=wifiProfileCount;
  for(unsigned i=0;i<count;++i)candidates[i]=wifiProfiles[i];
  // Dashboard profiles take priority; built-in defaults remain recovery options.
  auto addDefault=[&](const char* ssid,const char* password,int priority) {
    if(!ssid[0])return;
    for(unsigned i=0;i<count;++i)if(candidates[i].ssid==ssid)return;
    if(count<10)candidates[count++]={ssid,password,priority};
  };
  addDefault(WIFI_SSID,WIFI_PASSWORD,1000000);
#if defined(WIFI_FALLBACK_SSID) && defined(WIFI_FALLBACK_PASSWORD)
  addDefault(WIFI_FALLBACK_SSID,WIFI_FALLBACK_PASSWORD,1000001);
#endif
  std::sort(candidates,candidates+count,[](const WifiProfile& a,const WifiProfile& b){return a.priority==b.priority?a.ssid<b.ssid:a.priority<b.priority;});
  // Previously failed server route is tried last on this pass, not endlessly first.
  bool available[10]={false};
  WiFi.disconnect(false,false);
  const int networks=WiFi.scanNetworks(false,true);
  for(unsigned i=0;i<count;++i)for(int j=0;j<networks;++j)if(candidates[i].ssid==WiFi.SSID(j))available[i]=true;
  WiFi.scanDelete();
  for(unsigned pass=0;pass<2;++pass)for(unsigned i=0;i<count;++i) {
    if(!available[i] || ((candidates[i].ssid==failedSsid)!=(pass==1)))continue;
    Serial.printf("Trying available Wi-Fi profile %u\n",i+1);
    WiFi.begin(candidates[i].ssid.c_str(),candidates[i].password.c_str());
    const uint32_t started=millis();while(WiFi.status()!=WL_CONNECTED && millis()-started<10000)vTaskDelay(pdMS_TO_TICKS(200));
    if(WiFi.status()==WL_CONNECTED) {
      if(String(SERVER_URL).startsWith("https://") && time(nullptr)<1700000000) {
        configTime(0,0,"pool.ntp.org","time.nist.gov");
        const uint32_t timeStarted=millis();
        while(time(nullptr)<1700000000 && millis()-timeStarted<10000)vTaskDelay(pdMS_TO_TICKS(200));
      }
      if(sendHeartbeat(retryDelay,uploadAttempted))return true;
    }
    WiFi.disconnect(false,false);vTaskDelay(pdMS_TO_TICKS(100));
  }
  return false;
}
bool uploadConsole() {
  cargo::ConsoleLine lines[16];uint32_t dropped=0;
  const size_t count=cargoConsole.snapshot(lines,16,dropped);if(!count)return true;
  String url=SERVER_URL;const int suffix=url.indexOf("/api/v1/batches");if(suffix<0)return false;
  url=url.substring(0,suffix)+"/api/v1/console";
  WiFiClient plain;WiFiClientSecure secure;HTTPClient http;http.setReuse(false);
  bool ready=false;
  if(url.startsWith("https://")){if(strlen(ROOT_CA_PEM)>0){secure.setCACert(ROOT_CA_PEM);ready=http.begin(secure,url);}}
  else if(url.startsWith("http://"))ready=http.begin(plain,url);
  if(!ready)return false;
  http.setConnectTimeout(1500);http.setTimeout(2000);
  http.addHeader("Content-Type","application/json");http.addHeader("X-Device-Token",DEVICE_TOKEN);
  JsonDocument payload;payload["device_id"]=DEVICE_ID;payload["boot_id"]=bootId;payload["dropped_lines"]=dropped;
  JsonArray array=payload["lines"].to<JsonArray>();
  for(size_t i=0;i<count;++i){JsonObject row=array.add<JsonObject>();row["seq"]=lines[i].seq;row["uptime_ms"]=lines[i].uptime;row["message"]=lines[i].message;if(lines[i].trip[0])row["trip_id"]=lines[i].trip;}
  String body;serializeJson(payload,body);const int code=http.POST(body);
  bool accepted=false;
  if(code==200){JsonDocument response;if(!deserializeJson(response,http.getString()))accepted=response["accepted"]==true&&response["received"].as<size_t>()==count;}
  http.end();if(accepted)cargoConsole.acknowledge(lines[count-1].seq);return accepted;
}
void networkTask(void*) {
  uint32_t retryDelay=5000,lastHeartbeat=0,lastUploadAttempt=0,lastConsoleUpload=0;
  bool uploadAttempted=false;
  unsigned serverFailures=0;
  String failedSsid;
  loadWifiConfig();WiFi.mode(WIFI_STA);WiFi.setAutoReconnect(false);
  configTime(0,0,"pool.ntp.org","time.nist.gov");
  for(;;) {
    if(!cargo::keepWifi(WiFi.status()==WL_CONNECTED,serverFailures)) {
      if(WiFi.status()==WL_CONNECTED)failedSsid=WiFi.SSID();
      if(!connectPreferredWifi(retryDelay,uploadAttempted,failedSsid)) {
        Serial.println("No saved Wi-Fi can reach server; continuing offline queue");vTaskDelay(pdMS_TO_TICKS(10000));continue;
      }
      serverFailures=0;failedSsid="";lastHeartbeat=millis();
    }
    if(millis()-lastHeartbeat>=1000) {
      lastHeartbeat=millis();
      if(sendHeartbeat(retryDelay,uploadAttempted))serverFailures=0;else ++serverFailures;
      if(serverFailures>=3)continue;
    }
    xSemaphoreTake(queueMutex,portMAX_DELAY);
    const bool pending=queueCount>0,snapshotWaiting=pendingSnapshotId.length()>0;
    xSemaphoreGive(queueMutex);
    if(!pending && millis()-lastConsoleUpload>=2000){lastConsoleUpload=millis();uploadConsole();}
    if(!pending){retryDelay=5000;ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(snapshotWaiting?100:UPLOAD_MS));continue;}
    if(uploadAttempted && millis()-lastUploadAttempt<retryDelay){vTaskDelay(pdMS_TO_TICKS(250));continue;}
    lastUploadAttempt=millis();uploadAttempted=true;
    if(uploadBatch()){retryDelay=5000;uploadAttempted=false;taskYIELD();}
    else{retryDelay=std::min(retryDelay*2,uint32_t(60000));vTaskDelay(pdMS_TO_TICKS(250));}
  }
}
void updateTimedRule(bool condition,uint32_t now,uint32_t duration,cargo::TimedRule& rule,
                     const char* type,const char* detail,float value) {
  if(rule.update(condition,now,duration)) event(type,detail,value);
}
const char* movementLabel(){return !imuValid?"unknown":strongVibration?"strong_vibration":vibrationRms>0.05f?"moving":"still";}
const char* conditionLabel(uint32_t now){return !imuValid?"motion_sensor_unavailable":impactSeen&&uint32_t(now-lastImpactAt)<15000?"recent_impact":strongVibration?"strong_vibration":!calibrated?"orientation_uncalibrated":environmentRule.active?"unsafe_environment":tiltRule.active?"tilted":"normal";}
bool telemetry(uint32_t now,const String& requestId="") {
  JsonDocument doc;
  if(requestId.length()) doc["fetch_request_id"]=requestId;
  doc["armed"]=armed;doc["lid_open"]=reedStable!=LOW;
  doc["seal_state"]=cargo::sealLabel(currentSeal);
  doc["seal_evidence"]=!LDR_ENABLED?"Reed only; light sensor disabled":(reedStable!=LOW?(lightExposed?"Reed open and internal light":"Reed open; no bright light"): (lightExposed?"Internal light despite reed closed":"Reed closed and dark"));
  doc["vibration_rms_g"]=vibrationRms;
  doc["movement_state"]=movementLabel();
  doc["condition_state"]=conditionLabel(now);
  doc["wifi_ssid"]=WiFi.SSID();doc["wifi_rssi"]=WiFi.RSSI();doc["wifi_revision"]=wifiRevision;
  doc["ldr_enabled"]=LDR_ENABLED;
  if(LDR_ENABLED){doc["ldr_raw"]=ldrRaw;doc["light_exposed"]=lightExposed;doc["ldr_threshold"]=LIGHT_THRESHOLD;}
  doc["dht_ok"]=dhtValid;
  if(dhtValid) { doc["temperature_c"]=temperatureC;doc["humidity_pct"]=humidity; }
  doc["dht_age_ms"]=now-lastDht;
  doc["imu_ok"]=imuValid;doc["reference_valid"]=calibrated;
  if(imuValid) {
    doc["ax_g"]=ax;doc["ay_g"]=ay;doc["az_g"]=az;doc["raw_gx_rad_s"]=gx;doc["raw_gy_rad_s"]=gy;doc["raw_gz_rad_s"]=gz;doc["total_g"]=totalG;doc["peak_g"]=peakG;
    if(calibrated) { doc["tilt_deg"]=angleDeg;doc["gx_rad_s"]=gx-biasX;doc["gy_rad_s"]=gy-biasY;doc["gz_rad_s"]=gz-biasZ; }
  }
  xSemaphoreTake(queueMutex,portMAX_DELAY);
  doc["queued"]=queueCount;doc["dropped_records"]=droppedRecords;doc["storage_errors"]=storageErrors;
  xSemaphoreGive(queueMutex);
  doc["sample_gap_count"]=missedSamples;doc["wifi_connected"]=WiFi.status()==WL_CONNECTED;
  const bool saved=saveRecord("telemetry",doc.as<JsonObject>());
  if(saved) peakG=0;
  return saved;
}
void setup() {
  Serial.begin(115200);delay(1000);
  Serial.println("\nCARGO MONITOR | a=arm d=disarm c=reference F=format storage (erases queued history)");
  bootId=String((uint32_t)(ESP.getEfuseMac()>>32),HEX)+String(esp_random(),HEX)+String(esp_random(),HEX);
  queueMutex=xSemaphoreCreateMutex();
  if(!queueMutex) { Serial.println("FATAL: mutex allocation");while(true)delay(1000); }
  recoverQueue();
  {Preferences context;context.begin("cargo-wifi",true);activeTripId=context.getString("trip","");context.end();cargoConsole.setTrip(activeTripId);}
  pinMode(REED_PIN,INPUT_PULLUP);reedStable=reedRaw=digitalRead(REED_PIN);
  analogReadResolution(12);dht.begin();
  Wire.begin(21,22);Wire.setClock(100000);Wire.setTimeOut(20);
  imuConfigured=configureImu();
  if(imuConfigured) calibrateReference();
  event("boot","Monitoring starts DISARMED; send a after closing box");
  Serial.println("LDR detection disabled unless enabled in config.h. Use 2.4 GHz hotspot.");
  if(xTaskCreate(networkTask,"network",20480,nullptr,1,&networkHandle)!=pdPASS) {
    event("network_task_failed","Local monitoring only; task allocation failed");
  }
}
void loop() {
  uint32_t now=millis();
  while(Serial.available()) {
    const char cmd=Serial.read();
    if(cmd=='a') {
      if(!cargo::canArm(reedStable==LOW,calibrated,imuValid)) Serial.println("ARM refused: close lid and capture valid stationary reference with c.");
      else { armed=true;tiltRule.reset();lightRule.reset();environmentRule.reset();vibrationRule.reset();sealRule.reset();event("armed","Cargo protection armed"); }
    } else if(cmd=='d') { armed=false;event("disarmed","Cargo protection disarmed"); }
    else if(cmd=='i') {
      Serial.println("I2C diagnostic (SDA21/SCL22):");
      uint8_t raw[14];
      if(readRegisters(0x3B,raw,14)) {
        Serial.print("  Raw accel/temp/gyro:");
        for(uint8_t value:raw) Serial.printf(" %02x",value);
        Serial.println();
      }
      for(uint8_t a=1;a<127;++a) {
        Wire.beginTransmission(a);
        if(Wire.endTransmission()==0) Serial.printf("  ACK 0x%02x\n",a);
      }
      for(uint8_t reg: {uint8_t(0x75),uint8_t(0x6B),uint8_t(0x6C),uint8_t(0x1C),uint8_t(0x1B)}) {
        uint8_t v=0;
        if(readRegisters(reg,&v,1)) Serial.printf("  MPU reg 0x%02x = 0x%02x\n",reg,v);
        else Serial.printf("  MPU reg 0x%02x READ FAILED\n",reg);
      }
    }
    else if(cmd=='c') {
      if(armed) Serial.println("Disarm before reference capture.");
      else { calibrated=false;calibrateReference(); }
    } else if(cmd=='F') {
      xSemaphoreTake(queueMutex,portMAX_DELAY);
      if(!fsReady) {
        fsReady=LittleFS.format() && LittleFS.begin(false);queueCount=0;nextFileId=1;
        Serial.println(fsReady ? "STORAGE READY: flash queue initialized" : "STORAGE ERROR: initialization failed");
      }
      else Serial.println("Storage already mounted; format refused to protect queued history.");
      xSemaphoreGive(queueMutex);
    }
  }
  now=millis();
  const int reed=digitalRead(REED_PIN);
  if(reed!=reedRaw) { reedRaw=reed;reedChangedAt=now; }
  if(reedStable!=reedRaw && now-reedChangedAt>=100) {
    reedStable=reedRaw;
    event(reedStable==LOW ? "lid_closed" : (armed ? "suspected_tamper" : "lid_opened"),
          reedStable==LOW ? "Magnet detected" : "Reed indicates lid opening");
  }
  if(now-lastImu>=10) {
    if(lastImu && now-lastImu>30) ++missedSamples;
    lastImu=now;
    imuValid=imuConfigured && readImu();
    if(!imuValid) {
      if(imuFailureStreak<3) ++imuFailureStreak;
      if(imuFailureStreak>=3) {
      if(!imuFaultReported) { event("imu_fault","MPU read failed; motion detection unavailable");imuFaultReported=true; }
      imuConfigured=false;calibrated=false;tiltRule.reset();vibrationRule.reset();strongVibration=false;vibration=cargo::VibrationWindow();
      }
    } else {
      imuFailureStreak=0;
      if(imuFaultReported) { event("imu_recovered","Capture reference again with c before arming");imuFaultReported=false; }
      peakG=std::max(peakG,totalG);
      vibration.push(ax,ay,az);
      if(!vibrationWindowStart) vibrationWindowStart=now;
      if(now-vibrationWindowStart>=1000) {
        vibrationRms=vibration.rms();
        strongVibration=vibration.samples>=50 && vibration.repetitive() && vibrationRms>0.25f;
        updateTimedRule(armed&&strongVibration,now,1000,vibrationRule,"strong_vibration","Repeated strong vibration; inspect cargo restraints",vibrationRms);
        vibration.resetWindow();vibrationWindowStart=now;
      }
      if(calibrated && totalG>0.05f) {
        angleDeg=cargo::tiltDegrees(ax,ay,az,refX,refY,refZ);
      }
      if(impactRule.update(armed,totalG,IMPACT_G,now)) {lastImpactAt=now;impactSeen=true;event("impact","Sampled total acceleration above threshold; uncalibrated g",totalG);
      }
      const float rate=sqrtf(powf(gx-biasX,2)+powf(gy-biasY,2)+powf(gz-biasZ,2));
      const bool stable=cargo::stableMotion(calibrated,totalG,refMagnitude,rate);
      updateTimedRule(armed && stable && angleDeg>TILT_DEGREES,now,3000,tiltRule,
                      "unsafe_tilt","Sustained tilt from reference",angleDeg);
    }
  }
  if(!imuConfigured && now-lastRecovery>=5000) { lastRecovery=now;imuConfigured=configureImu(); }
  if(now-lastDht>=2500) {
    String snapshotId;
    xSemaphoreTake(queueMutex,portMAX_DELAY);snapshotId=pendingSnapshotId;xSemaphoreGive(queueMutex);
    { // Read at the existing 2.5-second sensor-safe cadence, including fetches.
      const bool wasValid=dhtValid;
      lastDht=now;humidity=dht.readHumidity();temperatureC=dht.readTemperature();
      dhtValid=isfinite(temperatureC) && isfinite(humidity);
      if(!dhtValid && wasValid) event("dht_fault","Temperature/humidity unavailable");
      if(dhtValid && !wasValid) event("dht_ready","Temperature/humidity readings available");
      if(!dhtValid) environmentRule.reset();
      if(snapshotId.length() && telemetry(now,snapshotId)) {
        xSemaphoreTake(queueMutex,portMAX_DELAY);
        lastSnapshotId=snapshotId;
        if(pendingSnapshotId==snapshotId) pendingSnapshotId="";
        xSemaphoreGive(queueMutex);
        lastTelemetry=now;
        Serial.printf("FETCH captured fresh snapshot %s\n",snapshotId.c_str());
      }
    }
  }
  if(LDR_ENABLED) {
    ldrRaw=analogRead(LDR_PIN);
    if(lightRiseRule.update(ldrRaw>LIGHT_THRESHOLD,now,250))lightExposed=true;
    if(lightFallRule.update(ldrRaw<LIGHT_THRESHOLD-150,now,250))lightExposed=false;
    updateTimedRule(armed && lightExposed,now,1000,lightRule,
                    "unexpected_light","Internal light above configured threshold",ldrRaw);
  }
  currentSeal=cargo::sealState(reedStable!=LOW,LDR_ENABLED,lightExposed);
  if(currentSeal!=previousSeal) {sealRule.reset();previousSeal=currentSeal;urgentTelemetry=true;}
  const bool sealWarning=currentSeal==cargo::SealState::LikelyOpened||currentSeal==cargo::SealState::PossibleSeparation;
  updateTimedRule(armed&&sealWarning,now,1000,sealRule,
    currentSeal==cargo::SealState::LikelyOpened?"likely_opened":"packaging_separation",
    currentSeal==cargo::SealState::LikelyOpened?"Reed opening and light exposure agree":"Light exposure while reed stays closed; possible packaging gap",ldrRaw);
  updateTimedRule(armed && dhtValid && (temperatureC<TEMP_MIN_C || temperatureC>TEMP_MAX_C || humidity>HUMIDITY_MAX),
                  now,15000,environmentRule,"environment_warning","Environmental limit exceeded",temperatureC);
  const String movement=movementLabel(),condition=conditionLabel(now);
  if(movement!=lastMovement || condition!=lastCondition) {lastMovement=movement;lastCondition=condition;urgentTelemetry=true;}
  const uint32_t interval=cargo::telemetryInterval(cargo::serverRecentlyReachable(WiFi.status()==WL_CONNECTED,now,lastServerOkAt));
  if(urgentTelemetry || !lastTelemetry || now-lastTelemetry>=interval) {urgentTelemetry=false;lastTelemetry=now;telemetry(now);}
  if(now-lastLog>=1000) {
    lastLog=now;
    uint32_t pending,dropped;
    xSemaphoreTake(queueMutex,portMAX_DELAY);pending=queueCount;dropped=droppedRecords;xSemaphoreGive(queueMutex);
    Serial.printf("[%lu] %s WiFi=%s queue=%lu dropped=%lu IMU=%s total=%.2fg peak=%.2fg tilt=%.1f ref=%s lid=%s T=%.1f H=%.1f\n",
      (unsigned long)now,armed?"ARMED":"DISARMED",WiFi.status()==WL_CONNECTED?"ON":"OFF",
      (unsigned long)pending,(unsigned long)dropped,imuValid?"OK":"FAULT",totalG,peakG,angleDeg,
      calibrated?"YES":"NO",reedStable==LOW?"CLOSED":"OPEN",temperatureC,humidity);
  }
  delay(1);
}
