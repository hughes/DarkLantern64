#pragma once
#include "stream_coordinator.h"
#include <nlohmann/json.hpp>
#include <array>
#include <algorithm>
#include <fstream>
#include <iomanip>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unordered_map>
#include <deque>

// Desktop transport owns only the directory, fixed slot pool and shared
// bootstrap. Static geometry/collision enters exclusively through DLM decoding.
class DlStreamPlay {
public:
    using Json=nlohmann::json;
    DlStreamCoordinator coordinator{};
    struct Transport {
        uint32_t read_delay_ticks=0,read_slice_bytes=256,renderer_delay_ticks=0,rsp_delay_ticks=0,rdp_delay_ticks=0;
    };
    void ConfigureTransport(const Json& args) {
        Transport next=transport_;
        const auto field=[&](const char* name,uint32_t& value,uint32_t low,uint32_t high){
            if(!args.contains(name))return;
            if(!args.at(name).is_number_integer()||args.at(name)<low||args.at(name)>high)
                throw std::runtime_error(std::string(name)+" is outside its supported range");
            value=args.at(name).get<uint32_t>();
        };
        field("read_delay_ticks",next.read_delay_ticks,0,600);
        field("read_slice_bytes",next.read_slice_bytes,1,4096);
        field("renderer_delay_ticks",next.renderer_delay_ticks,0,600);
        field("rsp_delay_ticks",next.rsp_delay_ticks,0,600);
        field("rdp_delay_ticks",next.rdp_delay_ticks,0,600);
        transport_=next;transportEnabled_=true;
    }
    Json TransportState() const {return {{"read_delay_ticks",transport_.read_delay_ticks},{"read_slice_bytes",transport_.read_slice_bytes},
        {"renderer_delay_ticks",transport_.renderer_delay_ticks},{"rsp_delay_ticks",transport_.rsp_delay_ticks},{"rdp_delay_ticks",transport_.rdp_delay_ticks},
        {"service_tick",serviceTick_},{"submitted_serial",submitted_},{"read_stalls",coordinator.world.stats.read_stalls}};}
    const DlLevel* PublishedLevel() const {return dl_stream_scene_level(&coordinator.scene);}
    explicit DlStreamPlay(Json& package,DlLevel& bootstrap):package_(package),bootstrap_(bootstrap) {
        const auto& data=package_.at("stream");
        file_.open(data.at("package_path").get<std::string>(),std::ios::binary);
        if(!file_)throw std::runtime_error("Cannot open native mission package");
        file_.seekg(0,std::ios::end);
        if(file_.tellg()!=std::streamoff(data.at("file_bytes").get<uint32_t>()))throw std::runtime_error("Mission package size mismatch");
        file_.seekg(0);
        if(Sha256(file_)!=data.at("package_sha256").get<std::string>())throw std::runtime_error("Mission package SHA-256 mismatch");
        file_.clear();file_.seekg(0);
        const auto directoryBytes=data.at("directory_bytes").get<uint32_t>();
        if(directoryBytes>65536)throw std::runtime_error("Mission directory capacity exceeded");
        directory_.resize(directoryBytes);
        file_.read(reinterpret_cast<char*>(directory_.data()),directory_.size());
        if(!file_||dl_mission_open(directory_.data(),directory_.size(),data.at("file_bytes"),&mission_)!=DL_MISSION_OK)
            throw std::runtime_error("Mission directory validation failed");
        for(const auto& row:data.at("runtime_debug_map").at("entities"))if(row.at("kind")=="static") {
            DlMissionEntity entity{};
            if(dl_mission_entity(&mission_,row.at("id"),&entity)!=DL_MISSION_OK||entity.home_region_id!=row.at("home_region").get<uint32_t>())
                throw std::runtime_error("Mission viewport/entity mapping mismatch");
            staticHomes_.emplace(row.at("source_id").get<std::string>(),entity.home_region_id);
        }
        definition_.package_path=data.at("package_path").get_ref<const std::string&>().c_str();
        definition_.package_sha256=data.at("package_sha256").get_ref<const std::string&>().c_str();
        definition_.directory_bytes=data.at("directory_bytes");definition_.file_bytes=data.at("file_bytes");
        definition_.max_region_bytes=data.at("max_region_bytes");definition_.region_count=data.at("region_count");
        definition_.max_resident_regions=data.at("max_resident_regions");
        if(definition_.max_region_bytes>65536||!definition_.max_resident_regions||definition_.max_resident_regions>5)
            throw std::runtime_error("Mission exceeds bounded slot pool");
        modelLinks_=data.at("model_entity_ids").get<std::vector<uint32_t>>();
        objectLinks_=data.at("object_entity_ids").get<std::vector<uint32_t>>();
        enemyLinks_=data.at("enemy_entity_ids").get<std::vector<uint32_t>>();
        definition_.model_entity_ids=modelLinks_.data();definition_.object_entity_ids=objectLinks_.data();definition_.enemy_entity_ids=enemyLinks_.data();
        definition_.keep_neighbors_resident=data.at("visibility")=="all-regions";
        for(const auto& row:data.at("materials")) {
            DlStreamMaterial m{};m.id=row.at("id");m.double_sided=row.at("double_sided");
            for(int i=0;i<4;++i)m.color[i]=row.at("color").at(i);
            materials_.push_back(m);
        }
        definition_.materials=materials_.data();definition_.material_count=(uint32_t)materials_.size();
        if(data.at("visibility")=="authored-sets") {
            definition_.policy=DL_STREAM_POLICY_AUTHORED;
            for(const auto& row:data.at("policy_regions"))policy_.push_back({V(row.at("activation_bounds")[0]),V(row.at("activation_bounds")[1]),row.at("working_mask")});
            if(policy_.size()!=definition_.region_count)throw std::runtime_error("Mission policy/region mapping mismatch");
            for(const auto& row:data.at("transitions"))transitions_.push_back({row.at("from_region"),row.at("to_region"),V(row.at("bounds")[0]),V(row.at("bounds")[1]),row.at("door_entity_id")});
            definition_.policy_regions=policy_.data();definition_.transitions=transitions_.data();definition_.transition_count=(uint32_t)transitions_.size();
        }
        for(const auto& row:package_.at("stream_meshes")) {
            vertices_.emplace_back();indices_.push_back(row.at("indices").get<std::vector<uint16_t>>());
            for(const auto& point:row.at("vertices"))vertices_.back().push_back(V(point));
            DlMesh mesh{};mesh.vertices=vertices_.back().data();mesh.vertex_count=(int)vertices_.back().size();
            mesh.indices=indices_.back().data();mesh.index_count=(int)indices_.back().size();meshes_.push_back(mesh);
        }
        for(const auto& row:package_.at("stream_models")) {
            DlModelInstance m{};m.id=row.at("id").get_ref<const std::string&>().c_str();m.mesh=row.at("mesh");
            m.position=V(row.at("position"));m.rotation=V(row.at("rotation"));m.scale=V(row.at("scale"));m.enemy_index=row.at("enemy_index");m.texture=-1;
            const auto role=row.at("role");m.role=role=="guard"?DL_MODEL_GUARD:role=="door"?DL_MODEL_DOOR:role=="control"?DL_MODEL_CONTROL:role=="loot"?DL_MODEL_LOOT:DL_MODEL_OBJECTIVE;
            std::fill(std::begin(m.color),std::end(m.color),uint8_t(255));models_.push_back(m);
        }
        if(modelLinks_.size()!=models_.size()||objectLinks_.size()!=size_t(bootstrap_.object_count)||enemyLinks_.size()!=size_t(bootstrap_.enemy_count))
            throw std::runtime_error("Mission bootstrap/entity mapping mismatch");
        bootstrap_.meshes=meshes_.data();bootstrap_.mesh_count=(int)meshes_.size();bootstrap_.models=models_.data();bootstrap_.model_count=(int)models_.size();bootstrap_.stream=&definition_;
        const size_t capacity=(definition_.max_region_bytes+15u)&~size_t(15);
        buffers_.resize(definition_.max_resident_regions);slots_.resize(buffers_.size());
        for(size_t i=0;i<slots_.size();++i){buffers_[i].resize(capacity);slots_[i].buffer=buffers_[i].data();slots_[i].capacity=capacity;}
        Initialize();
    }
    ~DlStreamPlay(){dl_stream_coordinator_cancel(&coordinator);}
    void Restart(DlGame& game,int preset) {
        dl_stream_coordinator_cancel(&coordinator);Initialize();
        Check(dl_stream_coordinator_start(&coordinator,&game,preset));
        bool ready=false;
        for(unsigned work=0;!ready&&work<65536;++work)Check(dl_stream_coordinator_initial_step(&coordinator,&game,4096,&ready));
        if(!ready)throw std::runtime_error("Native mission startup did not complete");
        Check(dl_stream_coordinator_initial_publish(&coordinator,&game));
        dl_stream_coordinator_initial_renderer_ready(&coordinator,&game);
        bootstrapping_=false;
    }
    void Service(DlGame& game){
        ++serviceTick_;Check(dl_stream_coordinator_service(&coordinator,&game));
        if(transportEnabled_){submitted_=serviceTick_;Check(dl_stream_coordinator_submitted(&coordinator,submitted_,submitted_));}
    }
    void Settle(DlGame& game) {
        for(unsigned work=0;work<65536;++work){Service(game);if(dl_stream_coordinator_settled(&coordinator))return;}
        throw std::runtime_error("Native mission did not settle at replay boundary");
    }
    void Filter(const DlGame& game,DlInput& input){dl_stream_coordinator_filter_input(&coordinator,&game,&input);}
    bool Visible(const std::string& id) const {
        if(const auto home=staticHomes_.find(id);home!=staticHomes_.end())return (coordinator.view_regions&(UINT32_C(1)<<(home->second-1)))!=0;
        const DlLevel* published=dl_stream_scene_level(&coordinator.scene);
        for(int i=0;i<published->model_count;++i)if(published->models[i].id&&id==published->models[i].id)return true;
        return false;
    }
    Json State(const DlGame& game) const {
        DlStreamCoordinatorSnapshot s{};dl_stream_coordinator_snapshot(&coordinator,&game,&s);
        Json slots=Json::array();
        for(const auto& row:slots_)slots.push_back({{"region",row.region_id},{"generation",row.generation},{"state",row.state},{"bytes_read",row.bytes_read},{"cpu_pins",row.cpu_pins}});
        const auto& w=coordinator.world.stats;
        Json result={{"current_region",s.current_region},{"demand_mask",s.wanted_mask},{"physical_mask",s.physical_mask},{"published_mask",s.published_mask},
            {"pending_mask",s.candidate_mask},{"pending_transition",s.pending_transition},{"revision",s.revision},{"preparing",s.preparing},{"cpu_ready",s.cpu_ready},{"gpu_ready",s.gpu_ready},
            {"candidate_job",s.candidate_job},{"settled",s.settled},{"queued_use",s.queued_use},{"queued_object",s.queued_object},{"queued_transition",s.queued_transition},
            {"active_enemy_mask",game.active_enemy_mask},{"slots",slots},{"slot_count",slots_.size()},{"allocated_slot_bytes",w.allocated_slot_bytes},{"reserved_bytes",w.reserved_bytes},
            {"resident_bytes",w.resident_bytes},{"peak_reserved_bytes",w.peak_reserved_bytes},{"completed_loads",w.completed_loads},{"retired",s.retired},
            {"colliders",game.level->collider_count},{"floors",game.level->floor_count},{"query_cache_ready",dl_game_query_cache_ready(game.level)},
            {"assembly_records",coordinator.config.assembly_records},{"pump_byte_budget",coordinator.config.pump_byte_budget}};
        if(transportEnabled_){result["transport"]=TransportState();result["events"]=transportEvents_;result["completed_rsp"]=s.completed_rsp;result["completed_rdp"]=s.completed_rdp;}
        return result;
    }
private:
    Json& package_;DlLevel& bootstrap_;
    std::ifstream file_;std::vector<uint8_t> directory_;DlMission mission_{};DlStreamDefinition definition_{};
    std::vector<uint32_t> modelLinks_,objectLinks_,enemyLinks_;std::vector<DlStreamMaterial> materials_;
    std::vector<DlStreamPolicyRegion> policy_;std::vector<DlStreamTransition> transitions_;
    std::vector<std::vector<DlVec3>> vertices_;std::vector<std::vector<uint16_t>> indices_;
    std::vector<DlMesh> meshes_;std::vector<DlModelInstance> models_;
    std::vector<std::vector<uint8_t>> buffers_;std::vector<DlRegionSlot> slots_;
    std::unordered_map<std::string,uint32_t> staticHomes_;
    Transport transport_{};bool transportEnabled_=false,bootstrapping_=true;
    uint64_t serviceTick_=0,submitted_=0,rendererJob_=0,rendererReadyTick_=0;
    std::unordered_map<uint64_t,uint64_t> readReadyTicks_;
    Json transportEvents_=Json::array();
    static DlVec3 V(const Json& row){return {row.at(0),row.at(1),row.at(2)};}
    static void Check(DlStreamCoordinatorStatus status){if(status!=DL_COORDINATOR_OK)throw std::runtime_error(std::string("Native stream: ")+dl_stream_coordinator_status_name(status));}
    static size_t Read(void* context,uint32_t offset,void* target,size_t size) {
        auto& self=*static_cast<DlStreamPlay*>(context);
        if(self.transportEnabled_&&!self.bootstrapping_){
            for(const auto& slot:self.slots_)if(slot.region_id&&offset>=slot.region.chunk_offset&&offset<slot.region.chunk_offset+slot.region.chunk_bytes){
                const uint64_t key=(uint64_t(slot.generation)<<32)|slot.region_id;
                const auto [it,inserted]=self.readReadyTicks_.emplace(key,self.serviceTick_+self.transport_.read_delay_ticks);
                if(self.serviceTick_<it->second)return 0;
                break;
            }
            size=std::min(size,size_t(self.transport_.read_slice_bytes));
        }
        self.file_.clear();self.file_.seekg(offset);self.file_.read(static_cast<char*>(target),size);
        return self.file_?size:SIZE_MAX;
    }
    void Initialize() {
        serviceTick_=submitted_=rendererJob_=rendererReadyTick_=0;bootstrapping_=true;readReadyTicks_.clear();transportEvents_=Json::array();
        DlStreamCoordinatorCallbacks callbacks{};callbacks.read=Read;
        callbacks.abandon=[](void* context,const DlRegionSlot* slot){auto& self=*static_cast<DlStreamPlay*>(context);
            self.readReadyTicks_.erase((uint64_t(slot->generation)<<32)|slot->region_id);};
        callbacks.renderer_begin=[](void* context,const DlGame*,uint64_t job){auto& self=*static_cast<DlStreamPlay*>(context);
            self.rendererJob_=job;self.rendererReadyTick_=self.serviceTick_+(self.transportEnabled_?self.transport_.renderer_delay_ticks:0);};
        callbacks.renderer_step=[](void* context,const DlGame*,uint32_t,uint64_t job){auto& self=*static_cast<DlStreamPlay*>(context);
            return self.rendererJob_==job&&self.serviceTick_>=self.rendererReadyTick_;};
        callbacks.renderer_cancel=[](void* context,uint64_t job){auto& self=*static_cast<DlStreamPlay*>(context);if(self.rendererJob_==job)self.rendererJob_=0;};
        callbacks.graphics_completed=[](void* context,uint64_t* rsp,uint64_t* rdp){auto& self=*static_cast<DlStreamPlay*>(context);
            const uint64_t nextRsp=self.transportEnabled_&&self.serviceTick_>self.transport_.rsp_delay_ticks?
                std::min(self.submitted_,self.serviceTick_-self.transport_.rsp_delay_ticks):0;
            const uint64_t nextRdp=self.transportEnabled_&&self.serviceTick_>self.transport_.rdp_delay_ticks?
                std::min(self.submitted_,self.serviceTick_-self.transport_.rdp_delay_ticks):0;
            *rsp=std::max(self.coordinator.world.completed_rsp,nextRsp);*rdp=std::max(self.coordinator.world.completed_rdp,nextRdp);};
        callbacks.observe=[](void* context,const DlStreamCoordinator*,const DlGame*,const DlStreamCoordinatorEvent* event){
            auto& self=*static_cast<DlStreamPlay*>(context);if(!self.transportEnabled_)return;
            if(self.transportEvents_.size()>=256)self.transportEvents_.erase(self.transportEvents_.begin());
            self.transportEvents_.push_back({{"tick",self.serviceTick_},{"kind",event->kind},{"reason",event->reason},{"job",event->job},{"mask",event->mask},{"region",event->region}});
        };
        const auto config=dl_stream_coordinator_default_config();
        Check(dl_stream_coordinator_init(&coordinator,&bootstrap_,&mission_,slots_.data(),slots_.size(),&callbacks,&config,this));
    }
    static std::string Sha256(std::ifstream& file) {
        // Hash in fixed blocks without keeping the full package resident.
        static constexpr uint32_t k[]={0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
            0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,
            0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
            0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,
            0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
            0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
        uint32_t h[]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
        const auto rotate=[](uint32_t v,unsigned n){return (v>>n)|(v<<(32-n));};
        const auto block=[&](const uint8_t* bytes){uint32_t w[64];for(unsigned i=0;i<16;++i)w[i]=(uint32_t(bytes[i*4])<<24)|(uint32_t(bytes[i*4+1])<<16)|(uint32_t(bytes[i*4+2])<<8)|bytes[i*4+3];
            for(unsigned i=16;i<64;++i){auto a=w[i-15],b=w[i-2];w[i]=w[i-16]+(rotate(a,7)^rotate(a,18)^(a>>3))+w[i-7]+(rotate(b,17)^rotate(b,19)^(b>>10));}
            uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
            for(unsigned i=0;i<64;++i){uint32_t t=z+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+k[i]+w[i],u=(rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c));z=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;}
            h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;};
        std::array<uint8_t,64> bytes{};uint64_t length=0;size_t tail=0;
        while(file.read(reinterpret_cast<char*>(bytes.data()),64)){length+=64;block(bytes.data());}
        tail=size_t(file.gcount());length+=tail;bytes[tail++]=0x80;
        if(tail>56){std::fill(bytes.begin()+tail,bytes.end(),0);block(bytes.data());tail=0;}
        std::fill(bytes.begin()+tail,bytes.end(),0);for(unsigned i=0;i<8;++i)bytes[63-i]=uint8_t((length*8)>>(i*8));block(bytes.data());
        std::ostringstream out;out<<std::hex<<std::setfill('0');for(auto value:h)out<<std::setw(8)<<value;return out.str();
    }
};
