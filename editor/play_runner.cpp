#include "play_session.h"
#include <fstream>
#include <iostream>
int main(int argc,char** argv) {
    try {
        if(argc<2||argc>4||(argc==4&&std::string(argv[3])!="--states"))throw std::runtime_error("Usage: play_runner play.json [replay.json [--states]]");
        std::ifstream source(argv[1]);auto package=DlPlaySession::Json::parse(source);
        DlPlaySession::Json replay;
        if(argc>=3){std::ifstream input(argv[2]);replay=DlPlaySession::Json::parse(input);}
        DlPlaySession::Json states=DlPlaySession::Json::array();
        DlPlaySession session(std::move(package),replay.is_object()?replay.value("start_preset",std::string("default")):"default");
        session.settleBeforeTick=replay.is_object()&&DlPlaySession::SettledBoundary(replay);
        if(!replay.is_null()) {
            if(replay.at("format")!="darklantern-replay"||replay.at("version")!=1||replay.at("tick_rate")!=DL_TICK_RATE||
                replay.at("fingerprint")!=session.package.at("fingerprint"))throw std::runtime_error("Replay/content mismatch");
            if(!replay.at("inputs").is_array()||replay.at("inputs").size()>DlPlaySession::maxRecords)throw std::runtime_error("Invalid replay inputs");
            for(const auto& row:replay.at("inputs")){session.Step(DlPlaySession::Input(row));if(argc==4)states.push_back(session.State());}
        }
        std::cout<<DlPlaySession::Json{{"state",session.State()},{"trace",session.trace},{"states",states}}.dump(2)<<'\n';
        return 0;
    } catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
