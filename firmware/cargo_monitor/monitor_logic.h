#pragma once
#include <stdint.h>
#include <math.h>

// Shared by the ESP32 sketch and native tests. No Arduino/network dependencies.
namespace cargo {
inline int16_t decodeSigned16(const uint8_t* bytes, unsigned index) {
  return static_cast<int16_t>((uint16_t(bytes[index]) << 8) | bytes[index + 1]);
}
inline bool canArm(bool lidClosed, bool referenceValid, bool imuValid) {
  return lidClosed && referenceValid && imuValid;
}
inline bool canQueue(bool storageReady, uint32_t count, bool telemetry,
                     uint32_t capacity, uint32_t reserve) {
  return storageReady && reserve <= capacity && count < (telemetry ? capacity - reserve : capacity);
}
inline float tiltDegrees(float x,float y,float z,float refX,float refY,float refZ) {
  const float magnitude=sqrtf(x*x+y*y+z*z);
  if(!isfinite(magnitude) || magnitude<=0.05f) return NAN;
  float dot=(x*refX+y*refY+z*refZ)/magnitude;
  if(!isfinite(dot)) return NAN;
  if(dot>1) dot=1;
  if(dot<-1) dot=-1;
  return acosf(dot)*180.0f/3.14159265358979323846f;
}
inline bool stableMotion(bool referenceValid,float magnitude,float referenceMagnitude,float rate) {
  return referenceValid && isfinite(magnitude) && isfinite(referenceMagnitude) && isfinite(rate)
      && fabsf(magnitude-referenceMagnitude)<0.20f && rate<0.25f && rate>=0;
}
struct TimedRule {
  uint32_t since=0;
  bool tracking=false, active=false;
  void reset() { since=0;tracking=false;active=false; }
  bool update(bool condition,uint32_t now,uint32_t duration) {
    if(!condition) { reset();return false; }
    if(!tracking) { tracking=true;since=now; }
    if(!active && uint32_t(now-since)>=duration) { active=true;return true; }
    return false;
  }
};
struct ImpactRule {
  uint32_t last=0;
  bool fired=false;
  bool update(bool armed,float magnitude,float threshold,uint32_t now,uint32_t cooldown=3000) {
    if(!armed || !isfinite(magnitude) || magnitude<=threshold) return false;
    if(fired && uint32_t(now-last)<cooldown) return false;
    fired=true;last=now;return true;
  }
};
}

namespace cargo {
enum class SealState { LikelyClosed, OpeningSuspected, LikelyOpened, PossibleSeparation };
inline SealState sealState(bool lidOpen,bool lightEnabled,bool bright) {
  if(lidOpen) return lightEnabled && bright ? SealState::LikelyOpened : SealState::OpeningSuspected;
  return lightEnabled && bright ? SealState::PossibleSeparation : SealState::LikelyClosed;
}
inline const char* sealLabel(SealState s) {
  switch(s) {
    case SealState::LikelyOpened:return "likely_opened";
    case SealState::OpeningSuspected:return "opening_suspected";
    case SealState::PossibleSeparation:return "possible_packaging_separation";
    default:return "likely_closed";
  }
}
inline bool keepWifi(bool associated,unsigned serverFailures) { return associated && serverFailures<3; }
struct VibrationWindow {
  float baseline[3]={0,0,0},energy=0;
  unsigned samples=0,crossings[3]={0,0,0};
  int signs[3]={0,0,0};
  bool initialized=false;
  void push(float x,float y,float z) {
    if(!isfinite(x)||!isfinite(y)||!isfinite(z)) return;
    float v[3]={x,y,z};
    if(!initialized) {for(int i=0;i<3;++i)baseline[i]=v[i];initialized=true;}
    for(int i=0;i<3;++i) {
      baseline[i]+=0.02f*(v[i]-baseline[i]);
      float residual=v[i]-baseline[i];energy+=residual*residual;
      if(fabsf(residual)>0.05f) {
        int sign=residual>0?1:-1;
        if(signs[i] && signs[i]!=sign)++crossings[i];
        signs[i]=sign;
      }
    }
    ++samples;
  }
  float rms() const {return samples?sqrtf(energy/samples):0;}
  bool repetitive() const {return crossings[0]>=6||crossings[1]>=6||crossings[2]>=6;}
  void resetWindow() {energy=0;samples=0;for(int i=0;i<3;++i)crossings[i]=0;}
};
}

namespace cargo {
inline bool serverRecentlyReachable(bool associated,uint32_t now,uint32_t lastOk){return associated && lastOk!=0 && uint32_t(now-lastOk)<15000;}
inline uint32_t telemetryInterval(bool reachable){return reachable?1000u:60000u;}
}
