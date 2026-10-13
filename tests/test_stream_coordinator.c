#include "stream_coordinator.h"
#include <stdio.h>
#include <string.h>

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)
enum { DIRECTORY = 364, FIRST = 368, CHUNK_BYTES = 200, FILE_BYTES = 984 };
static uint8_t package[FILE_BYTES], buffers[3][CHUNK_BYTES];
static DlMission mission;
static DlRegionSlot slots[3];
static DlStreamCoordinator coordinator;
static DlGame game;
typedef struct {
    size_t short_read, max_read, calls;
    unsigned stalls, graphics_delay, begins, commits, cancels, abandons;
    uint32_t error_region, corrupt_region, clock;
    uint64_t rsp, rdp, current_job, finished_job;
    const DlLevel *candidate, *committed;
} FakePlatform;
static FakePlatform platform;
static const DlVec3 vertices[] = {{0,0,0},{1,0,0},{0,1,0}};
static const uint16_t indices[] = {0,1,2};
static const DlMesh meshes[] = {{.vertices=vertices,.vertex_count=3,.indices=indices,.index_count=3}};
static const DlModelInstance models[] = {
    {.id="guard",.mesh=0,.position={2,0,0},.scale={1,1,1},.role=DL_MODEL_GUARD,.texture=-1,.enemy_index=0},
    {.id="door",.mesh=0,.position={-.05f,1,0},.scale={1,1,1},.role=DL_MODEL_DOOR,.texture=-1,.enemy_index=-1},
    {.id="loot",.mesh=0,.position={3,.5f,1},.scale={1,1,1},.role=DL_MODEL_LOOT,.texture=-1,.enemy_index=-1}
};
static const DlObjectDef objects[] = {
    {.id="door",.kind=DL_OBJECT_DOOR,.position={-.05f,1,0},.radius=2},
    {.id="loot",.kind=DL_OBJECT_LOOT,.position={3,.5f,1},.radius=1,.value=25}
};
static const DlEnemyDef enemies[] = {{.id="guard",.type=DL_ENEMY_WATCHMAN,.behavior=DL_BEHAVIOR_SENTRY,
    .spawn={2,0,0},.speed=1,.sight_range=10,.hearing_range=8}};
static const uint32_t model_links[]={3,2,4}, object_links[]={2,4}, enemy_links[]={3};
static const DlStreamMaterial materials[]={{.id=1,.color={128,192,255,255},.double_sided=true}};
static const DlStreamPolicyRegion regions[]={
    {.activation_minimum={-14,-1,-2},.activation_maximum={0,3,2},.working_mask=1},
    {.activation_minimum={0,-1,-2},.activation_maximum={14,3,2},.working_mask=2},
    {.activation_minimum={-14,-1,2},.activation_maximum={0,3,14},.working_mask=4}
};
static DlStreamTransition gates[]={
    {.from_region=1,.to_region=2,.minimum={-.02f,-1,-.5f},.maximum={.02f,3,.5f},.door_entity_id=2},
    {.from_region=2,.to_region=1,.minimum={-.02f,-1,-.5f},.maximum={.02f,3,.5f},.door_entity_id=2},
    {.from_region=1,.to_region=3,.minimum={-2.5f,-1,1.98f},.maximum={-1.5f,3,2.02f}},
    {.from_region=3,.to_region=1,.minimum={-2.5f,-1,1.98f},.maximum={-1.5f,3,2.02f}}
};
static const DlStreamDefinition definition={.package_path="fixture.dlm",.directory_bytes=DIRECTORY,
    .file_bytes=FILE_BYTES,.max_region_bytes=CHUNK_BYTES,.region_count=3,.model_entity_ids=model_links,
    .object_entity_ids=object_links,.enemy_entity_ids=enemy_links,.materials=materials,.material_count=1,
    .policy=DL_STREAM_POLICY_AUTHORED,.policy_regions=regions,.transitions=gates,.transition_count=4,.max_resident_regions=2};
static const DlLevel bootstrap={.version=2,.title="coordinator fixture",.meshes=meshes,.mesh_count=1,
    .models=models,.model_count=3,.objects=objects,.object_count=2,.enemies=enemies,.enemy_count=1,
    .spawn={-10,0,0},.spawn_yaw=1.57079632679f,.stream=&definition};
static void put16(uint8_t *p,uint16_t n){p[0]=(uint8_t)(n>>8);p[1]=(uint8_t)n;}
static void put32(uint8_t *p,uint32_t n){p[0]=(uint8_t)(n>>24);p[1]=(uint8_t)(n>>16);p[2]=(uint8_t)(n>>8);p[3]=(uint8_t)n;}
static void putfloat(uint8_t *p,float n){uint32_t bits;memcpy(&bits,&n,4);put32(p,bits);}
/* Deliberately independent of the production incremental checksum. */
static uint32_t crc32(const uint8_t *p,size_t bytes){
    uint32_t crc=UINT32_MAX;
    while(bytes--){crc^=*p++;for(unsigned bit=0;bit<8;++bit)crc=(crc>>1)^((crc&1)?0xedb88320u:0);}
    return crc^UINT32_MAX;
}
static void section(uint8_t *p,unsigned kind,unsigned offset,unsigned count,unsigned stride){
    put16(p,(uint16_t)kind);put32(p+4,offset);put32(p+8,count*stride);put32(p+12,count);put32(p+16,stride);
}
static void make_package(void){
    memset(package,0,sizeof package);
    for(unsigned region=1;region<=3;++region){
        uint8_t *p=package+FIRST+(region-1)*208;
        memcpy(p,"DLR1",4);put16(p+4,1);put16(p+6,32);put32(p+8,CHUNK_BYTES);put32(p+12,3);
        if(region==2)putfloat(p+16,2);
        if(region==3)putfloat(p+24,4);
        section(p+32,1,112,1,44);section(p+56,2,160,1,32);section(p+80,3,192,2,4);
        putfloat(p+112,region==2?-1:-3);putfloat(p+124,region==2?1:-1);putfloat(p+140,1);
        put32(p+148,1);put32(p+152,region);
        if(region==1){putfloat(p+160,-.05f);putfloat(p+164,1);putfloat(p+172,.05f);putfloat(p+176,1);putfloat(p+180,1);put32(p+188,2);}
        else{putfloat(p+164,-.25f);putfloat(p+172,2);putfloat(p+176,.25f);putfloat(p+180,2);}
        put32(p+192,(region-1)*2+1);put32(p+196,region*2);
    }
    memcpy(package,"DLM1",4);put16(package+4,1);put16(package+6,64);put32(package+8,FILE_BYTES);
    put32(package+12,3);put32(package+16,6);put32(package+20,DIRECTORY);put32(package+24,1);
    for(unsigned i=0;i<3;++i){
        uint8_t *p=package+64+i*64;unsigned offset=FIRST+i*208;
        put32(p,i+1);put32(p+4,offset);put32(p+8,CHUNK_BYTES);put32(p+12,CHUNK_BYTES);
        put32(p+16,crc32(package+offset,CHUNK_BYTES));put32(p+20,i);put32(p+24,1);
        putfloat(p+32,i==1?0:-14);putfloat(p+36,-1);putfloat(p+40,i==2?2:-2);
        putfloat(p+44,i==1?14:0);putfloat(p+48,3);putfloat(p+52,i==2?14:2);
    }
    const uint16_t kinds[]={0,2,1,3,0,0};
    for(unsigned i=0;i<6;++i){uint8_t *p=package+256+i*16;put32(p,i+1);put32(p+4,i/2+1);put16(p+8,kinds[i]);}
    put32(package+352,2);put32(package+356,1);put32(package+360,1);
    put32(package+28,crc32(package,DIRECTORY));
}
static size_t read_memory(void *context,uint32_t offset,void *out,size_t wanted){
    FakePlatform *p=context;++p->calls;if(wanted>p->max_read)p->max_read=wanted;
    unsigned region=offset>=FIRST?(offset-FIRST)/208+1:0;
    if(region==p->error_region&&region)return SIZE_MAX;
    if(p->stalls){--p->stalls;return 0;}
    if(offset>FILE_BYTES||wanted>FILE_BYTES-offset)return SIZE_MAX;
    size_t n=wanted;if(p->short_read&&n>p->short_read)n=p->short_read;
    memcpy(out,package+offset,n);
    if(region==p->corrupt_region&&region&&offset==FIRST+(region-1)*208)((uint8_t *)out)[0]^=1;
    return n;
}
static void abandon(void *context,const DlRegionSlot *slot){(void)slot;++((FakePlatform *)context)->abandons;}
static uint32_t clock_now(void *context){return ++((FakePlatform *)context)->clock;}
static bool io_busy(void *context){(void)context;return false;}
static void renderer_begin(void *context,const DlGame *candidate,uint64_t job){FakePlatform *p=context;++p->begins;p->candidate=candidate->level;p->current_job=job;}
static bool renderer_step(void *context,const DlGame *candidate,uint32_t budget,uint64_t job){
    FakePlatform *p=context;(void)budget;if(candidate->level!=p->candidate||job!=p->current_job)return false;
    if(p->finished_job&&p->finished_job!=job)return false;
    if(p->graphics_delay){--p->graphics_delay;return false;}return true;
}
static void renderer_cancel(void *context,uint64_t job){FakePlatform *p=context;++p->cancels;if(job==p->current_job){p->candidate=NULL;p->current_job=0;}}
static void renderer_commit(void *context,const DlGame *published,uint64_t job){FakePlatform *p=context;if(job!=p->current_job)return;++p->commits;p->committed=published->level;}
static void graphics_completed(void *context,uint64_t *rsp,uint64_t *rdp){FakePlatform *p=context;*rsp=p->rsp;*rdp=p->rdp;}
static const DlStreamCoordinatorCallbacks callbacks={.read=read_memory,.abandon=abandon,.clock=clock_now,
    .io_busy=io_busy,.renderer_begin=renderer_begin,.renderer_step=renderer_step,.renderer_cancel=renderer_cancel,
    .renderer_commit=renderer_commit,.graphics_completed=graphics_completed};
static int initialize(void){
    make_package();memset(&platform,0,sizeof platform);platform.short_read=7;
    CHECK(dl_mission_open(package,DIRECTORY,FILE_BYTES,&mission)==DL_MISSION_OK);
    for(unsigned i=0;i<3;++i)slots[i]=(DlRegionSlot){.buffer=buffers[i],.capacity=CHUNK_BYTES};
    DlStreamCoordinatorConfig config=dl_stream_coordinator_default_config();
    config.read_slice=13;config.pump_byte_budget=23;config.assembly_records=1;
    config.prepare_ticks=100000;config.service_ticks=100000;config.read_ticks=100000;config.max_pump_attempts=1;
    CHECK(dl_stream_coordinator_init(&coordinator,&bootstrap,&mission,slots,3,&callbacks,&config,&platform)==DL_COORDINATOR_OK);
    CHECK(dl_stream_coordinator_start(&coordinator,&game,-1)==DL_COORDINATOR_OK);
    bool ready=false;for(unsigned i=0;i<1000&&!ready;++i)CHECK(dl_stream_coordinator_initial_step(&coordinator,&game,23,&ready)==DL_COORDINATOR_OK);
    CHECK(ready);CHECK(dl_stream_coordinator_initial_publish(&coordinator,&game)==DL_COORDINATOR_OK);
    dl_stream_coordinator_initial_renderer_ready(&coordinator,&game);
    CHECK(coordinator.view_regions==1&&game.level==dl_stream_scene_level(&coordinator.scene));
    CHECK(platform.max_read<=13);return 0;
}
static int service(unsigned steps){for(unsigned i=0;i<steps;++i)CHECK(dl_stream_coordinator_service(&coordinator,&game)==DL_COORDINATOR_OK);return 0;}
static int settle(void){
    for(unsigned i=0;i<2000;++i){CHECK(service(1)==0);if(dl_stream_coordinator_settled(&coordinator))return 0;}
    CHECK(false);return 1;
}
static const DlModelInstance *model(const char *id){for(int i=0;i<game.level->model_count;++i)if(game.level->models[i].id&&!strcmp(game.level->models[i].id,id))return &game.level->models[i];return NULL;}
static unsigned pins(void){unsigned count=0;for(unsigned i=0;i<3;++i)count+=slots[i].cpu_pins;return count;}

/* Implementations below exercise the shared coordinator, not a second policy. */
static int delayed_read_and_renderer(void){
    CHECK(initialize()==0);const DlLevel *old=game.level;uint32_t revision=coordinator.scene.revision;
    platform.stalls=4;platform.graphics_delay=8;game.player=(DlVec3){-.6f,0,0};
    CHECK(service(4)==0);CHECK(game.level==old&&coordinator.scene.revision==revision);
    CHECK(coordinator.world.stats.read_stalls>0&&coordinator.view_regions==1);
    CHECK(!dl_position_clear(&game,(DlVec3){.035f,0,0},.18f,1.65f));
    for(unsigned i=0;i<1000&&!coordinator.gpu_started;++i)CHECK(service(1)==0);
    CHECK(coordinator.gpu_started&&coordinator.prepared_regions==3&&game.level==old);
    CHECK(service(3)==0);CHECK(game.level==old&&coordinator.scene.revision==revision);
    CHECK(settle()==0);CHECK(coordinator.view_regions==3&&game.level!=old&&platform.commits==1);
    CHECK(coordinator.scene.revision==revision+1&&pins()==0&&platform.max_read<=13);
    DlStreamCoordinatorSnapshot snapshot;dl_stream_coordinator_snapshot(&coordinator,&game,&snapshot);
    CHECK(snapshot.settled&&snapshot.published_mask==3&&snapshot.physical_mask==3&&!snapshot.preparing);
    return 0;
}
static int stale_preparation_and_guard_membership(void){
    CHECK(initialize()==0);const DlLevel *old=game.level;game.player=(DlVec3){-.5f,0,0};
    for(unsigned i=0;i<1000&&!coordinator.assembling;++i)CHECK(service(1)==0);
    CHECK(coordinator.assembling&&pins()>0);uint64_t old_job=coordinator.candidate_job;
    dl_stream_coordinator_cancel(&coordinator);CHECK(!coordinator.preparing&&pins()==0&&game.level==old);
    CHECK(coordinator.view_regions==1);platform.graphics_delay=30;
    for(unsigned i=0;i<1000&&!coordinator.gpu_started;++i)CHECK(service(1)==0);
    CHECK(coordinator.gpu_started&&coordinator.candidate_job>old_job&&game.level==old);
    old_job=coordinator.candidate_job;unsigned cancels=platform.cancels;
    game.enemies[0].position=(DlVec3){100,100,100};
    platform.graphics_delay=0;
    for(unsigned i=0;i<10&&platform.cancels==cancels;++i)CHECK(service(1)==0);
    CHECK(game.level==old&&platform.cancels>cancels);
    for(unsigned i=0;i<1000&&!coordinator.gpu_started;++i)CHECK(service(1)==0);
    CHECK(coordinator.candidate_job>old_job);platform.finished_job=old_job;
    CHECK(service(3)==0);CHECK(game.level==old&&coordinator.view_regions==1&&platform.commits==0);
    platform.finished_job=0;CHECK(settle()==0);
    CHECK(coordinator.candidate_job>old_job&&coordinator.view_regions==3&&!model("guard")&&pins()==0);
    game.enemies[0].position=(DlVec3){-2,0,0};CHECK(service(1)==0);
    CHECK(coordinator.preparing&&!model("guard")&&game.active_enemy_mask==0);CHECK(settle()==0);
    CHECK(model("guard")&&game.active_enemy_mask==1&&pins()==0);
    return 0;
}
static int delayed_graphics_retirement(void){
    CHECK(initialize()==0);game.player=(DlVec3){-.5f,0,0};CHECK(settle()==0);
    DlRegionHandle old={0,0};for(unsigned i=0;i<3;++i)if(slots[i].region_id==1)old=(DlRegionHandle){i,slots[i].generation};
    CHECK(old.generation);CHECK(dl_stream_coordinator_submitted(&coordinator,5,8)==DL_COORDINATOR_OK);
    game.player=(DlVec3){10,0,0};CHECK(settle()==0);CHECK(coordinator.view_regions==2);
    CHECK(slots[old.slot].state==DL_STREAM_RETIRING&&slots[old.slot].generation==old.generation);
    CHECK(dl_world_stream_cancel(&coordinator.world,old)==DL_STREAM_OK);
    CHECK(slots[old.slot].state==DL_STREAM_RETIRING);
    platform.rsp=5;platform.rdp=7;CHECK(dl_stream_coordinator_complete(&coordinator,5,7)==DL_COORDINATOR_OK);
    CHECK(slots[old.slot].state==DL_STREAM_RETIRING);
    platform.rdp=8;CHECK(dl_stream_coordinator_complete(&coordinator,5,8)==DL_COORDINATOR_OK);
    CHECK(slots[old.slot].state==DL_STREAM_FREE);
    DlRegionHandle next;CHECK(dl_world_stream_request(&coordinator.world,1,&next)==DL_STREAM_OK);
    CHECK(next.slot==old.slot&&next.generation>old.generation);
    CHECK(dl_world_stream_cancel(&coordinator.world,old)==DL_STREAM_HANDLE);
    CHECK(dl_world_stream_cancel(&coordinator.world,next)==DL_STREAM_OK);
    return 0;
}
static int branch_reversal_and_swept_gate(void){
    CHECK(initialize()==0);game.player=(DlVec3){-.5f,0,0};platform.stalls=30;CHECK(service(2)==0);
    CHECK(coordinator.wanted_regions==3);game.player=(DlVec3){-2,0,1.5f};CHECK(service(2)==0);
    CHECK(coordinator.wanted_regions==5&&coordinator.policy_state.current_region==1);
    platform.stalls=0;CHECK(settle()==0);CHECK(coordinator.view_regions==5);
    game.player=(DlVec3){-2,0,1.965f};CHECK(service(1)==0);
    game.player=(DlVec3){-2,0,2.035f};CHECK(service(1)==0);
    CHECK(coordinator.policy_state.current_region==3); /* Both endpoints miss the thin gate. */
    game.player=(DlVec3){-2,0,1.965f};CHECK(service(1)==0);CHECK(coordinator.policy_state.current_region==1);
    game.player=(DlVec3){-10,0,0};CHECK(settle()==0);CHECK(coordinator.view_regions==1);
    CHECK(coordinator.world.stats.completed_loads>=2&&coordinator.world.stats.reserved_bytes==CHUNK_BYTES);
    CHECK(pins()==0);return 0;
}
static int queued_door_and_persistence(void){
    CHECK(initialize()==0);game.player=(DlVec3){-1,0,0};game.yaw=1.57079632679f;
    DlInput input={.use=true};dl_stream_coordinator_filter_input(&coordinator,&game,&input);
    CHECK(!input.use&&coordinator.queued_use&&coordinator.queued_object==0&&coordinator.queued_transition==1);
    CHECK(!game.door_open&&game.open_objects==0);CHECK(settle()==0);
    input=(DlInput){0};dl_stream_coordinator_filter_input(&coordinator,&game,&input);
    CHECK(input.use&&!coordinator.queued_use);dl_game_update(&game,&input,1.0f/60.0f);
    CHECK(game.door_open&&game.open_objects==1);
    input=(DlInput){0};dl_stream_coordinator_filter_input(&coordinator,&game,&input);CHECK(!input.use);
    game.collected_objects=2;game.completed_objectives=2;game.loot_value=25;
    game.enemies[0].awareness=.75f;game.enemies[0].state=DL_CHASE;game.enemies[0].patrol_index=6;
    game.player=(DlVec3){10,0,0};CHECK(settle()==0);CHECK(coordinator.view_regions==2);
    game.player=(DlVec3){.5f,0,0};CHECK(settle()==0);
    game.player=(DlVec3){-10,0,0};CHECK(settle()==0);CHECK(coordinator.view_regions==1);
    CHECK(game.door_open&&game.open_objects==1&&game.collected_objects==2&&game.completed_objectives==2&&game.loot_value==25);
    CHECK(game.enemies[0].awareness==.75f&&game.enemies[0].state==DL_CHASE&&game.enemies[0].patrol_index==6);
    CHECK(model("door")&&!model("loot"));return 0;
}
static int errors_and_reset(void){
    for(unsigned corrupt=0;corrupt<2;++corrupt){
        CHECK(initialize()==0);const DlLevel *old=game.level;uint32_t revision=coordinator.scene.revision;
        if(corrupt)platform.corrupt_region=2;else platform.error_region=2;
        game.player=(DlVec3){-.5f,0,0};DlStreamCoordinatorStatus status=DL_COORDINATOR_OK;
        for(unsigned i=0;i<1000&&status==DL_COORDINATOR_OK;++i)status=dl_stream_coordinator_service(&coordinator,&game);
        CHECK(status==DL_COORDINATOR_WORLD&&coordinator.world_failure==(corrupt?DL_STREAM_CORRUPT:DL_STREAM_IO));
        CHECK(game.level==old&&coordinator.scene.revision==revision&&coordinator.view_regions==1&&pins()==0);
        dl_stream_coordinator_cancel(&coordinator);CHECK(game.level==old&&pins()==0);
    }
    CHECK(initialize()==0);game.player=(DlVec3){-.5f,0,0};platform.graphics_delay=100;
    for(unsigned i=0;i<1000&&!coordinator.gpu_started;++i)CHECK(service(1)==0);
    CHECK(coordinator.gpu_started);game.open_objects=1;game.door_open=true;game.collected_objects=2;game.loot_value=25;
    dl_stream_coordinator_cancel(&coordinator);
    CHECK(pins()==0&&!coordinator.preparing);
    /* Restart uses the same stop/initialize/start lifecycle as the adapters.
     * No submission fence exists in this case, so the fake renderer is shut
     * down immediately before the caller-owned storage is reinitialized. */
    platform.candidate=NULL;platform.committed=NULL;CHECK(initialize()==0);
    CHECK(!game.door_open&&!game.open_objects&&!game.collected_objects&&!game.loot_value);
    CHECK(coordinator.policy_state.current_region==1&&!coordinator.queued_use);
    CHECK(game.enemies[0].state==DL_PATROL&&game.enemies[0].patrol_index==0);
    return 0;
}
int main(void){
    CHECK(delayed_read_and_renderer()==0);CHECK(stale_preparation_and_guard_membership()==0);
    CHECK(delayed_graphics_retirement()==0);CHECK(branch_reversal_and_swept_gate()==0);
    CHECK(queued_door_and_persistence()==0);CHECK(errors_and_reset()==0);
    puts("stream coordinator: delayed/partial/error reads, graphics fences, reversal/branch/swept gates, queued door, guard preparation, persistence, cancellation/generations and reset passed");
    return 0;
}
