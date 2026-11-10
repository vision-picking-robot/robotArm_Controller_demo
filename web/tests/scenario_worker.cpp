// The actual asynchronous worker state machine, linked to a fake SDK.
#include "../rosbridge/host-sdk/rh_p12.hpp"
#include <atomic>
#include <cstring>
#include <string>
#include <sstream>
#include <cassert>
enum {STATE_STANDBY,STATE_MOVING,ROBOT_MODE_AUTONOMOUS,ROBOT_SYSTEM_REAL,STOP_TYPE_SLOW,MOVE_MODE_ABSOLUTE,MOVE_REFERENCE_BASE,BYTE_SIZE_EIGHTBITS,PARITY_CHECK_NONE,STOPBITS_ONE};
struct Pose{float _fPosition[6];};struct Task{float _fTargetPos[6];};
namespace DRAFramework {struct CDRFLEx{
    int state=STATE_STANDBY,moves=0,stops=0;std::string tcp="tool";Pose q{{.2f,-.1f,90,0,90,0}},goal{},via{},reference{{373,0,262,-90,180,0}};Task task{};
    Pose* get_current_posj(){return &q;}Task* get_current_posx(){return &task;}Pose* fkin(float*){return &reference;}Pose* ikin(float*,int){return nullptr;}
    std::string get_tcp(){return tcp;}int get_robot_state(){return state;}int get_robot_mode(){return ROBOT_MODE_AUTONOMOUS;}int get_robot_system(){return ROBOT_SYSTEM_REAL;}
    int check_motion(){return state==STATE_MOVING;}
    bool amovej(float* p,float,float,float,int){moves++;std::copy(p,p+6,goal._fPosition);state=STATE_MOVING;return true;}
    bool amovel(float*,float*,float*,float,int,int){moves++;state=STATE_MOVING;return true;}
    bool amovec(float p[2][6],float*,float*,float,int,int){moves++;std::copy(p[0],p[0]+6,via._fPosition);std::copy(p[1],p[1]+6,goal._fPosition);state=STATE_MOVING;return true;}
    bool stop(int){stops++;state=STATE_STANDBY;return true;}
    bool flange_serial_open(int,int,int,int){return true;}bool flange_serial_close(){return true;}
};}
static long long clock_ms=100000;static std::atomic<bool> down{false},held{true},initialized{true},in_move{false};
static std::atomic<unsigned> grant_number{1};static std::atomic<long long> last_frame{100000};static bool mode_verified=true;
static long long now_ms(){return clock_ms;}static std::string last_result;
static void event(const char* key,const char* value){if(std::string(key)=="segment")last_result=value;}
static bool can_move(DRAFramework::CDRFLEx& s,unsigned g){return !in_move&&held&&initialized&&!down&&g==grant_number&&s.state==STATE_STANDBY;}
struct FlangePort{DRAFramework::CDRFLEx& sdk;unsigned grant;bool open(){return sdk.flange_serial_open(57600,BYTE_SIZE_EIGHTBITS,PARITY_CHECK_NONE,STOPBITS_ONE);}void check(){}void write(rhp12::Bytes){}rhp12::Bytes read(float){return {};}};
#include "../rosbridge/host-sdk/scenario_worker.hpp"
static std::string command(float shoulder=0){std::ostringstream s;s<<"segment 1 0123456789abcdef0123456789abcdef joint 10 10 -1 0 1 120 746f6f6c 180 "<<shoulder<<" 90 0 90 0 373 0 262 -90 180 0";return s.str();}
static void advance(){clock_ms+=300;last_frame=clock_ms;}
int main(){
    DRAFramework::CDRFLEx sdk;ScenarioRun r;
    r.begin(sdk,command().c_str());assert(r.active&&sdk.moves==1);assert(sdk.goal._fPosition[0]==sdk.q._fPosition[0]+180);
    for(int i=1;i<6;i++)assert(sdk.goal._fPosition[i]==sdk.q._fPosition[i]);
    sdk.q=sdk.goal;sdk.state=STATE_STANDBY;advance();r.poll(sdk);assert(!r.active&&last_result.find("completed")!=std::string::npos);
    sdk.q={{0,0,90,0,90,0}};r.begin(sdk,command(3).c_str());assert(!r.active&&sdk.moves==1);
    r.begin(sdk,command().c_str());assert(r.active);r.stop(sdk);assert(sdk.stops==1);advance();r.poll(sdk);assert(last_result.find("cancelled")!=std::string::npos);
    r.begin(sdk,command().c_str());assert(r.active);held=false;advance();r.poll(sdk);assert(last_result.find("control_lost")!=std::string::npos&&sdk.stops==2);held=true;
    sdk.tcp="changed";int before=sdk.moves;r.begin(sdk,command().c_str());assert(!r.active&&sdk.moves==before&&last_result.find("tcp_changed")!=std::string::npos);
    sdk.tcp="tool";sdk.task={};const char* arc="segment 1 0123456789abcdef0123456789abcdef circle 10 10 -1 0 0 120 746f6f6c 20 0 0 0 0 0 373 0 262 -90 180 0 10 10 0 0 0 0";
    r.begin(sdk,arc);assert(r.active&&sdk.moves==before+1);assert(sdk.via._fPosition[0]==10&&sdk.via._fPosition[1]==10&&sdk.goal._fPosition[0]==20);
    std::copy(sdk.goal._fPosition,sdk.goal._fPosition+6,sdk.task._fTargetPos);sdk.state=STATE_STANDBY;advance();r.poll(sdk);assert(last_result.find("completed")!=std::string::npos&&last_result.find("current_joints")!=std::string::npos);
    before=sdk.moves;sdk.task={};r.begin(sdk,"segment 1 0123456789abcdef0123456789abcdef circle 10 10 -1 0 0 120 746f6f6c 20 0 0 0 0 0 373 0 262 -90 180 0 10 0 0 0 0 0");assert(!r.active&&sdk.moves==before&&last_result.find("invalid_arc")!=std::string::npos);
}
