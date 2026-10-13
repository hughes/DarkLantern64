#include "runtime_diagnostic_fixture.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
static DlRuntimeDoorwayFixture fixture;
static DlRuntimeReadiness readiness(void){
    return (DlRuntimeReadiness){.physical_mask=dl_runtime_doorway_physical_mask(&fixture),
        .published_mask=dl_runtime_doorway_published_mask(&fixture)};
}
static int doorway_queries(void){
    CHECK(dl_runtime_doorway_init(&fixture));
    CHECK(dl_runtime_doorway_load(&fixture,1)==DL_STREAM_OK);
    CHECK(dl_runtime_doorway_publish(&fixture)==DL_STREAM_SCENE_OK);
    CHECK(dl_runtime_doorway_physical_mask(&fixture)==1&&dl_runtime_doorway_published_mask(&fixture)==1);
    DlRuntimeInteractionDiagnostic door,loot;
    DlGame before=fixture.game;DlStreamStats stream_before=fixture.world.stats;uint32_t revision=fixture.scene.revision;
    CHECK(dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,0,&door));
    CHECK(door.valid&&door.eligible&&door.in_range&&!door.door_open&&door.ignored_object==0);
    CHECK(!door.ordinary_los.clear&&door.ordinary_los.door&&door.ordinary_source.kind==DL_RUNTIME_SOURCE_COLLIDER);
    CHECK(door.ordinary_source.entity==1&&door.ordinary_source.region==1&&door.ordinary_source.record==2);
    CHECK(door.ordinary_source.object_index==0&&strcmp(door.ordinary_source.object_id,"fixture-door")==0);
    CHECK(door.interaction_los.clear&&door.interaction_los.ignored_colliders==2);
    CHECK(!memcmp(&before,&fixture.game,sizeof before)&&!memcmp(&stream_before,&fixture.world.stats,sizeof stream_before)&&fixture.scene.revision==revision);
    /* The interaction ignores its own closed leaf and linked loading barrier. */
    dl_game_update(&fixture.game,&(DlInput){.use=true},1.f/60);
    CHECK(dl_game_object_open(&fixture.game,0));
    CHECK(dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,0,&door));
    CHECK(door.eligible&&door.door_open&&!door.ordinary_los.clear&&door.interaction_los.clear);
    CHECK(door.ordinary_source.kind==DL_RUNTIME_SOURCE_GATE_BARRIER&&door.ordinary_source.gate==1);
    CHECK(door.ordinary_source.entity==1&&door.ordinary_source.region==2&&door.ordinary_source.object_index==0);
    CHECK(dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,1,&loot));
    CHECK(loot.in_range&&!loot.eligible&&loot.reason==DL_RUNTIME_INTERACTION_OCCLUDED&&loot.ignored_object==-1);
    CHECK(loot.interaction_source.kind==DL_RUNTIME_SOURCE_GATE_BARRIER&&loot.interaction_source.object_index==0);
    CHECK(loot.ordinary_los.clear==dl_line_of_sight(&fixture.game,loot.eye,loot.target));
    /* Cache availability is visible while the underlying LOS result stays the same. */
    DlLevel scalar=*fixture.game.level;scalar.query_cache=NULL;DlGame scalar_game=fixture.game;scalar_game.level=&scalar;
    DlGameQueryDiagnostic scalar_query;
    CHECK(dl_game_diagnose_line_of_sight(&scalar_game,loot.eye,loot.target,-1,&scalar_query));
    CHECK(!scalar_query.query_cache_ready&&scalar_query.clear==loot.ordinary_los.clear);
    CHECK(scalar_query.clear==dl_line_of_sight(&scalar_game,loot.eye,loot.target));
    CHECK(loot.ordinary_los.query_cache_ready);
    return 0;
}
static int requested_movement(void){
    CHECK(doorway_queries()==0);
    DlInput filtered={0},toward={.forward=1,.jump=true};DlRuntimeMotionDiagnostic motion;
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&toward,&filtered,.125f,readiness(),&motion));
    CHECK(motion.valid&&motion.gate_waiting&&motion.gate==1&&motion.from_region==1&&motion.to_region==2&&motion.door_entity==1);
    CHECK(motion.direction==DL_RUNTIME_MOTION_TOWARD&&motion.horizontal_suppressed&&motion.jump_suppressed);
    CHECK(motion.required_mask==2&&motion.missing_physical==2&&motion.missing_published==2&&motion.readiness==DL_RUNTIME_WAIT_READ);
    CHECK(fabsf(motion.requested_delta.x-.2625f)<1e-5f&&fabsf(motion.requested_delta.z)<1e-5f);
    CHECK(!motion.position_query.clear&&motion.position_source.kind==DL_RUNTIME_SOURCE_GATE_BARRIER);
    CHECK(motion.position_source.gate==1&&motion.position_source.object_index==0);
    CHECK(motion.position_query.clear==dl_position_clear(&fixture.game,motion.requested_position,.18f,1.65f));
    DlRuntimeMotionDiagnostic intent;DlGame before=fixture.game;
    CHECK(dl_runtime_diagnose_motion_intent(&fixture.scene,&fixture.game,&toward,&filtered,.125f,readiness(),&intent));
    CHECK(intent.valid&&!intent.position_query.valid&&intent.position_query.collider_index==-1&&intent.position_source.kind==DL_RUNTIME_SOURCE_NONE);
    CHECK(intent.gate==motion.gate&&intent.required_mask==motion.required_mask&&intent.readiness==motion.readiness&&intent.direction==motion.direction);
    CHECK(intent.missing_physical==motion.missing_physical&&intent.missing_published==motion.missing_published&&intent.gate_waiting==motion.gate_waiting);
    CHECK(!memcmp(&intent.requested_delta,&motion.requested_delta,sizeof intent.requested_delta));
    CHECK(!memcmp(&before,&fixture.game,sizeof before));
    fixture.game.crouched=true;
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&toward,&filtered,.125f,readiness(),&motion));
    CHECK(dl_runtime_diagnose_motion_intent(&fixture.scene,&fixture.game,&toward,&filtered,.125f,readiness(),&intent));
    CHECK(!memcmp(&intent.requested_delta,&motion.requested_delta,sizeof intent.requested_delta)&&fabsf(intent.requested_delta.x-.2625f)<1e-5f);
    fixture.game.crouched=false;
    DlInput parallel={.strafe=1},away={.forward=-1};
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&parallel,&filtered,.125f,readiness(),&motion));
    CHECK(motion.direction==DL_RUNTIME_MOTION_PARALLEL&&motion.gate_waiting&&motion.horizontal_suppressed&&motion.position_query.clear);
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&away,&filtered,.125f,readiness(),&motion));
    CHECK(motion.direction==DL_RUNTIME_MOTION_AWAY&&motion.gate_waiting&&motion.horizontal_suppressed&&motion.position_query.clear);
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&filtered,&filtered,.125f,readiness(),&motion));
    CHECK(motion.direction==DL_RUNTIME_MOTION_NONE&&!motion.horizontal_suppressed);
    /* Physical residency does not make a loading barrier disappear before publication. */
    CHECK(dl_runtime_doorway_load(&fixture,2)==DL_STREAM_OK);
    DlRuntimeReadiness state=readiness();CHECK(state.physical_mask==3&&state.published_mask==1);
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&toward,&filtered,.125f,state,&motion));
    CHECK(motion.readiness==DL_RUNTIME_WAIT_CPU&&motion.missing_physical==0&&motion.missing_published==2&&!motion.position_query.clear);
    state.pending_mask=3;state.cpu_ready=true;
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&toward,&filtered,.125f,state,&motion));
    CHECK(motion.readiness==DL_RUNTIME_WAIT_GPU&&motion.gate_waiting);
    state.gpu_ready=true;
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&toward,&filtered,.125f,state,&motion));
    CHECK(motion.readiness==DL_RUNTIME_WAIT_PUBLICATION&&motion.gate_waiting);
    CHECK(dl_runtime_doorway_publish(&fixture)==DL_STREAM_SCENE_OK);
    CHECK(dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&toward,&toward,.125f,readiness(),&motion));
    CHECK(motion.readiness==DL_RUNTIME_READY&&!motion.gate_waiting&&!motion.horizontal_suppressed&&motion.position_query.clear);
    DlRuntimeInteractionDiagnostic loot;
    CHECK(dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,1,&loot));
    CHECK(loot.eligible&&loot.ordinary_los.clear&&loot.interaction_los.clear);
    return 0;
}
static int partial_reads_and_reverse_view(void){
    CHECK(dl_runtime_doorway_init(&fixture));fixture.read_limit=7;
    CHECK(dl_runtime_doorway_request(&fixture,1)==DL_STREAM_OK);
    CHECK(dl_world_stream_pump(&fixture.world,7)==7);
    CHECK(fixture.slots[fixture.handles[0].slot].bytes_read==7&&dl_runtime_doorway_physical_mask(&fixture)==0);
    fixture.read_stalled=true;CHECK(dl_world_stream_pump(&fixture.world,7)==0&&fixture.world.stats.read_stalls==1);
    fixture.read_stalled=false;CHECK(dl_runtime_doorway_load(&fixture,1)==DL_STREAM_OK);
    CHECK(dl_runtime_doorway_load(&fixture,2)==DL_STREAM_OK);
    CHECK(dl_runtime_doorway_publish(&fixture)==DL_STREAM_SCENE_OK);
    fixture.game.player=(DlVec3){.3f,0,0};fixture.game.door_open=true;fixture.game.open_objects=1;
    CHECK(dl_world_stream_retire(&fixture.world,fixture.handles[0])==DL_STREAM_OK);
    CHECK(dl_runtime_doorway_publish(&fixture)==DL_STREAM_SCENE_OK&&dl_runtime_doorway_published_mask(&fixture)==2);
    DlRuntimeInteractionDiagnostic door;
    CHECK(dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,0,&door));
    CHECK(!door.ordinary_los.clear&&door.ordinary_source.kind==DL_RUNTIME_SOURCE_GATE_BARRIER&&door.ordinary_source.gate==2);
    CHECK(door.interaction_los.clear&&door.interaction_los.ignored_colliders==1&&door.eligible);
    fixture.game.player=(DlVec3){0,0,0};
    CHECK(dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,0,&door));
    /* The leaf itself is not present in a region-2-only view, so no actor can
     * occupy that absent authored collider; the synthetic barrier still blocks. */
    CHECK(!door.door_occupied&&door.eligible);
    return 0;
}
static int invalid_and_other_sources(void){
    DlGameQueryDiagnostic query;DlRuntimeMotionDiagnostic motion;DlRuntimeInteractionDiagnostic interaction;
    CHECK(!dl_game_diagnose_position(NULL,(DlVec3){0},.18f,1.65f,&query)&&!query.valid);
    CHECK(!dl_game_diagnose_position(&fixture.game,(DlVec3){NAN,0,0},.18f,1.65f,&query));
    CHECK(!dl_game_diagnose_position(&fixture.game,(DlVec3){0},0,1.65f,&query));
    CHECK(!dl_game_diagnose_line_of_sight(&fixture.game,(DlVec3){0},(DlVec3){0},DL_MAX_OBJECTS,&query));
    CHECK(!dl_runtime_diagnose_motion(&fixture.scene,&fixture.game,&(DlInput){0},&(DlInput){0},NAN,readiness(),&motion));
    CHECK(!dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,-1,&interaction));
    CHECK(!dl_runtime_diagnose_interaction(&fixture.scene,&fixture.game,DL_MAX_OBJECTS,&interaction));
    CHECK(dl_game_diagnose_position(&fixture.game,(DlVec3){2.5f,0,0},.18f,1.65f,&query)&&!query.clear);
    DlRuntimeQuerySource source;dl_runtime_query_source(&fixture.scene,&fixture.game,&query,&source);
    CHECK(source.kind==DL_RUNTIME_SOURCE_COLLIDER&&source.entity==0&&source.region==2&&source.record==2&&source.object_index==-1);
    DlFloorTriangle floor={.vertices={{-1,1,-1},{1,1,-1},{0,1,1}}};
    DlLevel level={.floors=&floor,.floor_count=1};DlGame game={.level=&level};
    CHECK(dl_game_diagnose_line_of_sight(&game,(DlVec3){0,0,0},(DlVec3){0,2,0},-1,&query)&&!query.clear&&query.floor_index==0);
    CHECK(query.clear==dl_line_of_sight(&game,(DlVec3){0,0,0},(DlVec3){0,2,0}));
    dl_runtime_query_source(NULL,&game,&query,&source);CHECK(source.kind==DL_RUNTIME_SOURCE_FLOOR);
    CHECK(strcmp(dl_runtime_source_name(DL_RUNTIME_SOURCE_GATE_BARRIER),"gate_barrier")==0);
    CHECK(strcmp(dl_runtime_motion_direction_name(DL_RUNTIME_MOTION_AWAY),"away")==0);
    CHECK(strcmp(dl_runtime_readiness_name(DL_RUNTIME_WAIT_PUBLICATION),"publication")==0);
    CHECK(strcmp(dl_runtime_interaction_reason_name(DL_RUNTIME_INTERACTION_OCCLUDED),"occluded")==0);
    return 0;
}
int main(void){
    CHECK(requested_movement()==0);CHECK(partial_reads_and_reverse_view()==0);CHECK(invalid_and_other_sources()==0);
    puts("PASS reusable synthetic doorway: source-aware LOS, own-door barrier exemption, requested parallel/away movement, staged readiness and read-only probes");
    return 0;
}
