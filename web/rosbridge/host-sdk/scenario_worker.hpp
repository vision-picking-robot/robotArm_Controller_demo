// Included after FlangePort: all SDK calls remain on the owning worker thread.
#include <array>
#include <sstream>
#include <memory>
#include <iomanip>

using Six=std::array<float,6>;
static bool finite_six(const Six& p){for(float v:p)if(!std::isfinite(v))return false;return true;}
static bool joint_six(const Six& p){const float limits[]={360,360,155,360,155,360};for(int i=0;i<6;i++)if(!std::isfinite(p[i])||std::abs(p[i])>limits[i])return false;return true;}
static Six copy_six(const float* p){if(!p)throw std::runtime_error("pose_unavailable");Six r;std::copy(p,p+6,r.begin());if(!finite_six(r))throw std::runtime_error("pose_unavailable");return r;}
static Six joints_now(DRAFramework::CDRFLEx& sdk){auto p=sdk.get_current_posj();return copy_six(p?p->_fPosition:nullptr);}
static Six task_now(DRAFramework::CDRFLEx& sdk){auto p=sdk.get_current_posx();return copy_six(p?p->_fTargetPos:nullptr);}
static Six forward(DRAFramework::CDRFLEx& sdk,Six q){auto p=sdk.fkin(q.data());return copy_six(p?p->_fPosition:nullptr);}
static std::string hex_name(const std::string& name){std::ostringstream s;for(unsigned char c:name)s<<std::hex<<std::setw(2)<<std::setfill('0')<<int(c);return s.str().empty()?"-":s.str();}
static std::string json_quote(const std::string& text){std::ostringstream s;s<<'"';for(unsigned char c:text){if(c=='"'||c=='\\')s<<'\\'<<c;else if(c<32)s<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<int(c);else s<<c;}s<<'"';return s.str();}
static std::string array_json(const Six& p){std::ostringstream s;s<<'[';for(int i=0;i<6;i++){if(i)s<<',';s<<std::setprecision(9)<<p[i];}s<<']';return s.str();}
static std::array<float,9> rotation_of(const Six& p){
    const float r=3.141592653589793/180,a=p[3]*r,b=p[4]*r,c=p[5]*r,ca=cos(a),sa=sin(a),cb=cos(b),sb=sin(b),cc=cos(c),sc=sin(c);
    return {ca*cb*cc-sa*sc,-ca*cb*sc-sa*cc,ca*sb,sa*cb*cc+ca*sc,-sa*cb*sc+ca*cc,sa*sb,-sb*cc,sb*sc,cb};
}
static bool near_pose(const Six& a,const Six& b){
    float d=0;for(int i=0;i<3;i++)d+=(a[i]-b[i])*(a[i]-b[i]);
    auto x=rotation_of(a),y=rotation_of(b);float trace=0;for(int i=0;i<9;i++)trace+=x[i]*y[i];
    return d<=1&&std::acos(std::min(1.f,std::max(-1.f,(trace-1)/2)))<=3.141592653589793/180;
}
static bool valid_arc(const Six& start,const Six& via,const Six& end){
    if(!finite_six(start)||!finite_six(via)||!finite_six(end))return false;
    float a[3],b[3];double la=0,lb=0,lc=0;
    for(int i=0;i<3;i++){a[i]=via[i]-start[i];b[i]=end[i]-start[i];la+=a[i]*a[i];lb+=b[i]*b[i];lc+=(via[i]-end[i])*(via[i]-end[i]);}
    double c[]={a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]};
    return std::min({la,lb,lc})>=.0001&&(c[0]*c[0]+c[1]*c[1]+c[2]*c[2])>1e-8*la*lb;
}
static std::string hand_json(const rhp12::Status& h){std::ostringstream s;
    s<<"{\"position\":"<<h.position<<",\"current_ma\":"<<h.current<<",\"goal_current_ma\":"<<h.goal_current<<",\"limit_ma\":"<<h.limit
     <<",\"mode\":"<<h.mode<<",\"error\":"<<h.error<<",\"goal_position\":"<<h.goal_position<<",\"contact\":"<<(h.contact?"true":"false")
     <<",\"input_voltage_v\":"<<(h.voltage_tenths<0?"null":std::to_string(h.voltage_tenths/10.0))
     <<",\"moving\":"<<(h.moving?"true":"false")<<",\"velocity_limit\":"<<h.velocity_limit<<",\"acceleration_limit\":"<<h.acceleration_limit<<",\"torque_known\":true}";return s.str();}

static void inspect_scenario(DRAFramework::CDRFLEx& sdk,const char* command){
    std::istringstream in(command);std::string op,id,extra;unsigned grant;int np,nq;
    in>>op>>grant>>id>>np>>nq;
    std::vector<Six> poses(std::max(0,np)),qs(std::max(0,nq));
    if(!in||np<0||np>4||nq<0||nq>5||id.size()!=32)return;
    for(auto& p:poses)for(auto& v:p)in>>v;for(auto& p:qs)for(auto& v:p)in>>v;
    bool opened=false;
    try{
        if(!in||(in>>extra)||!can_move(sdk,grant))throw std::runtime_error("control");
        Six seed=joints_now(sdk),current=task_now(sdk),reference=forward(sdk,{0,0,90,0,90,0});
        std::vector<Six> solutions,fk;
        for(auto target:poses){
            if(!finite_six(target))throw std::runtime_error("invalid_pose");
            double best=1e100;Six chosen{};
            for(int space=0;space<8;space++){
                auto result=sdk.ikin(target.data(),space);if(!result)continue;
                try{
                    auto q=copy_six(result->_fPosition);if(!joint_six(q)||!near_pose(forward(sdk,q),target))continue;
                    double cost=0;for(int i=0;i<6;i++)cost+=(q[i]-seed[i])*(q[i]-seed[i]);
                    if(cost<best){best=cost;chosen=q;}
                }catch(const std::exception&){continue;}
            }
            if(best==1e100)throw std::runtime_error("inverse_kinematics");solutions.push_back(chosen);seed=chosen;
        }
        for(auto q:qs){if(!joint_six(q))throw std::runtime_error("joint_limit");fk.push_back(forward(sdk,q));}
        FlangePort port{sdk,grant};rhp12::Hand<FlangePort> hand(port);rhp12::Status h;
        opened=port.open();
        if(!opened)throw std::runtime_error("serial_open");hand.inspect(h);
        if(!sdk.flange_serial_close())throw std::runtime_error("serial_close");opened=false;port.check();
        std::ostringstream out;out<<"{\"id\":"<<json_quote(id)<<",\"current_joints\":"<<array_json(joints_now(sdk))<<",\"current_tcp\":"<<array_json(current)
          <<",\"reference_tcp\":"<<array_json(reference)<<",\"tcp_name\":"<<json_quote(sdk.get_tcp())<<",\"hand\":"<<hand_json(h)<<",\"solutions\":[";
        for(size_t i=0;i<solutions.size();i++){if(i)out<<',';out<<array_json(solutions[i]);}out<<"],\"forward\":[";
        for(size_t i=0;i<fk.size();i++){if(i)out<<',';out<<array_json(fk[i]);}out<<"]}";event("inspection",out.str().c_str());
    }catch(const std::exception& e){if(opened)sdk.flange_serial_close();std::string out="{\"id\":"+json_quote(id)+",\"error\":"+json_quote(e.what())+"}";event("inspection",out.c_str());}
}

struct ScenarioRun {
    std::string id,kind,tcp;
    unsigned grant=0;Six target{},reference{},via{};
    bool active=false,stopping=false,serial=false;long long started=0,deadline=0,last_poll=0;
    std::unique_ptr<FlangePort> port;
    std::unique_ptr<rhp12::Hand<FlangePort>> hand;
    rhp12::Status feedback;
    void finish(DRAFramework::CDRFLEx& sdk,const std::string& state,const std::string& message){
        std::string observed=state;if(serial&&!sdk.flange_serial_close())observed="unknown";
        std::string joints;if(observed=="completed"&&kind!="gripper"){try{joints=",\"current_joints\":"+array_json(joints_now(sdk));}catch(...){observed="unknown";}}
        std::string result="{\"id\":"+json_quote(id)+",\"state\":"+json_quote(observed)+",\"message\":"+json_quote(message);
        result+=joints;
        if(kind=="gripper")result+=",\"feedback\":"+hand_json(feedback);
        result+="}";active=false;serial=false;hand.reset();port.reset();in_move=false;event("segment",result.c_str());
    }
    void stop(DRAFramework::CDRFLEx& sdk){
        if(!active||stopping)return;stopping=true;
        if(kind=="gripper")hand->halt(feedback);
        else if(!sdk.stop(STOP_TYPE_SLOW))throw std::runtime_error("stop_unknown");
    }
    void begin(DRAFramework::CDRFLEx& sdk,const char* command){
        std::istringstream in(command);std::string op,extra;float velocity,acceleration;int position,threshold,only,timeout;
        in>>op>>grant>>id>>kind>>velocity>>acceleration>>position>>threshold>>only>>timeout>>tcp;
        for(auto& v:target)in>>v;for(auto& v:reference)in>>v;
        if(kind=="circle")for(auto& v:via)in>>v;
        feedback={};
        try{
            if(!in||(in>>extra)||id.size()!=32||!can_move(sdk,grant)||!std::isfinite(velocity)||velocity<=0||velocity>10||
               !std::isfinite(acceleration)||acceleration<=0||timeout<5||timeout>600||hex_name(sdk.get_tcp())!=tcp||!near_pose(forward(sdk,{0,0,90,0,90,0}),reference))throw std::runtime_error("control_or_tcp_changed");
            if(kind!="joint"&&kind!="line"&&kind!="circle"&&kind!="gripper")throw std::runtime_error("invalid_kind");
            if(only&&kind!="joint")throw std::runtime_error("j1_only");
            if(kind!="gripper"&&acceleration>10)throw std::runtime_error("speed_limit");
            if(kind=="joint"){
                if(!joint_six(target))throw std::runtime_error("joint_limit");
                if(only){auto q=joints_now(sdk);for(int i=1;i<6;i++)if(std::abs(q[i]-target[i])>.5)throw std::runtime_error("j1_only");if(std::abs(std::abs(q[0]-target[0])-180)>.5)throw std::runtime_error("j1_only");float turn=target[0]>q[0]?180:-180;target=q;target[0]+=turn;if(!joint_six(target))throw std::runtime_error("joint_limit");}
            }else if(!finite_six(target))throw std::runtime_error("invalid_target");
            if(kind=="circle"&&!valid_arc(task_now(sdk),via,target))throw std::runtime_error("invalid_arc");
            active=true;stopping=false;started=last_poll=now_ms();deadline=started+timeout*1000;in_move=true;
            bool accepted=true;
            if(kind=="joint")accepted=sdk.amovej(target.data(),velocity,acceleration,0,MOVE_MODE_ABSOLUTE);
            else if(kind=="line"){float v[]={velocity,velocity},a[]={acceleration,acceleration};accepted=sdk.amovel(target.data(),v,a,0,MOVE_MODE_ABSOLUTE,MOVE_REFERENCE_BASE);}
            else if(kind=="circle"){float p[2][6],v[]={velocity,velocity},a[]={acceleration,acceleration};std::copy(via.begin(),via.end(),p[0]);std::copy(target.begin(),target.end(),p[1]);accepted=sdk.amovec(p,v,a,0,MOVE_MODE_ABSOLUTE,MOVE_REFERENCE_BASE);}
            else{
                port=std::make_unique<FlangePort>(FlangePort{sdk,grant});hand=std::make_unique<rhp12::Hand<FlangePort>>(*port);
                serial=port->open();
                if(!serial)throw std::runtime_error("serial_open");hand->start(position,std::lround(velocity*100),std::lround(acceleration),threshold,timeout,feedback);
            }
            if(!accepted)throw std::runtime_error("command_refused");
        }catch(const std::exception& e){finish(sdk,active?"unknown":"failed",e.what());}
    }
    void poll(DRAFramework::CDRFLEx& sdk){
        if(!active||now_ms()-last_poll<100)return;last_poll=now_ms();
        try{
            if(down||!held||grant!=grant_number.load()||!initialized||!last_frame.load()||now_ms()-last_frame.load()>2000||
               !mode_verified||sdk.get_robot_mode()!=ROBOT_MODE_AUTONOMOUS||sdk.get_robot_system()!=ROBOT_SYSTEM_REAL)throw std::runtime_error("control_lost");
            if(hex_name(sdk.get_tcp())!=tcp||!near_pose(forward(sdk,{0,0,90,0,90,0}),reference))throw std::runtime_error("tcp_changed");
            if(now_ms()>deadline)throw std::runtime_error("operation_timeout");
            if(kind=="gripper"){if(hand->tick(feedback))finish(sdk,stopping?"cancelled":"completed",stopping?"stopped":"gripper_verified");return;}
            const auto state=sdk.get_robot_state();
            if(state!=STATE_MOVING&&state!=STATE_STANDBY)throw std::runtime_error("robot_state");
            if(state==STATE_STANDBY&&now_ms()-started>=250&&sdk.check_motion()==0){
                bool reached=kind=="joint";if(reached){auto q=joints_now(sdk);for(int i=0;i<6;i++)reached=reached&&std::abs(q[i]-target[i])<=.5;}
                else reached=near_pose(task_now(sdk),target);
                finish(sdk,stopping?"cancelled":reached?"completed":"unknown",stopping?"stopped":reached?"target_verified":"target_not_reached");
            }
        }catch(const std::exception& e){
            if(!down){try{if(kind=="gripper"&&hand)hand->halt(feedback);else sdk.stop(STOP_TYPE_SLOW);}catch(...) {}}
            finish(sdk,"unknown",e.what());
        }
    }
};
static ScenarioRun scenario_run;
