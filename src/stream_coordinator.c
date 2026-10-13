#include "stream_coordinator.h"
#include <math.h>
#include <string.h>

static void observe(DlStreamCoordinator *s,const DlGame *game,const char *kind,
    const char *reason,uint64_t job,uint32_t mask,uint32_t region){
    if(s->callbacks.observe){
        DlStreamCoordinatorEvent event={kind,reason,job,mask,region};
        s->callbacks.observe(s->context,s,game,&event);
    }
}
static DlStreamCoordinatorStatus world_result(DlStreamCoordinator *s,DlStreamStatus status){
    if(status!=DL_STREAM_OK){s->world_failure=status;s->failure=DL_COORDINATOR_WORLD;}
    return s->failure;
}
static DlStreamCoordinatorStatus scene_result(DlStreamCoordinator *s,DlStreamSceneStatus status){
    if(status!=DL_STREAM_SCENE_OK){s->scene_failure=status;s->failure=DL_COORDINATOR_SCENE;}
    return s->failure;
}
static uint32_t clock_now(const DlStreamCoordinator *s){return s->callbacks.clock?s->callbacks.clock(s->context):0;}
static bool within_budget(const DlStreamCoordinator *s,uint32_t start,uint32_t budget){
    return !s->callbacks.clock||!budget||(uint32_t)(clock_now(s)-start)<budget;
}
uint32_t dl_stream_coordinator_resident_mask(const DlStreamCoordinator *s){
    uint32_t mask=0;
    if(s)for(size_t i=0;i<s->world.slot_count;++i){const DlRegionSlot *slot=&s->world.slots[i];
        if(slot->state==DL_STREAM_RESIDENT&&slot->region_id&&slot->region_id<=32)
            mask|=UINT32_C(1)<<(slot->region_id-1);
    }
    return mask;
}
static uint32_t all_regions(const DlStreamCoordinator *s){
    return s->world.mission->region_count==32?UINT32_MAX:(UINT32_C(1)<<s->world.mission->region_count)-1u;
}
static unsigned bit_count(uint32_t bits){unsigned count=0;for(;bits;bits&=bits-1)++count;return count;}
static uint16_t guard_rows(const DlLevel *level){
    uint16_t mask=0;
    for(int i=0;i<level->model_count;++i){const DlModelInstance *model=&level->models[i];
        if(model->role==DL_MODEL_GUARD&&model->enemy_index>=0&&model->enemy_index<DL_MAX_ENEMIES)
            mask|=(uint16_t)(UINT16_C(1)<<model->enemy_index);
    }
    return mask;
}
static uint16_t drawable_enemies(const DlStreamCoordinator *s,uint16_t resident){
    /* Model membership is transactional too: an entering guard starts ticking
     * only when its visual rows join the published bank. Unmodeled actors keep
     * the ordinary region-based simulation predicate. */
    return resident&(uint16_t)(~s->guard_enemy_mask|s->published_guard_mask);
}
static bool doors_open(const DlGame *game){
    for(int i=0;i<game->level->object_count;++i)
        if(game->level->objects[i].kind==DL_OBJECT_DOOR&&dl_game_object_open(game,i))return true;
    return false;
}
static float bounds_distance(DlVec3 point,const DlMissionRegion *region){
    float x=fmaxf(region->minimum.x-point.x,fmaxf(0,point.x-region->maximum.x));
    float z=fmaxf(region->minimum.z-point.z,fmaxf(0,point.z-region->maximum.z));
    return sqrtf(x*x+z*z);
}
static void clear_queued(DlStreamCoordinator *s){s->queued_use=false;s->queued_object=-1;s->queued_transition=0;}
static uint32_t wanted_mask(DlStreamCoordinator *s,const DlGame *game){
    if(s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED){
        uint32_t wanted=dl_stream_scene_policy_update(&s->scene,game->player,s->view_regions,&s->policy_state);
        if(!wanted){s->failure=DL_COORDINATOR_DEMAND;return 0;}
        if(s->queued_use&&s->queued_transition){
            if(s->queued_transition>s->scene.bootstrap->stream->transition_count||s->queued_object<0||
                s->queued_object>=game->level->object_count){clear_queued(s);return wanted;}
            const DlStreamTransition *gate=&s->scene.bootstrap->stream->transitions[s->queued_transition-1];
            const DlObjectDef *object=&game->level->objects[s->queued_object];DlVec3 eye=dl_player_eye(game);
            float x=eye.x-object->position.x,y=eye.y-object->position.y,z=eye.z-object->position.z;
            if(gate->from_region==s->policy_state.current_region&&!dl_game_object_open(game,s->queued_object)&&
                x*x+y*y+z*z<=object->radius*object->radius){
                s->policy_state.pending_transition=s->queued_transition;
                wanted=dl_stream_scene_working_mask(&s->scene,s->policy_state.current_region)|
                    dl_stream_scene_working_mask(&s->scene,gate->to_region);
            }else clear_queued(s);
        }
        return wanted;
    }
    /* Open exterior coverage has no proven occlusion cut. */
    if(s->scene.bootstrap->stream->keep_neighbors_resident)return all_regions(s);
    uint32_t current=dl_stream_scene_region_at(&s->scene,game->player);
    if(!current){s->failure=DL_COORDINATOR_DEMAND;return 0;}
    uint32_t wanted=UINT32_C(1)<<(current-1);bool open=doors_open(game);
    for(uint32_t id=1;id<=s->world.mission->region_count;++id){
        DlMissionRegion region;
        if(dl_mission_region(s->world.mission,id,&region)!=DL_MISSION_OK){s->failure=DL_COORDINATOR_DEMAND;return 0;}
        if(open||bounds_distance(game->player,&region)<3.0f)wanted|=UINT32_C(1)<<(id-1);
    }
    for(int i=0;i<game->enemy_count;++i){const DlEnemy *enemy=&game->enemies[i];
        if(enemy->state!=DL_PATROL||enemy->awareness>0||enemy->heard_sound){
            uint32_t id=dl_stream_scene_region_at(&s->scene,enemy->position);
            if(id)wanted|=UINT32_C(1)<<(id-1);
        }
    }
    return wanted;
}
static DlStreamCoordinatorStatus check_loads(DlStreamCoordinator *s){
    for(size_t i=0;i<s->world.slot_count;++i)if(s->world.slots[i].state==DL_STREAM_FAILED)
        return world_result(s,s->world.slots[i].failure);
    return s->failure;
}
static DlStreamCoordinatorStatus request_wanted(DlStreamCoordinator *s,const DlGame *game,uint32_t wanted){
    if(bit_count(wanted)>s->world.slot_count)return s->failure=DL_COORDINATOR_DEMAND;
    if(check_loads(s)!=DL_COORDINATOR_OK)return s->failure;
    for(size_t i=0;i<s->world.slot_count;++i){DlRegionSlot *slot=&s->world.slots[i];
        if(slot->state==DL_STREAM_FREE)continue;
        if(!(wanted&(UINT32_C(1)<<(slot->region_id-1)))&&slot->state!=DL_STREAM_RETIRING){
            DlRegionHandle h={(uint32_t)i,slot->generation};uint32_t id=slot->region_id;
            bool resident=slot->state==DL_STREAM_RESIDENT;
            if(s->callbacks.abandon)s->callbacks.abandon(s->context,slot);
            if(world_result(s,dl_world_stream_cancel(&s->world,h))!=DL_COORDINATOR_OK)return s->failure;
            if(resident){++s->retired;observe(s,game,"retire","demand_changed",0,wanted,id);}
        }
    }
    for(uint32_t id=1;id<=s->world.mission->region_count;++id)if(wanted&(UINT32_C(1)<<(id-1))){
        DlRegionHandle h;DlStreamStatus status=dl_world_stream_request(&s->world,id,&h);
        /* Graphics-fenced retirement can temporarily occupy required slots. */
        if(status!=DL_STREAM_OK&&status!=DL_STREAM_BUSY&&status!=DL_STREAM_CAPACITY)return world_result(s,status);
    }
    return s->failure;
}
static uint64_t new_candidate(DlStreamCoordinator *s){
    s->candidate_job=UINT64_C(0x8000000000000000)|++s->candidate_sequence;
    s->prepared_generation=s->candidate_job;return s->candidate_job;
}
static void cancel_candidate(DlStreamCoordinator *s,const DlGame *game,const char *reason){
    bool preparing=s->preparing;uint64_t job=s->candidate_job;uint32_t regions=s->prepared_regions;
    if(s->gpu_started&&s->callbacks.renderer_cancel)s->callbacks.renderer_cancel(s->context,s->candidate_job);
    dl_stream_scene_prepare_cancel(&s->scene);
    s->preparing=s->assembling=s->gpu_started=s->gpu_ready=false;
    s->prepared_generation=0;s->prepared_regions=0;
    if(preparing)observe(s,game,"candidate_cancel",reason,job,regions,0);
}
DlStreamCoordinatorConfig dl_stream_coordinator_default_config(void){
    DlStreamCoordinatorConfig config={256,512,64,0,0,0,2};return config;
}
DlStreamCoordinatorStatus dl_stream_coordinator_init(DlStreamCoordinator *s,const DlLevel *bootstrap,
    const DlMission *mission,DlRegionSlot *slots,size_t slot_count,const DlStreamCoordinatorCallbacks *callbacks,
    const DlStreamCoordinatorConfig *config,void *context){
    if(!s||!bootstrap||!bootstrap->stream||!mission||!slots||!slot_count||
        slot_count>DL_STREAM_SCENE_MAX_RESIDENT_REGIONS||!callbacks||!callbacks->read||
        !mission->region_count||mission->region_count>DL_STREAM_SCENE_MAX_REGIONS)return DL_COORDINATOR_ARGUMENT;
    memset(s,0,sizeof *s);s->queued_object=-1;s->callbacks=*callbacks;s->context=context;
    s->config=config?*config:dl_stream_coordinator_default_config();
    if(!s->config.read_slice)s->config.read_slice=256;
    if(!s->config.pump_byte_budget)s->config.pump_byte_budget=s->config.read_slice*2;
    if(!s->config.assembly_records)s->config.assembly_records=64;
    if(!s->config.max_pump_attempts)s->config.max_pump_attempts=2;
    /* A missing clock always has a finite work-unit budget. */
    if(!callbacks->clock&&s->config.assembly_records==SIZE_MAX)s->config.assembly_records=64;
    dl_stream_scene_policy_reset(&s->policy_state);
    if(world_result(s,dl_world_stream_init(&s->world,mission,slots,slot_count,callbacks->read,context))!=DL_COORDINATOR_OK)return s->failure;
    s->world.read_slice=s->config.read_slice;
    s->guard_enemy_mask=guard_rows(bootstrap);
    return scene_result(s,dl_stream_scene_init(&s->scene,bootstrap,mission));
}
DlStreamCoordinatorStatus dl_stream_coordinator_start(DlStreamCoordinator *s,DlGame *game,int preset){
    if(!s||!game||s->started)return DL_COORDINATOR_ARGUMENT;
    if(s->failure!=DL_COORDINATOR_OK)return s->failure;
    if(!dl_game_start(game,s->scene.bootstrap,preset))return s->failure=DL_COORDINATOR_GAME;
    s->started=true;s->wanted_regions=wanted_mask(s,game);
    if(s->failure!=DL_COORDINATOR_OK)return s->failure;
    if(request_wanted(s,game,s->wanted_regions)!=DL_COORDINATOR_OK)return s->failure;
    observe(s,game,"demand","startup",0,s->wanted_regions,0);return s->failure;
}
DlStreamCoordinatorStatus dl_stream_coordinator_initial_step(DlStreamCoordinator *s,const DlGame *game,
    size_t byte_budget,bool *ready){
    if(!s||!game||!ready||!s->started||s->initial_cpu_ready)return DL_COORDINATOR_ARGUMENT;
    *ready=false;if(s->failure!=DL_COORDINATOR_OK)return s->failure;
    dl_world_stream_pump(&s->world,byte_budget);
    if(check_loads(s)!=DL_COORDINATOR_OK)return s->failure;
    *ready=(dl_stream_coordinator_resident_mask(s)&s->wanted_regions)==s->wanted_regions;return s->failure;
}
DlStreamCoordinatorStatus dl_stream_coordinator_initial_publish(DlStreamCoordinator *s,DlGame *game){
    if(!s||!game||!s->started||s->initial_cpu_ready)return DL_COORDINATOR_ARGUMENT;
    if(s->failure!=DL_COORDINATOR_OK)return s->failure;
    if((dl_stream_coordinator_resident_mask(s)&s->wanted_regions)!=s->wanted_regions)return DL_COORDINATOR_ARGUMENT;
    new_candidate(s);observe(s,game,"prepare_begin","startup",s->candidate_job,s->wanted_regions,0);
    if(scene_result(s,dl_stream_scene_refresh(&s->scene,&s->world,game))!=DL_COORDINATOR_OK)return s->failure;
    game->level=dl_stream_scene_level(&s->scene);
    if(!dl_game_query_cache_ready(game->level))return s->failure=DL_COORDINATOR_QUERY;
    s->published_guard_mask=guard_rows(game->level);
    dl_game_set_active_enemies(game,drawable_enemies(s,dl_stream_scene_enemy_mask(&s->scene,game)));
    s->view_regions=dl_stream_coordinator_resident_mask(s);s->initial_cpu_ready=true;
    observe(s,game,"cpu_ready","startup_assembled",s->candidate_job,s->wanted_regions,0);return s->failure;
}
void dl_stream_coordinator_initial_renderer_ready(DlStreamCoordinator *s,const DlGame *game){
    if(!s||!s->initial_cpu_ready||s->initial_ready||s->failure!=DL_COORDINATOR_OK)return;
    observe(s,game,"gpu_ready","startup_renderer_prepared",s->candidate_job,s->view_regions,0);
    s->initial_ready=true;s->prepared_generation=0;
    observe(s,game,"publish","startup_ready",s->candidate_job,s->view_regions,0);
}
DlStreamCoordinatorStatus dl_stream_coordinator_complete(DlStreamCoordinator *s,uint64_t rsp,uint64_t rdp){
    if(!s)return DL_COORDINATOR_ARGUMENT;
    return world_result(s,dl_world_stream_complete(&s->world,rsp,rdp));
}
DlStreamCoordinatorStatus dl_stream_coordinator_service(DlStreamCoordinator *s,DlGame *game){
    if(!s||!game||!s->started||!s->initial_cpu_ready)return DL_COORDINATOR_ARGUMENT;
    if(s->failure!=DL_COORDINATOR_OK)return s->failure;
    uint32_t service_start=clock_now(s);s->service_stage=0;
    uint16_t active_enemies=0;bool active_enemies_known=false;
    if(s->callbacks.graphics_completed){uint64_t rsp=0,rdp=0;s->callbacks.graphics_completed(s->context,&rsp,&rdp);
        if(dl_stream_coordinator_complete(s,rsp,rdp)!=DL_COORDINATOR_OK)return s->failure;}
    uint32_t previous=s->wanted_regions,wanted=wanted_mask(s,game);s->wanted_regions=wanted;
    if(s->failure!=DL_COORDINATOR_OK)return s->failure;
    if(previous!=wanted)observe(s,game,"demand",s->queued_use?"door_request":"movement",0,wanted,0);
    if(s->preparing&&(s->prepared_regions&~wanted))cancel_candidate(s,game,"demand_reversed");
    if(s->preparing){
        if(s->assembling){
            s->service_stage=1;uint32_t start=clock_now(s);bool ready=false;size_t records=0;
            do{
                /* Batch a few bounded records between platform clock calls.
                 * A clock callback per record needlessly consumes the target's
                 * preparation slice. The caller's work-unit cap still applies;
                 * publication and cancellation remain foreground boundaries. */
                size_t batch=s->config.assembly_records-records;
                if(batch>16)batch=16;
                if(scene_result(s,dl_stream_scene_prepare_step(&s->scene,batch,&ready))!=DL_COORDINATOR_OK){cancel_candidate(s,game,"assembly_failed");return s->failure;}
                records+=batch;
            }while(!ready&&records<s->config.assembly_records&&within_budget(s,start,s->config.prepare_ticks));
            if(ready){s->assembling=false;s->prepared_game.level=dl_stream_scene_pending(&s->scene);
                observe(s,game,"cpu_ready","assembly_complete",s->candidate_job,s->prepared_regions,0);}
        }else if(!s->gpu_started){
            s->service_stage=2;
            if(s->callbacks.renderer_begin)s->callbacks.renderer_begin(s->context,&s->prepared_game,s->candidate_job);
            s->gpu_started=true;
        }else if(!s->gpu_ready){
            s->service_stage=2;
            s->gpu_ready=!s->callbacks.renderer_step||s->callbacks.renderer_step(s->context,&s->prepared_game,s->config.prepare_ticks,s->candidate_job);
            if(s->gpu_ready)observe(s,game,"gpu_ready","renderer_prepared",s->candidate_job,s->prepared_regions,0);
        }else{
            s->service_stage=3;
            /* A renderer result may authorize only its still-live CPU candidate. */
            if(!s->prepared_generation||s->prepared_generation!=s->candidate_job||!s->scene.pending_ready)
                return s->failure=DL_COORDINATOR_SCENE;
            if(!dl_stream_scene_pending_guards_match(&s->scene,game))cancel_candidate(s,game,"guard_membership_changed");
            else{
                observe(s,game,"publish_begin","candidate_ready",s->candidate_job,s->prepared_regions,0);
                dl_stream_scene_publish(&s->scene);game->level=dl_stream_scene_level(&s->scene);
                if(!dl_game_query_cache_ready(game->level))return s->failure=DL_COORDINATOR_QUERY;
                s->published_guard_mask=guard_rows(game->level);
                active_enemies=dl_stream_scene_enemy_mask(&s->scene,game);active_enemies_known=true;
                dl_game_set_active_enemies(game,drawable_enemies(s,active_enemies));
                if(s->callbacks.renderer_commit)s->callbacks.renderer_commit(s->context,game,s->candidate_job);
                s->view_regions=s->prepared_regions;s->preparing=false;s->gpu_started=s->gpu_ready=false;s->prepared_generation=0;
                observe(s,game,"publish","candidate_ready",s->candidate_job,s->view_regions,0);
            }
        }
    }else{
        if(request_wanted(s,game,wanted)!=DL_COORDINATOR_OK)return s->failure;
        uint32_t resident=dl_stream_coordinator_resident_mask(s);
        /* Publish removals promptly; batch additions until demand is complete.
         * The platform geometry pool holds at most the bounded view union. */
        bool masks_changed=resident!=s->view_regions&&((s->view_regions&~resident)||(resident&wanted)==wanted);
        active_enemies=dl_stream_scene_enemy_mask(&s->scene,game);active_enemies_known=true;
        bool guards_changed=s->published_guard_mask!=(active_enemies&s->guard_enemy_mask);
        if(masks_changed||guards_changed){
            uint32_t candidate=masks_changed?(bit_count(resident|s->view_regions)<=DL_STREAM_SCENE_MAX_RESIDENT_REGIONS?resident:resident&s->view_regions):s->view_regions;
            if(masks_changed&&candidate==s->view_regions)return s->failure=DL_COORDINATOR_DEMAND;
            s->service_stage=1;
            if(scene_result(s,dl_stream_scene_prepare_begin_mask(&s->scene,&s->world,game,candidate))!=DL_COORDINATOR_OK)return s->failure;
            s->prepared_game=*game;s->prepared_regions=candidate;s->preparing=true;s->assembling=true;new_candidate(s);
            observe(s,game,"prepare_begin",masks_changed?"resident_set_changed":"guard_membership_changed",s->candidate_job,candidate,0);
        }
    }
    /* Borrowed snapshots are immutable. Continue bounded IO in other slots;
     * defer demand cancellation until publication releases the CPU borrows. */
    if(s->service_stage!=3&&within_budget(s,service_start,s->config.service_ticks)){
        uint32_t start=clock_now(s);size_t processed=0;
        for(unsigned attempt=0;attempt<s->config.max_pump_attempts;++attempt){
            processed+=dl_world_stream_pump(&s->world,s->config.pump_byte_budget);
            if((s->callbacks.io_busy&&s->callbacks.io_busy(s->context))||processed>=s->config.read_slice||
                !within_budget(s,start,s->config.read_ticks))break;
        }
    }
    if(check_loads(s)!=DL_COORDINATOR_OK){cancel_candidate(s,game,"read_failed");return s->failure;}
    if(!active_enemies_known)active_enemies=dl_stream_scene_enemy_mask(&s->scene,game);
    dl_game_set_active_enemies(game,drawable_enemies(s,active_enemies));return s->failure;
}
void dl_stream_coordinator_filter_input(DlStreamCoordinator *s,const DlGame *game,DlInput *input){
    if(!s||!game||!input||!s->started)return;
    bool near_closed=false;int near_object=-1;float nearest=INFINITY;
    uint32_t resident=s->view_regions;
    if(s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED&&
        dl_stream_scene_gate_waiting(&s->scene,game->player,resident)){
        input->forward=0;input->strafe=0;input->jump=false;
    }
    DlVec3 eye=dl_player_eye(game);
    for(int i=0;i<game->level->object_count;++i){const DlObjectDef *object=&game->level->objects[i];
        float x=eye.x-object->position.x,y=eye.y-object->position.y,z=eye.z-object->position.z;float distance=x*x+y*y+z*z;
        if(object->kind==DL_OBJECT_DOOR&&!dl_game_object_open(game,i)&&distance<=object->radius*object->radius&&distance<nearest){
            near_closed=true;near_object=i;nearest=distance;}
    }
    uint32_t transition=0,needed=all_regions(s);
    if(s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED){
        needed=near_object>=0?dl_stream_scene_door_target(&s->scene,s->policy_state.current_region,
            s->scene.bootstrap->stream->object_entity_ids[near_object],&transition):0;
        if(s->queued_use&&(s->queued_object!=near_object||!transition||transition!=s->queued_transition))clear_queued(s);
    }
    if(near_closed&&(resident&needed)!=needed){
        if(input->use){s->queued_use=true;s->queued_object=near_object;s->queued_transition=transition;}input->use=false;
    }else if(s->queued_use){input->use=near_closed&&near_object==s->queued_object;clear_queued(s);}
}
void dl_stream_coordinator_cancel(DlStreamCoordinator *s){
    if(!s)return;
    cancel_candidate(s,NULL,"shutdown");clear_queued(s);
}
DlStreamCoordinatorStatus dl_stream_coordinator_submitted(DlStreamCoordinator *s,uint64_t rsp,uint64_t rdp){
    if(!s)return DL_COORDINATOR_ARGUMENT;
    for(size_t i=0;i<s->world.slot_count;++i){DlRegionSlot *slot=&s->world.slots[i];
        if(slot->state!=DL_STREAM_RESIDENT)continue;
        DlRegionHandle h={(uint32_t)i,slot->generation};const DlMissionChunk *chunk;
        if(world_result(s,dl_world_stream_borrow(&s->world,h,&chunk))!=DL_COORDINATOR_OK)return s->failure;
        DlStreamStatus status=dl_world_stream_submit(&s->world,h,rsp,rdp);
        DlStreamStatus release=dl_world_stream_release(&s->world,h);
        if(world_result(s,status)!=DL_COORDINATOR_OK)return s->failure;
        if(world_result(s,release)!=DL_COORDINATOR_OK)return s->failure;
    }
    return s->failure;
}
uint32_t dl_stream_coordinator_current(const DlStreamCoordinator *s,const DlGame *game){
    if(!s||!game)return 0;
    return s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED?
        s->policy_state.current_region:dl_stream_scene_region_at(&s->scene,game->player);
}
bool dl_stream_coordinator_settled(const DlStreamCoordinator *s){
    return !s||(s->initial_ready&&!s->preparing&&s->failure==DL_COORDINATOR_OK&&s->view_regions==dl_stream_coordinator_resident_mask(s)&&
        (s->view_regions&s->wanted_regions)==s->wanted_regions);
}
void dl_stream_coordinator_snapshot(const DlStreamCoordinator *s,const DlGame *game,DlStreamCoordinatorSnapshot *out){
    if(!out)return;
    memset(out,0,sizeof *out);out->queued_object=-1;if(!s)return;
    out->current_region=dl_stream_coordinator_current(s,game);out->wanted_mask=s->wanted_regions;
    out->physical_mask=dl_stream_coordinator_resident_mask(s);out->published_mask=s->initial_ready?s->view_regions:0;
    out->candidate_mask=s->initial_ready?(s->preparing?s->prepared_regions:0):s->wanted_regions;
    out->pending_transition=s->policy_state.pending_transition;out->revision=s->scene.revision;
    out->retired=s->retired;out->service_stage=s->service_stage;out->candidate_job=s->candidate_job;
    out->completed_rsp=s->world.completed_rsp;out->completed_rdp=s->world.completed_rdp;
    out->preparing=!s->initial_ready||s->preparing;
    out->cpu_ready=s->initial_ready?(s->preparing&&!s->assembling):s->initial_cpu_ready;
    out->gpu_ready=s->gpu_ready;out->initial_ready=s->initial_ready;out->queued_use=s->queued_use;
    out->queued_object=s->queued_object;out->queued_transition=s->queued_transition;out->settled=dl_stream_coordinator_settled(s);
}
const char *dl_stream_coordinator_status_name(DlStreamCoordinatorStatus status){
    static const char *const names[]={"ok","argument","game","demand","world","scene","query"};
    return (unsigned)status<sizeof names/sizeof names[0]?names[status]:"unknown";
}
