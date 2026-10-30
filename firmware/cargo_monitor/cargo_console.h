#pragma once
#include <Arduino.h>
#include "console_buffer.h"

// Tee application Serial output to USB and a bounded RAM ring. ESP boot ROM and
// system-library logging are outside this wrapper and remain USB-only.
class CargoConsole: public Print {
 public:
  using Print::write;
  void begin(unsigned long baud){::Serial.begin(baud);}
  int available(){return ::Serial.available();}
  int read(){return ::Serial.read();}
  size_t write(uint8_t value) override {
    const size_t result=::Serial.write(value);
    portENTER_CRITICAL(&mux);
    if(value=='\n') {
      partial[used]=0;
      if(truncated)strcpy(partial+used," [truncated]");
      buffer.push(partial,millis(),trip);used=0;truncated=false;
    } else if(value!='\r') {
      if(used<sizeof(partial)-14)partial[used++]=char(value);else truncated=true;
    }
    portEXIT_CRITICAL(&mux);return result;
  }
  void setTrip(const String& id){
    portENTER_CRITICAL(&mux);strncpy(trip,id.c_str(),sizeof(trip)-1);trip[sizeof(trip)-1]=0;portEXIT_CRITICAL(&mux);
  }
  size_t snapshot(cargo::ConsoleLine* out,size_t maximum,uint32_t& dropped){
    portENTER_CRITICAL(&mux);const size_t n=buffer.peek(out,maximum);dropped=buffer.dropped;portEXIT_CRITICAL(&mux);return n;
  }
  void acknowledge(uint32_t seq){portENTER_CRITICAL(&mux);buffer.acknowledge(seq);portEXIT_CRITICAL(&mux);}
 private:
  cargo::ConsoleBuffer<96> buffer;
  portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;
  char partial[256]={0},trip[65]={0};
  size_t used=0;
  bool truncated=false;
};
