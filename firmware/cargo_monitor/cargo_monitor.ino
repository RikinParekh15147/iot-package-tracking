/* Cargo monitoring prototype: classic ESP32. See README.md before uploading. */
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <LittleFS.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <math.h>
#include <algorithm>
#include "config.h"

constexpr int REED_PIN=27, LDR_PIN=34, DHT_PIN=4;
constexpr uint16_t CAPACITY=256, EVENT_RESERVE=32, BATCH_SIZE=25;
constexpr uint32_t TELEMETRY_MS=60000, UPLOAD_MS=5000;
constexpr float G=9.80665f;
DHT dht(DHT_PIN,DHT11);
SemaphoreHandle_t queueMutex;
uint32_t queueIds[CAPACITY], queueCount=0, nextFileId=1;
uint32_t droppedRecords=0, storageErrors=0, sequenceNumber=0;
bool fsReady=false;
String bootId;
String activeTripId; // Updated by heartbeat; copied into each queued record at capture time.
uint8_t imuAddress=0x68;
bool imuConfigured=false, imuValid=false, calibrated=false, armed=false;
float ax=0,ay=0,az=0,gx=0,gy=0,gz=0,totalG=0,peakG=0,angleDeg=0;
float refX=0,refY=0,refZ=1,refMagnitude=1, biasX=0,biasY=0,biasZ=0;
float temperatureC=NAN,humidity=NAN;
bool dhtValid=false;
uint32_t lastDht=0;
int ldrRaw=0, reedRaw=HIGH,reedStable=HIGH;
uint32_t reedChangedAt=0,lastImu=0,lastTelemetry=0,lastLog=0,lastRecovery=0;
uint32_t tiltSince=0,lightSince=0,environmentSince=0,lastImpact=0;
bool tiltActive=false,lightActive=false,environmentActive=false,imuFaultReported=false;
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
void saveRecord(const char* kind, JsonObject data) {
  JsonDocument envelope;
  envelope["boot_id"]=bootId;
  JsonObject record=envelope["record"].to<JsonObject>();
  record["seq"]=++sequenceNumber; record["uptime_ms"]=millis(); record["kind"]=kind;
  record["data"].set(data);
  const bool telemetry=strcmp(kind,"telemetry")==0;
  xSemaphoreTake(queueMutex,portMAX_DELAY);
  if(activeTripId.length()) record["trip_id"]=activeTripId;
  else record["trip_id"]=nullptr;
  if(!fsReady || queueCount>=(telemetry ? CAPACITY-EVENT_RESERVE : CAPACITY)) {
    ++droppedRecords; xSemaphoreGive(queueMutex); return;
  }
  File f=LittleFS.open("/pending.tmp","w");
  const size_t expected=measureJson(envelope);
  const size_t written=f ? serializeJson(envelope,f) : 0;
  if(f) { f.flush(); f.close(); }
  if(written!=expected || !LittleFS.rename("/pending.tmp",recordPath(nextFileId))) {
    ++storageErrors; ++droppedRecords; LittleFS.remove("/pending.tmp");
  } else { queueIds[queueCount++]=nextFileId++; }
  xSemaphoreGive(queueMutex);
}
void event(const char* type,const char* detail,float value=0) {
  JsonDocument doc; doc["type"]=type; doc["detail"]=detail; doc["value"]=value; doc["armed"]=armed;
  saveRecord("event",doc.as<JsonObject>());
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
  uint8_t identity=0;
  for(uint8_t address: {uint8_t(0x68),uint8_t(0x69)}) {
    imuAddress=address;
    if(readRegisters(0x75,&identity,1) && identity==0x68) {
      // PLL clock, 44Hz DLPF, 100Hz sample rate, +/-16g, +/-500deg/s.
      if(!writeRegister(0x6B,0x01)) continue;
      delay(100);
      if(writeRegister(0x1A,3) && writeRegister(0x19,9) &&
         writeRegister(0x1C,0x18) && writeRegister(0x1B,0x08)) return true;
    }
  }
  return false;
}
bool readImu() {
  uint8_t b[14];
  if(!readRegisters(0x3B,b,sizeof(b))) return false;
  auto word=[&](int i)->int16_t { return (int16_t)((uint16_t(b[i])<<8)|b[i+1]); };
  ax=word(0)/2048.0f; ay=word(2)/2048.0f; az=word(4)/2048.0f;
  const float toRad=PI/180.0f/65.5f;
  gx=word(8)*toRad; gy=word(10)*toRad; gz=word(12)*toRad;
  totalG=sqrtf(ax*ax+ay*ay+az*az);
  return isfinite(totalG) && totalG>0.01f;
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
  HTTPClient http;
  WiFiClient plain;
  WiFiClientSecure secure;
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
void networkTask(void*) {
  uint32_t retryDelay=5000;
  uint32_t lastHeartbeat=0;
  bool heartbeatSent=false;
  uint32_t lastUploadAttempt=0;
  bool uploadAttempted=false;
  WiFi.mode(WIFI_STA);WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
  configTime(0,0,"pool.ntp.org","time.nist.gov");
  for(;;) {
    if(WiFi.status()!=WL_CONNECTED) {
      Serial.println("Wi-Fi unavailable: continuing local monitoring and flash queue.");
      WiFi.reconnect();vTaskDelay(pdMS_TO_TICKS(10000));continue;
    }
    if(!heartbeatSent || millis()-lastHeartbeat>=5000) {
      lastHeartbeat=millis();heartbeatSent=true;
      String heartbeatUrl=SERVER_URL;
      const int suffix=heartbeatUrl.indexOf("/api/v1/batches");
      if(suffix>=0) {
        heartbeatUrl=heartbeatUrl.substring(0,suffix)+"/api/v1/heartbeat";
        WiFiClient plain;WiFiClientSecure secure;HTTPClient http;
        bool ready=false;
        if(heartbeatUrl.startsWith("https://")) {
          if(strlen(ROOT_CA_PEM)>0) { secure.setCACert(ROOT_CA_PEM);ready=http.begin(secure,heartbeatUrl); }
        } else if(heartbeatUrl.startsWith("http://")) ready=http.begin(plain,heartbeatUrl);
        if(ready) {
          http.setConnectTimeout(1500);http.setTimeout(1500);
          http.addHeader("Content-Type","application/json");http.addHeader("X-Device-Token",DEVICE_TOKEN);
          JsonDocument message;message["device_id"]=DEVICE_ID;
          message["firmware"]="cargo-monitor";message["ip"]=WiFi.localIP().toString();
          message["rssi"]=WiFi.RSSI();message["uptime_ms"]=millis();
          String payload;serializeJson(message,payload);
          const int heartbeatCode=http.POST(payload);
          if(heartbeatCode==200) {
            JsonDocument response;
            if(!deserializeJson(response,http.getString()) && response["accepted"]==true &&
               (response["active_trip_id"].isNull() || response["active_trip_id"].is<const char*>())) {
              xSemaphoreTake(queueMutex,portMAX_DELAY);
              activeTripId=response["active_trip_id"].isNull() ? String("") : response["active_trip_id"].as<String>();
              xSemaphoreGive(queueMutex);
            }
          }
          Serial.printf("HEARTBEAT HTTP=%d\n",heartbeatCode);http.end();
        }
      }
    }
    xSemaphoreTake(queueMutex,portMAX_DELAY);
    const bool pending=queueCount>0;
    xSemaphoreGive(queueMutex);
    if(!pending) { retryDelay=5000;vTaskDelay(pdMS_TO_TICKS(UPLOAD_MS));continue; }
    if(uploadAttempted && millis()-lastUploadAttempt<retryDelay) { vTaskDelay(pdMS_TO_TICKS(250));continue; }
    lastUploadAttempt=millis();uploadAttempted=true;
    if(uploadBatch()) { retryDelay=5000;vTaskDelay(pdMS_TO_TICKS(250)); }
    else {
      // Poll the loop while backing off so heartbeats remain independent.
      retryDelay=std::min(retryDelay*2,uint32_t(60000));
      vTaskDelay(pdMS_TO_TICKS(250));
    }
  }
}
void updateTimedRule(bool condition,uint32_t now,uint32_t duration,uint32_t& since,bool& active,
                     const char* type,const char* detail,float value) {
  if(!condition) { since=0;active=false;return; }
  if(!since) since=now;
  if(!active && now-since>=duration) { active=true;event(type,detail,value); }
}
void telemetry(uint32_t now) {
  JsonDocument doc;
  doc["armed"]=armed;doc["lid_open"]=reedStable!=LOW;
  doc["ldr_enabled"]=LDR_ENABLED;
  if(LDR_ENABLED) doc["ldr_raw"]=ldrRaw;
  doc["dht_ok"]=dhtValid;
  if(dhtValid) { doc["temperature_c"]=temperatureC;doc["humidity_pct"]=humidity; }
  doc["dht_age_ms"]=now-lastDht;
  doc["imu_ok"]=imuValid;doc["reference_valid"]=calibrated;
  if(imuValid) {
    doc["ax_g"]=ax;doc["ay_g"]=ay;doc["az_g"]=az;doc["total_g"]=totalG;doc["peak_g"]=peakG;
    if(calibrated) { doc["tilt_deg"]=angleDeg;doc["gx_rad_s"]=gx-biasX;doc["gy_rad_s"]=gy-biasY;doc["gz_rad_s"]=gz-biasZ; }
  }
  xSemaphoreTake(queueMutex,portMAX_DELAY);
  doc["queued"]=queueCount;doc["dropped_records"]=droppedRecords;doc["storage_errors"]=storageErrors;
  xSemaphoreGive(queueMutex);
  doc["sample_gap_count"]=missedSamples;doc["wifi_connected"]=WiFi.status()==WL_CONNECTED;
  saveRecord("telemetry",doc.as<JsonObject>());peakG=0;
}
void setup() {
  Serial.begin(115200);delay(1000);
  Serial.println("\nCARGO MONITOR | a=arm d=disarm c=reference F=format storage (erases queued history)");
  bootId=String((uint32_t)(ESP.getEfuseMac()>>32),HEX)+String(esp_random(),HEX)+String(esp_random(),HEX);
  queueMutex=xSemaphoreCreateMutex();
  if(!queueMutex) { Serial.println("FATAL: mutex allocation");while(true)delay(1000); }
  recoverQueue();
  pinMode(REED_PIN,INPUT_PULLUP);reedStable=reedRaw=digitalRead(REED_PIN);
  analogReadResolution(12);dht.begin();
  Wire.begin(21,22);Wire.setClock(100000);Wire.setTimeOut(20);
  imuConfigured=configureImu();
  if(imuConfigured) calibrateReference();
  event("boot","Monitoring starts DISARMED; send a after closing box");
  Serial.println("LDR detection disabled unless enabled in config.h. Use 2.4 GHz hotspot.");
  if(xTaskCreate(networkTask,"network",16384,nullptr,1,nullptr)!=pdPASS) {
    event("network_task_failed","Local monitoring only; task allocation failed");
  }
}
void loop() {
  uint32_t now=millis();
  while(Serial.available()) {
    const char cmd=Serial.read();
    if(cmd=='a') {
      if(reedStable!=LOW || !calibrated || !imuValid) Serial.println("ARM refused: close lid and capture valid stationary reference with c.");
      else { armed=true;tiltSince=lightSince=environmentSince=0;tiltActive=lightActive=environmentActive=false;event("armed","Cargo protection armed"); }
    } else if(cmd=='d') { armed=false;event("disarmed","Cargo protection disarmed"); }
    else if(cmd=='c') {
      if(armed) Serial.println("Disarm before reference capture.");
      else { calibrated=false;calibrateReference(); }
    } else if(cmd=='F') {
      xSemaphoreTake(queueMutex,portMAX_DELAY);
      if(!fsReady) { fsReady=LittleFS.format() && LittleFS.begin(false);queueCount=0;nextFileId=1; }
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
      if(!imuFaultReported) { event("imu_fault","MPU read failed; motion detection unavailable");imuFaultReported=true; }
      imuConfigured=false;calibrated=false;tiltSince=0;tiltActive=false;
    } else {
      if(imuFaultReported) { event("imu_recovered","Capture reference again with c before arming");imuFaultReported=false; }
      peakG=std::max(peakG,totalG);
      if(calibrated) {
        const float dot=(ax*refX+ay*refY+az*refZ)/totalG;
        angleDeg=acosf(std::max(-1.0f,std::min(1.0f,dot)))*180.0f/PI;
      }
      if(armed && totalG>IMPACT_G && (!lastImpact || now-lastImpact>=3000)) {
        lastImpact=now;event("impact","Sampled total acceleration above threshold; uncalibrated g",totalG);
      }
      const float rate=sqrtf(powf(gx-biasX,2)+powf(gy-biasY,2)+powf(gz-biasZ,2));
      const bool stable=calibrated && fabsf(totalG-refMagnitude)<0.20f && rate<0.25f;
      updateTimedRule(armed && stable && angleDeg>TILT_DEGREES,now,3000,tiltSince,tiltActive,
                      "unsafe_tilt","Sustained tilt from reference",angleDeg);
    }
  }
  if(!imuConfigured && now-lastRecovery>=5000) { lastRecovery=now;imuConfigured=configureImu(); }
  if(now-lastDht>=3000) {
    const bool wasValid=dhtValid;
    lastDht=now;humidity=dht.readHumidity();temperatureC=dht.readTemperature();
    dhtValid=isfinite(temperatureC) && isfinite(humidity);
    if(!dhtValid && wasValid) event("dht_fault","Temperature/humidity unavailable");
    if(dhtValid && !wasValid) event("dht_ready","Temperature/humidity readings available");
    if(!dhtValid) { environmentSince=0;environmentActive=false; }
  }
  if(LDR_ENABLED) {
    ldrRaw=analogRead(LDR_PIN);
    updateTimedRule(armed && ldrRaw>LIGHT_THRESHOLD,now,1000,lightSince,lightActive,
                    "unexpected_light","Internal light above configured threshold",ldrRaw);
  }
  updateTimedRule(armed && dhtValid && (temperatureC<TEMP_MIN_C || temperatureC>TEMP_MAX_C || humidity>HUMIDITY_MAX),
                  now,15000,environmentSince,environmentActive,"environment_warning","Environmental limit exceeded",temperatureC);
  if(!lastTelemetry || now-lastTelemetry>=TELEMETRY_MS) { lastTelemetry=now;telemetry(now); }
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
