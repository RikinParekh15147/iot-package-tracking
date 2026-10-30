#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

namespace cargo {
struct ConsoleLine {
  uint32_t seq=0,uptime=0;
  char message[256]={0},trip[65]={0};
};
template <size_t Capacity> class ConsoleBuffer {
 public:
  uint32_t dropped=0,next=1;
  size_t count() const{return size;}
  void push(const char* message,uint32_t uptime,const char* trip="") {
    if(size==Capacity){head=(head+1)%Capacity;--size;++dropped;}
    ConsoleLine& line=lines[(head+size)%Capacity];
    line.seq=next++;line.uptime=uptime;
    copy(line.message,sizeof(line.message),message);copy(line.trip,sizeof(line.trip),trip);++size;
  }
  size_t peek(ConsoleLine* out,size_t maximum) const {
    size_t n=size<maximum?size:maximum;
    for(size_t i=0;i<n;++i)out[i]=lines[(head+i)%Capacity];
    return n;
  }
  void acknowledge(uint32_t through) {
    while(size && lines[head].seq<=through){head=(head+1)%Capacity;--size;}
  }
 private:
  ConsoleLine lines[Capacity];
  size_t head=0,size=0;
  static void copy(char* target,size_t capacity,const char* source){
    strncpy(target,source?source:"",capacity-1);target[capacity-1]=0;
  }
};
}
