#pragma once
#include "tick.h"
#include "stream_play.h"
#include "lighting_policy.h"
#include <nlohmann/json.hpp>
#include <cmath>
#include <deque>
#include <stdexcept>
#include <string>
#include <vector>

// Headless as well as in-editor: no LightEngine, window, renderer or Jolt types.
class DlPlaySession {
public:
    using Json=nlohmann::json;
    Json package, trace=Json::array(), inputs=Json::array();
    DlLevel level{};
    DlGame game{};
    DlTickClock clock{};
    DlInput injected{};
    int preset=-1;
    bool inputOverride=false, diagnostics=true;
    bool settleBeforeTick=false;
    uint64_t traceDropped=0;
    static constexpr size_t maxRecords=36000;
    DlPlaySession(const DlPlaySession&)=delete;
    DlPlaySession& operator=(const DlPlaySession&)=delete;
    explicit DlPlaySession(Json source, const std::string& start="default") : package(std::move(source)) {
        if(package.at("format")!="darklantern-play" || package.at("version")!=1)
            throw std::runtime_error("Unsupported desktop play package");
        for(const auto& row:package.at("colliders"))
            boxes.push_back({V(row.at("center")),V(row.at("half_size")),row.at("yaw"),row.at("door")});
        if(package.contains("floors")&&!package.at("floors").is_array())throw std::runtime_error("Floors must be an array");
        if(package.contains("floors"))for(const auto& row:package.at("floors")) {
            const auto& points=row.at("vertices");
            if(!points.is_array()||points.size()!=3)throw std::runtime_error("Floor requires three XYZ vertices");
            DlFloorTriangle floor{{V(points[0]),V(points[1]),V(points[2])}};
            const auto a=floor.vertices[0],b=floor.vertices[1],c=floor.vertices[2];
            const float bx=b.x-a.x,bz=b.z-a.z,cx=c.x-a.x,cz=c.z-a.z,by=b.y-a.y,cy=c.y-a.y;
            const float determinant=bx*cz-bz*cx;
            if(!std::isfinite(determinant)||std::abs(determinant)<=1e-8f)
                throw std::runtime_error("Degenerate or vertical floor footprint");
            const float gx=(by*cz-bz*cy)/determinant,gz=(bx*cy-by*cx)/determinant;
            if(!std::isfinite(gx)||!std::isfinite(gz)||gx*gx+gz*gz>1.000001f)
                throw std::runtime_error("Floor grade exceeds supported 45 degrees");
            floors.push_back(floor);
        }
        if((!package.contains("stream")&&boxes.empty()&&floors.empty())||boxes.size()+floors.size()>320||floors.size()>DL_MAX_FLOORS||
            package.at("enemies").size()>DL_MAX_ENEMIES)
            throw std::runtime_error("Play package exceeds ROM simulation capacity");
        for(const auto& row:package.at("lights"))
            lights.push_back({V(row.at("position")),row.at("radius"),row.at("intensity"),V(row.at("color"))});
        routes.resize(package.at("enemies").size());
        for(size_t i=0;i<routes.size();++i) {
            const auto& row=package.at("enemies")[i];
            for(const auto& point:row.at("patrol")) routes[i].push_back(V(point));
            if(routes[i].size()>32)throw std::runtime_error("Patrol exceeds ROM capacity");
            DlEnemyDef enemy{};
            enemy.id=row.at("id").get_ref<const std::string&>().c_str();
            const std::string type=row.at("enemy_type");
            bool known=false;
#define DL_ENEMY_TYPE(symbol,id,label,speed,sight,hearing) if(type==id){enemy.type=DL_ENEMY_##symbol;known=true;}
#include "enemy_types.def"
#undef DL_ENEMY_TYPE
            if(!known)throw std::runtime_error("Unknown cooked enemy type");
            enemy.behavior=row.at("behavior")=="patrol"?DL_BEHAVIOR_PATROL:DL_BEHAVIOR_SENTRY;
            enemy.spawn=V(row.at("spawn")); enemy.yaw=row.at("yaw");
            enemy.patrol=routes[i].data();enemy.patrol_count=(int)routes[i].size();
            enemy.speed=row.at("speed");enemy.sight_range=row.at("sight_range");enemy.hearing_range=row.at("hearing_range");
            enemies.push_back(enemy);
        }
        for(const auto& row:package.at("test_starts")) {
            DlStartPreset value{};
            value.id=row.at("id").get_ref<const std::string&>().c_str();
            value.label=row.at("label").get_ref<const std::string&>().c_str();
            value.position=V(row.at("position"));value.yaw=row.at("yaw");value.pitch=row.at("pitch");
            value.door_open=row.at("door_open");value.crouched=row.at("crouched");
            if(start==value.id)preset=(int)starts.size();
            starts.push_back(value);
        }
        if(start!="default"&&preset<0)throw std::runtime_error("Unknown native play test start");
        level.version=2;level.title=package.at("title").get_ref<const std::string&>().c_str();
        level.colliders=boxes.data();level.collider_count=(int)boxes.size();
        level.floors=floors.data();level.floor_count=(int)floors.size();
        level.enemies=enemies.data();level.enemy_count=(int)enemies.size();
        level.lights=lights.data();level.light_count=(int)lights.size();
        level.test_starts=starts.data();level.test_start_count=(int)starts.size();
        level.spawn=V(package.at("spawn"));level.spawn_yaw=package.at("spawn_yaw");
        level.control=V(package.at("control"));level.objective=V(package.at("objective"));
        if(package.contains("objects")) {
            if(package.at("objects").size()>DL_MAX_OBJECTS)throw std::runtime_error("Too many interactive objects");
            for(const auto& row:package.at("objects")) {
                DlObjectDef object{};object.id=row.at("id").get_ref<const std::string&>().c_str();
                const auto kind=row.at("kind");
                object.kind=kind=="door"?DL_OBJECT_DOOR:kind=="control"?DL_OBJECT_CONTROL:kind=="loot"?DL_OBJECT_LOOT:DL_OBJECT_OBJECTIVE;
                object.position=V(row.at("position"));object.targets=row.at("targets").get<uint64_t>();
                object.value=row.at("value");object.required_loot=row.at("required_loot");object.radius=row.at("radius");object.zone=row.at("zone");
                objects.push_back(object);
            }
            for(const auto& index:package.at("collider_objects"))colliderObjects.push_back(index.get<int16_t>());
            if(colliderObjects.size()!=boxes.size())throw std::runtime_error("Collider/object mapping mismatch");
            level.objects=objects.empty()?&emptyObject_:objects.data();level.object_count=(int)objects.size();
            level.collider_objects=colliderObjects.data();
        }
        if(package.contains("foot_contacts")) {
            for(const auto& row:package.at("foot_contacts")) {
                DlFootContacts track{};track.stride=row.at("stride");
                if(row.at("phases").size()>8)throw std::runtime_error("Too many foot contacts");
                for(const auto& phase:row.at("phases"))track.phases[track.count++]=phase.get<uint16_t>();
                contacts.push_back(track);
            }
            if(contacts.size()!=enemies.size())throw std::runtime_error("Foot contact/enemy mapping mismatch");
            level.foot_contacts=contacts.data();
        }
        const auto& env=package.at("environment");
        if(!env.empty()) {
            auto& e=level.environment;e.enabled=true;
            e.ambient=V(env.at("ambient"));e.moon_direction=V(env.at("moon_direction"));e.moon_color=V(env.at("moon_color"));
            e.moon_intensity=env.at("moon_intensity");e.fog_color=V(env.at("fog_color"));
            e.fog_near=env.at("fog_near");e.fog_far=env.at("fog_far");
            e.sky_top=V(env.at("sky_top"));e.sky_bottom=V(env.at("sky_bottom"));e.exposure=env.at("exposure");
        }
        if(package.contains("stream"))stream_=std::make_unique<DlStreamPlay>(package,level);
        Restart();
    }
    static DlVec3 V(const Json& row) {
        if(!row.is_array()||row.size()!=3)throw std::runtime_error("Expected XYZ vector");
        DlVec3 v{row[0],row[1],row[2]};
        if(!std::isfinite(v.x)||!std::isfinite(v.y)||!std::isfinite(v.z))throw std::runtime_error("Non-finite vector");
        return v;
    }
    static Json J(DlVec3 p){return Json::array({p.x,p.y,p.z});}
    static Json InputJson(DlInput i) {return {{"forward",i.forward},{"strafe",i.strafe},{"turn",i.turn},{"look",i.look},
        {"crouch",i.crouch},{"use",i.use},{"noise",i.noise},{"jump",i.jump},{"restart",i.restart}};}
    static DlInput Input(const Json& row) {
        if(!row.is_object())throw std::runtime_error("Input must be an object");
        DlInput i{};
        for(const auto& [key,value]:row.items()) {
            float* axis=key=="forward"?&i.forward:key=="strafe"?&i.strafe:key=="turn"?&i.turn:key=="look"?&i.look:nullptr;
            if(axis){*axis=value.get<float>();if(!std::isfinite(*axis)||std::abs(*axis)>1)throw std::runtime_error("Input axis outside -1..1");continue;}
            bool* edge=key=="crouch"?&i.crouch:key=="use"?&i.use:key=="noise"?&i.noise:key=="jump"?&i.jump:key=="restart"?&i.restart:nullptr;
            if(!edge||!value.is_boolean())throw std::runtime_error("Unknown input field or invalid button");
            *edge=value.get<bool>();
        }
        return i;
    }
    static bool SettledBoundary(const Json& replay) {
        const auto boundary=replay.value("stream_boundary",std::string("frame"));
        if(boundary!="frame"&&boundary!="settled")throw std::runtime_error("Replay stream boundary must be frame or settled");
        return boundary=="settled";
    }
    void ResetGame(){if(stream_)stream_->Restart(game,preset);else dl_game_start(&game,&level,preset);}
    void Restart(){ResetGame();clock={};trace=Json::array();inputs=Json::array();traceDropped=0;injected={};}
    bool Streamed() const{return stream_!=nullptr;}
    DlStreamPlay* Stream(){return stream_.get();}
    const DlStreamPlay* Stream() const{return stream_.get();}
    void ServiceStream(){if(stream_)stream_->Service(game);}
    void ConfigureStreamTransport(const Json& options){if(!stream_)throw std::runtime_error("Session is not streamed");stream_->ConfigureTransport(options);}
    bool StreamVisible(const std::string& id) const{return !stream_||stream_->Visible(id);}
    static void Tick(void* context,const DlInput* input,uint64_t tick) {
        auto& s=*static_cast<DlPlaySession*>(context);
        if(s.inputs.size()>=maxRecords)throw std::runtime_error("Recording capacity reached; restart or export the session");
        if(s.stream_){if(s.settleBeforeTick)s.stream_->Settle(s.game);else s.stream_->Service(s.game);}
        const auto before=s.game;
        DlInput filtered=*input;if(s.stream_)s.stream_->Filter(s.game,filtered);
        if(input->restart)s.ResetGame();
        else dl_game_update(&s.game,&filtered,DL_TICK_SECONDS);
        if(s.game.elapsed<before.elapsed&&!input->restart)s.ResetGame();
        s.inputs.push_back(InputJson(*input));
        if(s.diagnostics) {
            for(unsigned i=0;i<s.game.event_count;++i) {
                const auto& e=s.game.events[i];
                s.Event({{"tick",tick},{"kind","simulation_event"},{"event",e.kind},{"position",J(e.position)},
                    {"time",e.time},{"strength",e.strength},{"actor",e.actor<0?"player":s.level.enemies[e.actor].id},{"detail",e.detail},{"previous",e.previous}});
            }
            for(int i=0;i<s.game.enemy_count;++i) {
                const auto& e=s.game.enemies[i];const auto& old=before.enemies[i];
                if(e.state!=old.state||e.sees_player!=old.sees_player||e.heard_sound!=old.heard_sound)
                    s.Event({{"tick",tick},{"kind","guard"},{"id",s.level.enemies[i].id},{"state",dl_guard_state_name(e.state)},
                        {"sees_player",e.sees_player},{"heard_sound",e.heard_sound},{"awareness",e.awareness},{"visibility",s.game.visibility}});
            }
            if(s.game.door_open!=before.door_open)s.Event({{"tick",tick},{"kind","door"},{"open",s.game.door_open}});
            for(int i=0;i<s.level.object_count;++i){const uint64_t bit=UINT64_C(1)<<i;
                if(dl_game_object_open(&s.game,i)!=dl_game_object_open(&before,i)||
                    ((s.game.collected_objects^before.collected_objects)&bit)||((s.game.completed_objectives^before.completed_objectives)&bit))
                    s.Event({{"tick",tick},{"kind","object"},{"id",s.level.objects[i].id},{"open",dl_game_object_open(&s.game,i)},
                        {"collected",(s.game.collected_objects&bit)!=0},{"completed",(s.game.completed_objectives&bit)!=0},{"loot_value",s.game.loot_value}});
            }
            if(s.game.complete!=before.complete||s.game.caught!=before.caught)s.Event({{"tick",tick},{"kind","mission"},{"complete",s.game.complete},{"caught",s.game.caught}});
        }
    }
    void Event(Json row){if(trace.size()>=2048){trace.erase(trace.begin());++traceDropped;}trace.push_back(std::move(row));}
    void Advance(double dt,DlInput input){dl_tick_advance(&clock,dt,input,Tick,this);}
    void Step(DlInput input){dl_tick_step(&clock,input,Tick,this);}
    Json RendererRuntime() const {
        if(!stream_)return {{"supported",false},{"basis","unknown"},{"cases",nullptr},{"change_cases",nullptr},
            {"models",nullptr},{"door_object_count",nullptr},{"identity",{{"play_fingerprint",package.at("fingerprint")},
                {"renderer_source_sha256",nullptr},{"package_sha256",nullptr},{"build_id",nullptr}}},
            {"reason","Resident Native packets do not carry the ROM model and lighting-cache definitions"}};
        const DlLevel& published=*game.level;
        const DlLightingSettings settings{true,false,false,dl_lighting_independent_doors(&published)};
        DlLightingPolicyReport initial{},changed{};
        dl_lighting_policy_report(&published,settings,false,&initial);dl_lighting_policy_report(&published,settings,true,&changed);
        const auto cases=[](const DlLightingPolicyReport& report){Json result=Json::array();
            for(unsigned mode=0;mode<DL_LIGHT_MODE_COUNT;++mode)for(unsigned reason=0;reason<DL_LIGHT_REASON_COUNT;++reason){
                const auto& count=report.cases[mode][reason];if(count.models)result.push_back({{"mode",dl_lighting_mode_name((DlLightingMode)mode)},
                    {"reason",dl_lighting_reason_name((DlLightingReason)reason)},{"models",count.models},{"triangles",count.triangles}});}
            return result;};
        Json models=Json::array();
        for(int i=0;i<published.model_count;++i){const auto& model=published.models[i];const auto& mesh=published.meshes[model.mesh];
            const auto d=dl_lighting_decision(&published,i,settings);
            models.push_back({{"model_index",i},{"entity_id",model.id?Json(model.id):Json(nullptr)},
                {"region_id",uint32_t(mesh.stream_key>>40)},{"material_id",uint32_t((mesh.stream_key>>8)&UINT32_MAX)},
                {"role",model.role},{"triangles",d.triangles},{"eligible",d.static_eligible},
                {"inline_payload",model.baked_colors[0]!=nullptr&&model.baked_colors[1]!=nullptr},{"inline_eligible",d.inline_eligible},
                {"mode",dl_lighting_mode_name(d.mode)},{"reason",dl_lighting_reason_name(d.reason)},
                {"change_mode",dl_lighting_mode_name(d.change_mode)},{"change_reason",dl_lighting_reason_name(d.change_reason)}});
        }
        const auto state=stream_->State(game);
        return {{"supported",true},{"basis","published_model_policy"},{"scope","published_models"},
            {"identity",{{"renderer_source_sha256",published.renderer_source_sha256?Json(published.renderer_source_sha256):Json(nullptr)},
                {"package_sha256",package.at("stream").at("package_sha256")},{"play_fingerprint",package.at("fingerprint")},{"build_id",nullptr}}},
            {"models",initial.models},{"triangles",initial.triangles},{"door_object_count",settings.independent_doors},
            {"revision",state.at("revision")},{"published_mask",state.at("published_mask")},
            {"settings",{{"bake_enabled",settings.bake_enabled},{"standalone_loaded",settings.standalone_loaded},
                {"standalone_rejected",settings.standalone_rejected},{"independent_doors",settings.independent_doors}}},
            {"cases",cases(initial)},{"change_cases",cases(changed)},{"model_entries",models},
            {"measured_timing",nullptr},{"measured_heap",nullptr},
            {"limitations",Json::array({"Shared target policy applied to decoded published models; desktop GPU presentation and ROM timing remain separate observations",
                "ROM build flags may override these host classification settings; verified ROM records carry their own build identity"})}};
    }
    Json State() const {
        Json actors=Json::array(),objectState=Json::array(),events=Json::array();
        for(unsigned i=0;i<game.event_count;++i){const auto& e=game.events[i];events.push_back({
            {"kind",e.kind},{"position",J(e.position)},{"time",e.time},{"strength",e.strength},
            {"actor",e.actor},{"detail",e.detail},{"previous",e.previous}});}
        for(int i=0;i<level.object_count;++i) {
            const auto& o=level.objects[i];const uint64_t bit=UINT64_C(1)<<i;
            objectState.push_back({{"id",o.id},{"kind",o.kind},{"position",J(o.position)},
                {"open",dl_game_object_open(&game,i)},{"collected",(game.collected_objects&bit)!=0},
                {"completed",(game.completed_objectives&bit)!=0},{"required_loot",o.required_loot},{"targets",o.targets}});
        }
        for(int i=0;i<game.enemy_count;++i){const auto& e=game.enemies[i];
            const DlVec3 eye{e.position.x,e.position.y+1.6f,e.position.z};
            actors.push_back({{"id",level.enemies[i].id},{"position",J(e.position)},{"yaw",e.yaw},{"state",dl_guard_state_name(e.state)},
                {"awareness",e.awareness},{"patrol_index",e.patrol_index},{"sees_player",e.sees_player},{"heard_sound",e.heard_sound},
                {"sight_signal",e.sight_signal},{"player_distance",e.player_distance},{"in_viewcone",e.in_viewcone},{"perception_ray_passed",e.unoccluded},
                {"investigate_target",J(e.investigate_target)},{"line_of_sight",dl_line_of_sight(&game,eye,dl_player_eye(&game))}});}
        Json result={{"fingerprint",package.at("fingerprint")},{"tick",clock.tick},{"tick_rate",DL_TICK_RATE},
            {"dropped_seconds",clock.dropped_seconds},{"player",J(game.player)},{"yaw",game.yaw},{"pitch",game.pitch},
            {"grounded",game.grounded},{"crouched",game.crouched},{"visibility",game.visibility},{"elapsed",game.elapsed},{"events",events},
            {"door_open",game.door_open},{"complete",game.complete},{"caught",game.caught},{"enemies",actors},{"objects",objectState},{"loot_value",game.loot_value},
            {"input_override",inputOverride},{"input",InputJson(injected)},{"recorded_ticks",inputs.size()},{"trace_dropped",traceDropped},{"event_overflow",game.event_overflow},
            {"open_objects",game.open_objects},{"collected_objects",game.collected_objects},{"completed_objectives",game.completed_objectives},{"active_enemy_mask",game.active_enemy_mask},
            {"stream_boundary",settleBeforeTick?"settled":"frame"}};
        if(stream_)result["stream"]=stream_->State(game);
        result["renderer_runtime"]=RendererRuntime();
        return result;
    }
private:
    std::vector<DlCollider> boxes;
    std::vector<DlFloorTriangle> floors;
    std::vector<DlLight> lights;
    std::vector<DlEnemyDef> enemies;
    std::vector<std::vector<DlVec3>> routes;
    std::vector<DlStartPreset> starts;
    std::vector<DlObjectDef> objects;
    std::vector<int16_t> colliderObjects;
    DlObjectDef emptyObject_{};
    std::vector<DlFootContacts> contacts;
    std::unique_ptr<DlStreamPlay> stream_;
};
