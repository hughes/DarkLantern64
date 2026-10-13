#include "play_session.h"
#include <iostream>

using Json=nlohmann::json;
static void Check(bool condition,const char* label) {
    if(!condition)throw std::runtime_error(label);
}
static Json Fixture() {
    return Json::parse(R"({
        "format":"darklantern-play","version":1,"title":"Exact native floor",
        "fingerprint":"floor-test","colliders":[],"enemies":[],"lights":[],
        "test_starts":[],"spawn":[0,-0.25,-1],"spawn_yaw":0,
        "control":[20,0,20],"objective":[20,0,20],"environment":{},
        "floors":[
          {"vertices":[[-1,-0.5,-2],[1,-0.5,-2],[1,0.5,2]]},
          {"vertices":[[-1,-0.5,-2],[1,0.5,2],[-1,0.5,2]]}
        ]
    })");
}
static void Reject(Json packet,const char* label) {
    try { DlPlaySession ignored(std::move(packet)); }
    catch(const std::exception&) { return; }
    throw std::runtime_error(label);
}
int main() {
    try {
        DlPlaySession session(Fixture());
        Check(session.level.collider_count==0&&session.level.floor_count==2,"Floor-only packet not wired");
        for(int tick=0;tick<60;++tick) {
            session.Step({.forward=1});
            Check(session.game.grounded,"Native ramp lost support");
            Check(std::abs(session.game.player.y-.25f*session.game.player.z)<.002f,"Native ramp was flattened");
        }
        Check(session.game.player.z>1,"Native packet failed to walk uphill");
        auto malformed=Fixture();malformed["floors"][0]["vertices"]=Json::array({Json::array({0,0,0})});
        Reject(malformed,"Malformed floor vertex count accepted");
        malformed=Fixture();malformed["floors"][0]["vertices"][2]=malformed["floors"][0]["vertices"][1];
        Reject(malformed,"Degenerate floor accepted");
        malformed=Fixture();malformed["floors"][0]["vertices"][2][1]=9;
        Reject(malformed,"Unsupported floor grade accepted");
        malformed=Fixture();malformed["floors"]=Json::object();Reject(malformed,"Non-array floor section accepted");
        malformed=Fixture();for(int i=0;i<319;++i)malformed["colliders"].push_back(
            {{"center",{0,-1,0}},{"half_size",{1,1,1}},{"yaw",0},{"door",false}});
        Reject(malformed,"Combined floor/collider capacity accepted");
        auto legacy=Fixture();legacy.erase("floors");legacy["spawn"]={0,0,0};
        legacy["colliders"].push_back({{"center",{0,-.5,0}},{"half_size",{2,.5,2}},{"yaw",0},{"door",false}});
        DlPlaySession old(std::move(legacy));Check(old.game.grounded&&old.level.floor_count==0,"Legacy packet changed");
        std::cout<<"PASS exact native floor packet walking, rejection and legacy compatibility\n";
        return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
