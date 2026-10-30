#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
// Reproduce Arduino's macro environment: the decoder must remain unaffected.
#define word(...) makeWord(__VA_ARGS__)
#include "../../firmware/cargo_monitor/monitor_logic.h"
#include "../../firmware/cargo_monitor/console_buffer.h"
#define CHECK(c) do { if(!(c)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#c);exit(1); } } while(0)
static bool closeTo(float a,float b) { return fabsf(a-b)<0.001f; }
void run(const char* name) {
 if(!strcmp(name,"decode_signed_boundaries")) {
  const uint8_t b[]={0,0,0,1,0x7f,0xff,0x80,0,0xff,0xff,0xf8,0};
  CHECK(cargo::decodeSigned16(b,0)==0);CHECK(cargo::decodeSigned16(b,2)==1);
  CHECK(cargo::decodeSigned16(b,4)==32767);CHECK(cargo::decodeSigned16(b,6)==-32768);
  CHECK(cargo::decodeSigned16(b,8)==-1);CHECK(cargo::decodeSigned16(b,10)==-2048);
 } else if(!strcmp(name,"captured_mpu_frame")) {
  // Actual raw frame captured on the board, including negative gyro words.
  const uint8_t b[]={0,0x21,0xff,0xed,7,0xa2,0xf4,7,0xff,0xc7,0xff,0x4a,0xff,0xd5};
  CHECK(cargo::decodeSigned16(b,0)==33);CHECK(cargo::decodeSigned16(b,2)==-19);
  CHECK(cargo::decodeSigned16(b,4)==1954);CHECK(cargo::decodeSigned16(b,8)==-57);
  CHECK(cargo::decodeSigned16(b,10)==-182);CHECK(cargo::decodeSigned16(b,12)==-43);
  CHECK(closeTo(cargo::decodeSigned16(b,4)/2048.0f,0.9541015625f));
 } else if(!strcmp(name,"arming_all_prerequisites")) {
  for(int mask=0;mask<8;++mask) CHECK(cargo::canArm(mask&1,mask&2,mask&4)==(mask==7));
 } else if(!strcmp(name,"queue_event_reservation")) {
  CHECK(cargo::canQueue(true,223,true,256,32));CHECK(!cargo::canQueue(true,224,true,256,32));
  CHECK(cargo::canQueue(true,224,false,256,32));CHECK(cargo::canQueue(true,255,false,256,32));
  CHECK(!cargo::canQueue(true,256,false,256,32));CHECK(!cargo::canQueue(false,0,false,256,32));
  CHECK(!cargo::canQueue(true,0,true,16,32));
 } else if(!strcmp(name,"timer_duration_and_single_alert")) {
  cargo::TimedRule r;CHECK(!r.update(true,100,3000));CHECK(!r.update(true,3099,3000));
  CHECK(r.update(true,3100,3000));CHECK(!r.update(true,9999,3000));
 } else if(!strcmp(name,"timer_interruption_resets_duration")) {
  cargo::TimedRule r;r.update(true,100,1000);CHECK(!r.update(false,900,1000));
  CHECK(!r.update(true,1000,1000));CHECK(!r.update(true,1999,1000));CHECK(r.update(true,2000,1000));
  r.reset();CHECK(!r.active);CHECK(!r.update(true,3000,1000));
 } else if(!strcmp(name,"timer_start_at_zero")) {
  cargo::TimedRule r;CHECK(!r.update(true,0,1000));CHECK(!r.update(true,500,1000));CHECK(r.update(true,1000,1000));
 } else if(!strcmp(name,"timer_rollover")) {
  cargo::TimedRule r;CHECK(!r.update(true,0xfffffff0u,32));CHECK(!r.update(true,15,32));CHECK(r.update(true,16,32));
 } else if(!strcmp(name,"timer_immediate_and_rearm")) {
  cargo::TimedRule r;CHECK(r.update(true,0,0));CHECK(!r.update(true,1,0));
  CHECK(!r.update(false,2,0));CHECK(r.update(true,3,0));
 } else if(!strcmp(name,"impact_threshold_and_disarmed")) {
  cargo::ImpactRule r;CHECK(!r.update(false,8,3,10));CHECK(!r.update(true,3,3,20));
  CHECK(!r.update(true,NAN,3,30));CHECK(!r.update(true,INFINITY,3,40));CHECK(r.update(true,3.01f,3,50));
 } else if(!strcmp(name,"impact_cooldown_at_zero")) {
  cargo::ImpactRule r;CHECK(r.update(true,4,3,0));CHECK(!r.update(true,5,3,1));
  CHECK(!r.update(true,5,3,2999));CHECK(r.update(true,5,3,3000));
 } else if(!strcmp(name,"impact_rollover")) {
  cargo::ImpactRule r;CHECK(r.update(true,4,3,0xfffffff0u,32));CHECK(!r.update(true,4,3,15,32));CHECK(r.update(true,4,3,16,32));
 } else if(!strcmp(name,"tilt_known_orientations")) {
  CHECK(closeTo(cargo::tiltDegrees(0,0,1,0,0,1),0));
  CHECK(closeTo(cargo::tiltDegrees(1,0,0,0,0,1),90));
  CHECK(closeTo(cargo::tiltDegrees(0,0,-1,0,0,1),180));
  CHECK(closeTo(cargo::tiltDegrees(0,0,1.15f,0,0,1),0));
 } else if(!strcmp(name,"tilt_invalid_and_roundoff")) {
  CHECK(isnan(cargo::tiltDegrees(0,0,0,0,0,1)));CHECK(isnan(cargo::tiltDegrees(NAN,0,1,0,0,1)));
  CHECK(isnan(cargo::tiltDegrees(0,0,1,NAN,0,1)));CHECK(closeTo(cargo::tiltDegrees(0,0,1,0,0,1.00001f),0));
 } else if(!strcmp(name,"stable_motion_gating")) {
  CHECK(cargo::stableMotion(true,1.15f,1.15f,0));CHECK(!cargo::stableMotion(false,1,1,0));
  CHECK(!cargo::stableMotion(true,3,1,0));CHECK(!cargo::stableMotion(true,1,1,0.25f));
  CHECK(!cargo::stableMotion(true,1,1,NAN));CHECK(!cargo::stableMotion(true,NAN,1,0));
 } else if(!strcmp(name,"seal_evidence_combinations")) {
  CHECK(cargo::sealState(false,true,false)==cargo::SealState::LikelyClosed);
  CHECK(cargo::sealState(true,true,true)==cargo::SealState::LikelyOpened);
  CHECK(cargo::sealState(false,true,true)==cargo::SealState::PossibleSeparation);
  CHECK(cargo::sealState(true,true,false)==cargo::SealState::OpeningSuspected);
 } else if(!strcmp(name,"disabled_light_cannot_confirm_opening")) {
  CHECK(cargo::sealState(true,false,true)==cargo::SealState::OpeningSuspected);
  CHECK(cargo::sealState(false,false,true)==cargo::SealState::LikelyClosed);
 } else if(!strcmp(name,"wifi_sticky_until_failure")) {
  CHECK(cargo::keepWifi(true,0));CHECK(cargo::keepWifi(true,2));
  CHECK(!cargo::keepWifi(true,3));CHECK(!cargo::keepWifi(false,0));
 } else if(!strcmp(name,"vibration_repetitive_signal")) {
  cargo::VibrationWindow v;
  for(int i=0;i<200;++i)v.push(0.5f*sinf(i*2*3.14159265f*12/100),0,1);
  CHECK(v.rms()>0.25f);CHECK(v.repetitive());
  v.resetWindow();CHECK(v.samples==0);CHECK(v.rms()==0);
 } else if(!strcmp(name,"vibration_rejects_stillness_and_single_impact")) {
  cargo::VibrationWindow still,impact;
  for(int i=0;i<100;++i){still.push(0,0,1);impact.push(i==50?4:0,0,1);}
  CHECK(still.rms()==0);CHECK(!still.repetitive());CHECK(!impact.repetitive());
 } else if(!strcmp(name,"vibration_ignores_invalid_samples")) {
  cargo::VibrationWindow v;v.push(NAN,0,1);v.push(0,INFINITY,1);CHECK(v.samples==0);
  v.push(0,0,1);CHECK(v.samples==1);CHECK(v.rms()==0);
 } else if(!strcmp(name,"console_order_and_trip_capture")) {
  cargo::ConsoleBuffer<3> b;cargo::ConsoleLine out[3];b.push("first",10,"trip-a");b.push("second",20,"trip-b");
  CHECK(b.peek(out,3)==2);CHECK(out[0].seq==1);CHECK(out[0].uptime==10);CHECK(!strcmp(out[0].trip,"trip-a"));CHECK(!strcmp(out[1].message,"second"));
 } else if(!strcmp(name,"console_overflow_keeps_latest")) {
  cargo::ConsoleBuffer<2> b;cargo::ConsoleLine out[2];b.push("one",1);b.push("two",2);b.push("three",3);
  CHECK(b.dropped==1);CHECK(b.peek(out,2)==2);CHECK(out[0].seq==2);CHECK(out[1].seq==3);
 } else if(!strcmp(name,"console_ack_preserves_arrivals_during_upload")) {
  cargo::ConsoleBuffer<2> b;cargo::ConsoleLine out[2];b.push("one",1);b.push("two",2);CHECK(b.peek(out,2)==2);
  b.push("three",3);b.acknowledge(2);CHECK(b.peek(out,2)==1);CHECK(out[0].seq==3);
 } else if(!strcmp(name,"console_retry_and_partial_ack")) {
  cargo::ConsoleBuffer<3> b;cargo::ConsoleLine first[3],retry[3];b.push("one",1);b.push("two",2);
  CHECK(b.peek(first,1)==1);CHECK(b.peek(retry,1)==1);CHECK(first[0].seq==retry[0].seq);
  b.acknowledge(1);CHECK(b.peek(first,3)==1);CHECK(first[0].seq==2);b.acknowledge(1);CHECK(b.count()==1);
 } else if(!strcmp(name,"console_long_messages_are_terminated")) {
  cargo::ConsoleBuffer<1> b;cargo::ConsoleLine out[1];char longText[400];memset(longText,'a',399);longText[399]=0;
  b.push(longText,0,longText);CHECK(b.peek(out,1)==1);CHECK(strlen(out[0].message)==255);CHECK(strlen(out[0].trip)==64);
 } else if(!strcmp(name,"online_fast_offline_storage_interval")) {
  CHECK(cargo::telemetryInterval(true)==1000);CHECK(cargo::telemetryInterval(false)==60000);
  CHECK(!cargo::serverRecentlyReachable(false,1000,999));CHECK(!cargo::serverRecentlyReachable(true,1000,0));
  CHECK(cargo::serverRecentlyReachable(true,15999,1000));CHECK(!cargo::serverRecentlyReachable(true,16000,1000));
 } else if(!strcmp(name,"server_health_clock_rollover")) {
  CHECK(cargo::serverRecentlyReachable(true,5,0xfffffff0u));CHECK(!cargo::serverRecentlyReachable(true,20000,0xfffffff0u));
 } else { fprintf(stderr,"Unknown test: %s\n",name);exit(2); }
 printf("PASS %s\n",name);
}
int main(int argc,char**argv) { if(argc!=2)return 2;run(argv[1]);return 0; }
