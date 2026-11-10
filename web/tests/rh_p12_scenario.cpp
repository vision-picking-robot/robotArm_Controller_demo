#include "../rosbridge/host-sdk/rh_p12.hpp"
#include <map>
#include <cassert>
using namespace rhp12;
struct Port {
    std::map<int,int> r{{0,35074},{5,5<<8},{19,250},{20,3447},{22,2970},{24,1150},{256,1},{275,120},{287,50},{290,100},{296,240}};
    Bytes reply;std::vector<Bytes> writes;bool empty=false,bad_crc=false;
    void check(){}
    void write(Bytes b){assert(crc(b)==0);int address=word(b,2);
        if(b[1]==3){reply={1,3,uint8_t(word(b,4)*2)};for(int i=0;i<word(b,4);i++){int x=r[address+i];reply.push_back(x>>8);reply.push_back(x);}}
        else {writes.push_back(b);if(b[1]==16){r[address]=word(b,7);r[address+1]=word(b,9);if(address==282)r[290]=empty?r[address]:std::min(430,r[address]);}else r[address]=word(b,4);reply.assign(b.begin(),b.begin()+6);}
        reply=frame(reply);if(bad_crc)reply.back()^=1;
    }
    Bytes read(float){Bytes b=reply;reply.clear();return b;}
};
int main(){
    Port p;Status s;Hand<Port> h(p);h.inspect(s);assert(p.writes.empty()&&s.voltage_tenths==240);
    h.start(700,200,3000,30,5,s);
    assert(word(p.writes[0],2)==278&&word(p.writes[1],2)==280&&word(p.writes[2],2)==282);
    bool completed=false;for(int i=0;i<15&&!completed;i++){std::this_thread::sleep_for(std::chrono::milliseconds(100));completed=h.tick(s);}
    assert(completed&&s.contact&&s.position==430&&p.r[282]==430&&p.r[256]==1);
    Port empty;empty.empty=true;Hand<Port> e(empty);e.start(700,200,3000,30,5,s);
    try{e.tick(s);assert(false);}catch(const std::runtime_error& x){assert(std::string(x.what())=="empty_grip");}
    Port bad;bad.bad_crc=true;Hand<Port> d(bad);try{d.start(700,200,3000,30,5,s);assert(false);}catch(...){assert(bad.writes.empty());}
    Port stop;Hand<Port> halted(stop);halted.start(500,1000,3000,0,5,s);halted.halt(s);assert(stop.r[282]==stop.r[290]&&stop.r[256]==1);
}
