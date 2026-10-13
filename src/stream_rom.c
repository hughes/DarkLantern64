#include "stream_rom.h"
#include "stream_scene.h"
#include "dl_music.h"
#include "dl_profile.h"
#include "runtime_trace.h"
#include "runtime_diagnostics.h"
#include "tick.h"
#include <libdragon.h>
#include <malloc.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

enum { SLOT_COUNT=5, READ_SLICE=8192, DMA_BYTES=8208, MAX_DIRECTORY=65536, MAX_CHUNK=65536 };
#define PREPARE_TICKS (TICKS_PER_SECOND*11u/2000u)
typedef struct {
    DlMission mission;
    DlWorldStream world;
    DlStreamScene scene;
    DlRegionSlot slots[SLOT_COUNT];
    uint8_t *directory, *dma;
    uint32_t rom, file_bytes, io_offset, io_bytes, io_prefix;
    bool io_pending, io_abandoned, queued_use;
    uint32_t view_regions, retired, wanted_regions,queued_transition;
    DlStreamPolicyState policy_state;
    int queued_object;
    size_t platform_allocated;
    bool preparing, assembling, gpu_started, gpu_ready;
    DlGame prepared_game;
    uint32_t prepared_regions, service_stage;
#if DL_RUNTIME_TRACE
    DlRuntimeTrace trace;
    uint64_t candidate_job, candidate_sequence, published_jobs[SLOT_COUNT];
    uint32_t trace_frames;
    bool trace_initial_ready, trace_initial_cpu_ready;
    DlRuntimeRecord trace_cost_group;
    uint32_t trace_motion_frame, trace_motion_gate, trace_motion_physical, trace_motion_published;
    float trace_motion_forward, trace_motion_strafe;
    unsigned trace_motion_readiness;
#endif
} StreamRom;
static StreamRom *active;
static uint32_t resident_mask(const StreamRom *s);
#if DL_RUNTIME_TRACE
#ifndef DL_RUNTIME_TRACE_BUILD_ID
#define DL_RUNTIME_TRACE_BUILD_ID "unidentified"
#endif
static uint32_t trace_session;
static uint32_t trace_clock(void *context){(void)context;return TICKS_READ();}
static void trace_sink(void *context,const DlRuntimeRecord *r){
    StreamRom *s=context;
    const uint32_t *d=r->data;const uint64_t *w=r->wide;const float *p=r->position;
    unsigned long session=s->trace.session;unsigned long long seq=r->seq,time=r->time,job=r->job;
    if(r->type==DL_RT_EVENT)
        debugf("DL64 rt_event session=%lu seq=%llu time=%llu kind=%s reason=%s job=%llu region=%lu slot=%lu generation=%lu value=%lu total=%lu mask=%lu\n",
            session,seq,time,r->kind,r->reason,job,(unsigned long)d[0],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3],(unsigned long)d[4],(unsigned long)d[5]);
    else if(r->type==DL_RT_SNAPSHOT)
        debugf("DL64 rt_snapshot session=%lu seq=%llu time=%llu current=%lu wanted=%lu physical=%lu published=%lu candidate=%lu gate=%lu preparing=%lu cpu_ready=%lu gpu_ready=%lu revision=%lu slots=%lu completed_rsp=%llu completed_rdp=%llu player=%.5f,%.5f,%.5f\n",
            session,seq,time,(unsigned long)d[0],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3],(unsigned long)d[4],(unsigned long)d[5],(unsigned long)d[6],(unsigned long)d[7],(unsigned long)d[8],(unsigned long)d[9],(unsigned long)d[10],
            (unsigned long long)w[0],(unsigned long long)w[1],p[0],p[1],p[2]);
    else if(r->type==DL_RT_SLOT)
        debugf("DL64 rt_slot session=%lu seq=%llu time=%llu snapshot=%llu job=%llu slot=%lu generation=%lu region=%lu state=%lu bytes=%lu total=%lu pins=%lu rsp=%llu rdp=%llu\n",
            session,seq,time,(unsigned long long)w[2],job,(unsigned long)d[0],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3],(unsigned long)d[4],(unsigned long)d[5],(unsigned long)d[6],(unsigned long long)w[0],(unsigned long long)w[1]);
    else if(r->type==DL_RT_COST)
        debugf("DL64 rt_cost session=%lu seq=%llu time=%llu job=%llu stage=%lu elapsed=%llu cpu=%llu audio=%llu commit=%llu startup=%lu samples=%lu max_cpu=%lu\n",
            session,seq,time,job,(unsigned long)d[0],(unsigned long long)w[0],(unsigned long long)w[1],(unsigned long long)w[2],(unsigned long long)w[3],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3]);
    else if(r->type==DL_RT_MOTION)
        debugf("DL64 rt_motion session=%lu seq=%llu time=%llu gate=%lu from=%lu to=%lu door=%lu required=%lu missing_physical=%lu missing_published=%lu waiting=%lu suppressed=%lu jump=%lu direction=%s readiness=%s position=%.5f,%.5f,%.5f delta=%.5f,%.5f,%.5f cpu_ready=%lu gpu_ready=%lu candidate=%lu\n",
            session,seq,time,(unsigned long)d[0],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3],(unsigned long)d[4],(unsigned long)d[5],(unsigned long)d[6],(unsigned long)d[7],(unsigned long)d[8],(unsigned long)d[9],r->reason,r->kind,
            p[0],p[1],p[2],p[3],p[4],p[5],(unsigned long)d[10],(unsigned long)d[11],(unsigned long)d[12]);
    else if(r->type==DL_RT_INTERACTION)
        debugf("DL64 rt_interaction session=%lu seq=%llu time=%llu object=%lu entity=%lu eligible=%lu in_range=%lu raw_clear=%lu effective_clear=%lu blocker=%ld kind=%s source_entity=%lu source_region=%lu source_record=%lu source_gate=%lu associated_object=%ld query_cache_ready=%lu reason=%s suppressed=%lu probe=%s\n",
            session,seq,time,(unsigned long)d[0],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3],(unsigned long)d[4],(unsigned long)d[5],(long)(int32_t)d[6],r->reason,(unsigned long)d[7],(unsigned long)d[8],(unsigned long)d[9],(unsigned long)d[10],(long)(int32_t)d[11],(unsigned long)d[12],r->kind,(unsigned long)d[13],d[14]?"effective":"raw");
}
static void trace_event(StreamRom *s,const char *kind,const char *reason,uint64_t job,uint32_t mask){
    dl_runtime_trace_event(&s->trace,kind,reason,job,0,0,0,0,0,mask);
}
static void trace_snapshot(StreamRom *s,const DlGame *game){
    DlRuntimeRecord r={.type=DL_RT_SNAPSHOT};
    r.data[0]=dl_stream_rom_current(game);r.data[1]=s->wanted_regions;r.data[2]=resident_mask(s);
    r.data[3]=s->trace_initial_ready?s->view_regions:0;
    r.data[4]=s->trace_initial_ready?(s->preparing?s->prepared_regions:0):s->wanted_regions;
    r.data[5]=s->policy_state.pending_transition;r.data[6]=!s->trace_initial_ready||s->preparing;
    r.data[7]=s->trace_initial_ready?(s->preparing&&!s->assembling):s->trace_initial_cpu_ready;
    r.data[8]=s->gpu_ready;r.data[9]=s->scene.revision;r.data[10]=(uint32_t)s->world.slot_count;
    r.wide[0]=s->world.completed_rsp;r.wide[1]=s->world.completed_rdp;
    r.position[0]=game->player.x;r.position[1]=game->player.y;r.position[2]=game->player.z;
    uint64_t snapshot=dl_runtime_trace_push(&s->trace,r);
    for(size_t i=0;i<s->world.slot_count;++i){const DlRegionSlot *slot=&s->slots[i];
        r=(DlRuntimeRecord){.type=DL_RT_SLOT,.job=slot->state==DL_STREAM_FREE?0:dl_runtime_region_job((uint32_t)i,slot->generation)};
        r.data[0]=(uint32_t)i;r.data[1]=slot->generation;r.data[2]=slot->region_id;r.data[3]=slot->state;
        r.data[4]=(uint32_t)slot->bytes_read;r.data[5]=slot->region.chunk_bytes;r.data[6]=slot->cpu_pins;
        r.wide[0]=slot->last_rsp;r.wide[1]=slot->last_rdp;r.wide[2]=snapshot;dl_runtime_trace_push(&s->trace,r);
    }
}
static void trace_publication(StreamRom *s,const DlGame *game,const char *reason){
    trace_event(s,"publish",reason,s->candidate_job,s->view_regions);
    for(size_t i=0;i<s->world.slot_count;++i){const DlRegionSlot *slot=&s->slots[i];
        if(slot->state!=DL_STREAM_RESIDENT||!(s->view_regions&(UINT32_C(1)<<(slot->region_id-1))))continue;
        uint64_t job=dl_runtime_region_job((uint32_t)i,slot->generation);
        if(s->published_jobs[i]!=job){s->published_jobs[i]=job;
            dl_runtime_trace_event(&s->trace,"publish",reason,job,slot->region_id,(uint32_t)i,slot->generation,(uint32_t)slot->bytes_read,slot->region.chunk_bytes,s->view_regions);}
    }
    trace_snapshot(s,game);
}
static void trace_cost_flush(StreamRom *s){
    if(s->trace_cost_group.data[2]){dl_runtime_trace_push(&s->trace,s->trace_cost_group);
        s->trace_cost_group=(DlRuntimeRecord){0};}
}
static void trace_cost(StreamRom *s,DlProfileMark start,uint64_t job,uint32_t commit,bool startup){
    DlProfileMark end=dl_profile_mark();uint32_t elapsed=end.ticks-start.ticks,audio=end.audio_ticks-start.audio_ticks;
    DlRuntimeRecord r={.type=DL_RT_COST,.job=job};r.data[0]=s->service_stage;r.data[1]=startup?elapsed:0;r.data[2]=1;r.data[3]=elapsed>=audio?elapsed-audio:0;
    r.wide[0]=elapsed;r.wide[1]=elapsed>=audio?elapsed-audio:0;r.wide[2]=audio;r.wide[3]=commit;
    if(startup){dl_runtime_trace_push(&s->trace,r);return;}
    DlRuntimeRecord *group=&s->trace_cost_group;
    if(group->data[2]&&(group->job!=job||group->data[0]!=r.data[0]))trace_cost_flush(s);
    if(!group->data[2])*group=r;
    else{for(unsigned i=0;i<4;++i)group->wide[i]+=r.wide[i];++group->data[2];
        if(r.data[3]>group->data[3])group->data[3]=r.data[3];}
    if(group->data[2]>=60)trace_cost_flush(s);
}
#define TRACE_EVENT(s,kind,reason,job,mask) trace_event(s,kind,reason,job,mask)
#define TRACE_SNAPSHOT(s,game) trace_snapshot(s,game)
#define TRACE_PUBLICATION(s,game,reason) trace_publication(s,game,reason)
#else
#define TRACE_EVENT(s,kind,reason,job,mask) ((void)0)
#define TRACE_SNAPSHOT(s,game) ((void)0)
#define TRACE_PUBLICATION(s,game,reason) ((void)0)
#endif

void dl_stream_rom_trace_flush(void){
#if DL_RUNTIME_TRACE
    if(active)dl_runtime_trace_flush_limit(&active->trace,trace_sink,active,1);
#endif
}
void dl_stream_rom_trace_poll(void){
#if DL_RUNTIME_TRACE
    if(active&&!active->trace.finished){uint32_t start=TICKS_READ();
        dl_runtime_trace_time(&active->trace);active->trace.trace_ticks+=(uint32_t)(TICKS_READ()-start);}
#endif
}
void dl_stream_rom_trace_finish(void){
#if DL_RUNTIME_TRACE
    if(!active||active->trace.finished)return;
    trace_cost_flush(active);dl_runtime_trace_flush(&active->trace,trace_sink,active);DlRuntimeTrace *t=&active->trace;
    debugf("DL64 rt_end session=%lu seq=%llu time=%llu emitted=%llu dropped=%llu trace_ticks=%llu buffer_bytes=%lu\n",
        (unsigned long)t->session,(unsigned long long)(t->sequence+1),(unsigned long long)dl_runtime_trace_time(t),
        (unsigned long long)t->emitted,(unsigned long long)t->dropped,(unsigned long long)t->trace_ticks,(unsigned long)sizeof *t);
    t->finished=true;
#endif
}
void dl_stream_rom_trace_startup_ready(const DlGame *game,uint32_t elapsed,uint32_t audio){
#if DL_RUNTIME_TRACE
    if(!active)return;
    StreamRom *s=active;
    TRACE_EVENT(s,"gpu_ready","startup_renderer_prepared",s->candidate_job,s->view_regions);
    DlRuntimeRecord r={.type=DL_RT_COST,.job=s->candidate_job};r.data[0]=2;r.data[1]=elapsed;r.data[2]=1;r.data[3]=elapsed>=audio?elapsed-audio:0;
    r.wide[0]=elapsed;r.wide[1]=elapsed>=audio?elapsed-audio:0;r.wide[2]=audio;
    dl_runtime_trace_push(&s->trace,r);
    /* Initial scene is drawable only after the caller prepares its renderer. */
    s->trace_initial_ready=true;TRACE_PUBLICATION(s,game,"startup_ready");
    dl_runtime_trace_flush(&s->trace,trace_sink,s);
#else
    (void)game;(void)elapsed;(void)audio;
#endif
}
static bool pi_busy(void){return (*PI_STATUS&3u)!=0;}

/* PI writes only an aligned staging buffer. It never retains a region-slot
 * destination, so a cancelled region can safely release its slot. Pending
 * cancellation discards the result after PI finishes. The context/staging
 * lifetime still waits for that transfer at mission shutdown. */
static size_t read_region(void *context,uint32_t offset,void *destination,size_t wanted){
    StreamRom *s=context;
    if(!wanted||wanted>READ_SLICE||offset>s->file_bytes||wanted>s->file_bytes-offset)return SIZE_MAX;
    if(s->io_pending){
        if(pi_busy())return 0;
        if(s->io_abandoned){s->io_pending=false;s->io_abandoned=false;}
        else{
            /* Other slots wait without discarding the first slot's completed
             * result. The manager visits all slots once per pump. */
            if(offset!=s->io_offset)return 0;
            size_t count=wanted<s->io_bytes?wanted:s->io_bytes;
            memcpy(destination,s->dma+s->io_prefix,count);
            s->io_offset+=(uint32_t)count;s->io_prefix+=(uint32_t)count;s->io_bytes-=(uint32_t)count;
            if(!s->io_bytes)s->io_pending=false;
            return count;
        }
    }
    if(pi_busy())return 0;
    uint32_t prefix=offset&1u;
    size_t bytes=(wanted+prefix+1u)&~(size_t)1;
    uint32_t aligned=offset-prefix;
    if(bytes>s->file_bytes-aligned)return SIZE_MAX;
    data_cache_hit_invalidate(s->dma,DMA_BYTES);
    s->io_offset=offset;s->io_bytes=(uint32_t)wanted;s->io_prefix=prefix;s->io_pending=true;
    dma_read_raw_async(s->dma,s->rom+aligned,(unsigned long)bytes);
    return 0;
}
static void abandon_read(StreamRom *s,const DlRegionSlot *slot){
    if(s->io_pending&&s->io_offset>=slot->region.chunk_offset&&
        s->io_offset-slot->region.chunk_offset<slot->region.chunk_bytes)s->io_abandoned=true;
}
static void completion(StreamRom *s){
    uint64_t rsp,rdp;dl_render_stream_completed(&rsp,&rdp);
    assertf(dl_world_stream_complete(&s->world,rsp,rdp)==DL_STREAM_OK,"Stream completion regressed");
}
void dl_stream_rom_close(void){
    if(!active)return;
    /* Caller releases the old renderer first, reaching its RDP ownership
     * boundary. PI may still own the independent read staging buffer. */
    dl_stream_scene_prepare_cancel(&active->scene);
    completion(active);
    if(active->io_pending)dma_wait();
    dl_stream_rom_trace_finish();
    for(size_t i=0;i<active->world.slot_count;++i)free(active->slots[i].buffer);
    free(active->dma);free(active->directory);free(active);active=NULL;
}
bool dl_stream_rom_active(void){return active!=NULL;}
bool dl_stream_rom_music_ready(void){return !active||!active->io_pending||!pi_busy();}
const DlWorldStream *dl_stream_rom_world(void){return active?&active->world:NULL;}
uint32_t dl_stream_rom_retired(void){return active?active->retired:0;}
uint32_t dl_stream_rom_service_stage(void){return active?active->service_stage:0;}
uint32_t dl_stream_rom_view_mask(void){return active?active->view_regions:0;}
bool dl_stream_rom_settled(void){
    return !active||(!active->preparing&&active->view_regions==resident_mask(active)&&
        (active->view_regions&active->wanted_regions)==active->wanted_regions);
}
uint32_t dl_stream_rom_current(const DlGame *g){
    return active?(active->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED?
        active->policy_state.current_region:dl_stream_scene_region_at(&active->scene,g->player)):0;
}
static uint32_t all_regions(const StreamRom *s){return s->mission.region_count==32?UINT32_MAX:(UINT32_C(1)<<s->mission.region_count)-1u;}
static uint32_t resident_mask(const StreamRom *s){
    uint32_t mask=0;
    for(size_t i=0;i<s->world.slot_count;++i)if(s->slots[i].state==DL_STREAM_RESIDENT)mask|=UINT32_C(1)<<(s->slots[i].region_id-1);
    return mask;
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
static uint32_t wanted_mask(StreamRom *s,const DlGame *game){
    if(s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED){
        uint32_t wanted=dl_stream_scene_policy_update(&s->scene,game->player,s->view_regions,&s->policy_state);
        assertf(wanted,"Stream start outside activation bounds");
        if(s->queued_use&&s->queued_transition){
            const DlStreamTransition *gate=&s->scene.bootstrap->stream->transitions[s->queued_transition-1];
            const DlObjectDef *object=&game->level->objects[s->queued_object];DlVec3 eye=dl_player_eye(game);
            float x=eye.x-object->position.x,y=eye.y-object->position.y,z=eye.z-object->position.z;
            if(gate->from_region==s->policy_state.current_region&&!dl_game_object_open(game,s->queued_object)&&x*x+y*y+z*z<=object->radius*object->radius){
                s->policy_state.pending_transition=s->queued_transition;
                wanted=dl_stream_scene_working_mask(&s->scene,s->policy_state.current_region)|dl_stream_scene_working_mask(&s->scene,gate->to_region);
            }else{s->queued_use=false;s->queued_object=-1;s->queued_transition=0;}
        }
        return wanted;
    }
    /* Open exterior coverage has no proven occlusion cut. Keep all authored
     * regions rather than retiring visible architecture. Outside the partial
     * import's bounds, retain coverage and allow the existing fall recovery. */
    if(s->scene.bootstrap->stream->keep_neighbors_resident)
        return all_regions(s);
    uint32_t current=dl_stream_scene_region_at(&s->scene,game->player);
    assertf(current,"Player outside streaming probe bounds");
    uint32_t wanted=UINT32_C(1)<<(current-1);
    bool open=doors_open(game)||s->scene.bootstrap->stream->keep_neighbors_resident;
    for(uint32_t id=1;id<=s->mission.region_count;++id){
        DlMissionRegion region;assertf(dl_mission_region(&s->mission,id,&region)==DL_MISSION_OK,"Bad region demand");
        /* This two-room proof has one opaque shared wall/gate. Opening the
         * gate exposes both rooms; a 3 m approach window prefetches its peer.
         * General portal/PVS demand remains the next spatial increment. */
        if(open||bounds_distance(game->player,&region)<3.0f)wanted|=UINT32_C(1)<<(id-1);
    }
    for(int i=0;i<game->enemy_count;++i){
        const DlEnemy *enemy=&game->enemies[i];
        if(enemy->state!=DL_PATROL||enemy->awareness>0||enemy->heard_sound){
            uint32_t id=dl_stream_scene_region_at(&s->scene,enemy->position);
            if(id)wanted|=UINT32_C(1)<<(id-1);
        }
    }
    return wanted;
}
static void request_wanted(StreamRom *s,uint32_t wanted){
    unsigned count=0;for(uint32_t bits=wanted;bits;bits&=bits-1)++count;
    assertf(count<=s->world.slot_count,"Authored demand exceeds bounded region slots");
    for(size_t i=0;i<s->world.slot_count;++i){
        DlRegionSlot *slot=&s->slots[i];
        if(slot->state==DL_STREAM_FREE)continue;
        assertf(slot->state!=DL_STREAM_FAILED,"Region %lu failed: %s/%s",(unsigned long)slot->region_id,
            dl_world_stream_status_name(slot->failure),dl_mission_status_name(slot->validation_failure));
        if(!(wanted&(UINT32_C(1)<<(slot->region_id-1)))&&slot->state!=DL_STREAM_RETIRING){
            DlRegionHandle h={(uint32_t)i,slot->generation};uint32_t id=slot->region_id;
            bool resident=slot->state==DL_STREAM_RESIDENT;abandon_read(s,slot);
            assertf(dl_world_stream_cancel(&s->world,h)==DL_STREAM_OK,"Region cancellation failed");
            if(resident){++s->retired;debugf("DL64 stream_retire region=%lu total=%lu\n",(unsigned long)id,(unsigned long)s->retired);}
        }
    }
    for(uint32_t id=1;id<=s->mission.region_count;++id)if(wanted&(UINT32_C(1)<<(id-1))){
        DlRegionHandle h;DlStreamStatus status=dl_world_stream_request(&s->world,id,&h);
        /* Retiring resources remain fenced. Retry once completion frees the
         * slot instead of increasing capacity or destroying their ownership. */
        assertf(status==DL_STREAM_OK||status==DL_STREAM_BUSY||status==DL_STREAM_CAPACITY,
            "Region request %lu: %s",(unsigned long)id,dl_world_stream_status_name(status));
    }
}
static void refresh(StreamRom *s,DlGame *game,bool renderer_ready){
    completion(s);
    assertf(!renderer_ready,"Initial refresh only");
    assertf(dl_stream_scene_refresh(&s->scene,&s->world,game)==DL_STREAM_SCENE_OK,"Invalid streamed working set");
    game->level=dl_stream_scene_level(&s->scene);
    assertf(dl_game_query_cache_ready(game->level),"Published collision index is incomplete");
    dl_game_set_active_enemies(game,dl_stream_scene_enemy_mask(&s->scene,game));
    s->view_regions=resident_mask(s);
    /* Region DMA owns only staging, but renderer DFS asset loads also use PI.
     * Preserve the completed staging result for the next bounded pump. */
    if(s->io_pending&&pi_busy())dma_wait();
    debugf("DL64 stream_view resident_mask=%lu triangles=%d colliders=%d active_enemies=%u loads=%llu retired=%lu reserved=%lu allocated_slots=%lu adapter_bytes=%lu\n",
        (unsigned long)s->view_regions,s->scene.banks[s->scene.active_bank].triangle_count,
        game->level->collider_count,(unsigned)game->active_enemy_mask,
        (unsigned long long)s->world.stats.completed_loads,(unsigned long)s->retired,
        (unsigned long)s->world.stats.reserved_bytes,(unsigned long)s->world.stats.allocated_slot_bytes,
        (unsigned long)s->platform_allocated);
}
void dl_stream_rom_start(DlGame *game,const DlLevel *bootstrap,int preset){
#if DL_RUNTIME_TRACE
    DlProfileMark trace_start=dl_profile_mark();
#endif
    assertf(!active&&bootstrap&&bootstrap->stream,"Invalid stream mission start");
    const DlStreamDefinition *definition=bootstrap->stream;
    /* Chunk slots grow only to the cooked worst reachable set; scene geometry
     * and collision banks retain the same capacities across larger missions. */
    assertf(definition->region_count&&definition->region_count<=DL_STREAM_SCENE_MAX_REGIONS&&
        (definition->policy==DL_STREAM_POLICY_AUTHORED||definition->region_count==2)&&definition->directory_bytes<=MAX_DIRECTORY&&
        definition->max_region_bytes<=MAX_CHUNK,"Streaming demo exceeds its bounded adapter contract");
    active=calloc(1,sizeof *active);assertf(active,"Cannot allocate stream scene adapter");
    StreamRom *s=active;s->queued_object=-1;
#if DL_RUNTIME_TRACE
    dl_runtime_trace_init(&s->trace,++trace_session,trace_clock,NULL);
    debugf("DL64 rt_begin version=1 session=%lu build=%s package=%s ticks_per_second=%lu slots=%lu\n",
        (unsigned long)s->trace.session,DL_RUNTIME_TRACE_BUILD_ID,definition->package_sha256?definition->package_sha256:"unidentified",
        (unsigned long)TICKS_PER_SECOND,(unsigned long)(definition->max_resident_regions?definition->max_resident_regions:2));
#endif
    dl_stream_scene_policy_reset(&s->policy_state);
    assertf(strncmp(definition->package_path,"rom:/",5)==0,"Invalid mission package path");
    const char *path=definition->package_path+4;
    /* This SDK's dfs_rom_addr returns a KSEG1-mapped address despite the header
     * calling it physical. Raw PI DMA needs the actual cartridge bus address;
     * the synchronous dma_read wrapper normally performs this conversion. */
    s->rom=dfs_rom_addr(path)&UINT32_C(0x1fffffff);int file_bytes=dfs_rom_size(path);
    assertf(s->rom&&file_bytes>0&&(uint32_t)file_bytes==definition->file_bytes,"Mission package size/path mismatch");
    s->file_bytes=(uint32_t)file_bytes;
    s->directory=malloc(definition->directory_bytes);s->dma=memalign(16,DMA_BYTES);
    assertf(s->directory&&s->dma,"Cannot allocate mission directory/read staging");
    int file=dfs_open(path);assertf(file>=0,"Cannot open mission directory");
    int read=dfs_read(s->directory,1,(int)definition->directory_bytes,(uint32_t)file);dfs_close((uint32_t)file);
    assertf(read==(int)definition->directory_bytes,"Truncated mission directory");
    assertf(dl_mission_open(s->directory,definition->directory_bytes,s->file_bytes,&s->mission)==DL_MISSION_OK,
        "Mission directory validation failed");
    assertf(s->mission.region_count==definition->region_count,"Mission region count mismatch");
    size_t capacity=(definition->max_region_bytes+15u)&~(size_t)15;
    uint32_t slot_count=definition->max_resident_regions?definition->max_resident_regions:2;
    assertf(slot_count&&slot_count<=SLOT_COUNT,"Invalid bounded slot count");
    for(uint32_t i=0;i<slot_count;++i){s->slots[i].buffer=memalign(16,capacity);s->slots[i].capacity=capacity;assertf(s->slots[i].buffer,"Cannot allocate region slot");}
    s->platform_allocated=sizeof *s+definition->directory_bytes+DMA_BYTES+slot_count*capacity;
    assertf(dl_world_stream_init(&s->world,&s->mission,s->slots,slot_count,read_region,s)==DL_STREAM_OK,"Cannot initialize streaming pool");
    s->world.read_slice=READ_SLICE;
#if DL_RUNTIME_TRACE
    s->world.trace=&s->trace;
#endif
    assertf(dl_stream_scene_init(&s->scene,bootstrap,&s->mission)==DL_STREAM_SCENE_OK,"Invalid streaming bootstrap");
    assertf(dl_game_start(game,bootstrap,preset),"Invalid stream test start");
    uint32_t wanted=wanted_mask(s,game);s->wanted_regions=wanted;request_wanted(s,wanted);
    TRACE_EVENT(s,"demand","startup",0,wanted);TRACE_SNAPSHOT(s,game);
    uint32_t started=TICKS_READ();
    while((resident_mask(s)&wanted)!=wanted){
        dl_world_stream_pump(&s->world,READ_SLICE);
#if DL_RUNTIME_TRACE
        if(s->trace.count>=DL_RUNTIME_TRACE_CAPACITY/2)dl_runtime_trace_flush(&s->trace,trace_sink,s);
#endif
        assertf((uint32_t)(TICKS_READ()-started)<TICKS_PER_SECOND*5u,"Initial stream load timed out");
        for(size_t i=0;i<s->world.slot_count;++i)assertf(s->slots[i].state!=DL_STREAM_FAILED,
            "Initial stream load failed region=%lu status=%s/%s read=%lu crc=%08lx expected=%08lx header=%02x%02x%02x%02x",
            (unsigned long)s->slots[i].region_id,dl_world_stream_status_name(s->slots[i].failure),
            dl_mission_status_name(s->slots[i].validation_failure),(unsigned long)s->slots[i].bytes_read,
            (unsigned long)(s->slots[i].crc32^UINT32_MAX),(unsigned long)s->slots[i].region.chunk_crc32,
            s->slots[i].buffer[0],s->slots[i].buffer[1],s->slots[i].buffer[2],s->slots[i].buffer[3]);
    }
#if DL_RUNTIME_TRACE
    s->candidate_job=UINT64_C(0x8000000000000000)|++s->candidate_sequence;
    TRACE_EVENT(s,"prepare_begin","startup",s->candidate_job,wanted);
#endif
    refresh(s,game,false);
#if DL_RUNTIME_TRACE
    s->trace_initial_cpu_ready=true;
#endif
    TRACE_EVENT(s,"cpu_ready","startup_assembled",s->candidate_job,wanted);TRACE_SNAPSHOT(s,game);
#if DL_RUNTIME_TRACE
    trace_cost(s,trace_start,s->candidate_job,0,true);dl_runtime_trace_flush(&s->trace,trace_sink,s);
#endif
}
void dl_stream_rom_service(DlGame *game,DlRenderStats *stats){
    stats->streaming=active!=NULL;
    if(!active)return;
#if DL_RUNTIME_TRACE
    DlProfileMark trace_start=dl_profile_mark();
#endif
    uint32_t service_start=TICKS_READ();
    StreamRom *s=active;s->service_stage=0;completion(s);
#if DL_RUNTIME_TRACE
    uint32_t trace_commit=0;
    uint64_t trace_job=s->preparing?s->candidate_job:0;
    dl_runtime_trace_time(&s->trace);
    uint32_t previous_wanted=s->wanted_regions;
#endif
    uint32_t wanted=wanted_mask(s,game);s->wanted_regions=wanted;
#if DL_RUNTIME_TRACE
    if(wanted!=previous_wanted){TRACE_EVENT(s,"demand",s->queued_use?"door_request":"movement",0,wanted);TRACE_SNAPSHOT(s,game);}
#endif
    /* A turn can choose a different approach while the previous candidate is
     * still being prepared. Discard that unpublished work immediately instead
     * of making the player wait for an obsolete doorway's full rebuild. */
    if(s->preparing&&(s->prepared_regions&~wanted)){
        TRACE_EVENT(s,"candidate_cancel","demand_reversed",s->candidate_job,s->prepared_regions);
        if(s->gpu_started)dl_render_cancel_scene();
        dl_stream_scene_prepare_cancel(&s->scene);
        s->preparing=s->assembling=s->gpu_started=s->gpu_ready=false;
        TRACE_SNAPSHOT(s,game);
    }
    if(s->preparing){
        if(s->assembling){
            s->service_stage=1;uint32_t start=TICKS_READ();bool ready=false;
            do{
                assertf(dl_stream_scene_prepare_step(&s->scene,1,&ready)==DL_STREAM_SCENE_OK,"Invalid incremental streamed working set");
            }while(!ready&&(uint32_t)(TICKS_READ()-start)<PREPARE_TICKS);
            if(ready){
                s->assembling=false;s->prepared_game.level=dl_stream_scene_pending(&s->scene);
                TRACE_EVENT(s,"cpu_ready","assembly_complete",s->candidate_job,s->prepared_regions);TRACE_SNAPSHOT(s,game);
            }
        }else if(!s->gpu_started){
            s->service_stage=2;
            dl_render_stage_scene(&s->prepared_game);s->gpu_started=true;
        }else if(!s->gpu_ready){
            s->service_stage=2;
            s->gpu_ready=dl_render_stage_step(&s->prepared_game,PREPARE_TICKS);
            if(s->gpu_ready){TRACE_EVENT(s,"gpu_ready","renderer_prepared",s->candidate_job,s->prepared_regions);TRACE_SNAPSHOT(s,game);}
        }else{
            s->service_stage=3;
            /* Active guards keep moving while this candidate prepares. A
             * changed visual membership must retry without publishing an
             * invisible active guard or a visible guard in an absent region. */
            if(!dl_stream_scene_pending_guards_match(&s->scene,game)){
                TRACE_EVENT(s,"candidate_cancel","guard_membership_changed",s->candidate_job,s->prepared_regions);
                dl_render_cancel_scene();dl_stream_scene_prepare_cancel(&s->scene);
                s->preparing=s->assembling=s->gpu_started=s->gpu_ready=false;
                s->prepared_regions=0;
            }else{
#if DL_RUNTIME_TRACE
            uint32_t commit_start=TICKS_READ();
#endif
            dl_stream_scene_publish(&s->scene);
            game->level=dl_stream_scene_level(&s->scene);
            assertf(dl_game_query_cache_ready(game->level),"Published collision index is incomplete");
            dl_render_commit_scene(game);
            s->view_regions=s->prepared_regions;s->preparing=false;s->gpu_started=s->gpu_ready=false;
#if DL_RUNTIME_TRACE
            trace_commit=TICKS_READ()-commit_start;
#endif
            TRACE_PUBLICATION(s,game,"candidate_ready");
            debugf("DL64 stream_query_cache ready=%d colliders=%d floors=%d\n",
                dl_game_query_cache_ready(game->level),game->level->collider_count,game->level->floor_count);
            debugf("DL64 stream_view resident_mask=%lu triangles=%d colliders=%d active_enemies=%u loads=%llu retired=%lu reserved=%lu allocated_slots=%lu adapter_bytes=%lu\n",
                (unsigned long)s->view_regions,s->scene.banks[s->scene.active_bank].triangle_count,
                game->level->collider_count,(unsigned)game->active_enemy_mask,
                (unsigned long long)s->world.stats.completed_loads,(unsigned long)s->retired,
                (unsigned long)s->world.stats.reserved_bytes,(unsigned long)s->world.stats.allocated_slot_bytes,
                (unsigned long)s->platform_allocated);
            }
        }
    }else{
        request_wanted(s,wanted);
        uint32_t resident=resident_mask(s);
        /* Prepare a removal before exposing its replacements. The GPU pool
         * contains at most the five-region union of current and candidate. */
        /* Publish departures promptly to release the GPU union. Additions
         * wait for the complete demand, avoiding an expensive rebuild for
         * each independently completed chunk of the same approach. */
        if(resident!=s->view_regions&&((s->view_regions&~resident)||(resident&wanted)==wanted)){
            unsigned count=0;for(uint32_t bits=resident|s->view_regions;bits;bits&=bits-1)++count;
            uint32_t candidate=count<=SLOT_COUNT?resident:resident&s->view_regions;
            assertf(candidate!=s->view_regions,"Cannot retire a bounded render union");
            s->service_stage=1;
            assertf(dl_stream_scene_prepare_begin_mask(&s->scene,&s->world,game,candidate)==DL_STREAM_SCENE_OK,"Invalid pending streamed working set");
            s->prepared_game=*game;
            s->prepared_regions=candidate;s->preparing=true;s->assembling=true;
#if DL_RUNTIME_TRACE
            s->candidate_job=UINT64_C(0x8000000000000000)|++s->candidate_sequence;trace_job=s->candidate_job;
#endif
            TRACE_EVENT(s,"prepare_begin","resident_set_changed",s->candidate_job,candidate);TRACE_SNAPSHOT(s,game);
        }
    }
    /* Borrowed resident chunks are immutable. Reads can progress in other
     * slots while their snapshot is assembled or packed; request/cancel stays
     * deferred until publication. Newly resident chunks join the next view. */
    if(s->service_stage!=3&&(uint32_t)(TICKS_READ()-service_start)<TICKS_PER_SECOND/250u){
        uint32_t start=TICKS_READ();size_t processed=0;
        for(unsigned attempt=0;attempt<2;++attempt){
            processed+=dl_world_stream_pump(&s->world,READ_SLICE*2u);
            if(pi_busy()||processed>=READ_SLICE||
                (uint32_t)(TICKS_READ()-start)>=TICKS_PER_SECOND/1000u)break;
        }
    }
    /* main.c primes music before this bounded service call. Refill here
     * would combine another mixer job with the stream CPU budget. */
    dl_game_set_active_enemies(game,dl_stream_scene_enemy_mask(&s->scene,game));
    stats->stream_current=dl_stream_rom_current(game);
    stats->stream_regions=0;for(size_t i=0;i<s->world.slot_count;++i)stats->stream_regions+=s->slots[i].state==DL_STREAM_RESIDENT;
    stats->stream_loading=(s->view_regions&wanted)!=wanted;
    stats->stream_reserved_bytes=(uint32_t)s->world.stats.reserved_bytes;
    stats->stream_allocated_bytes=(uint32_t)s->world.stats.allocated_slot_bytes;
    stats->stream_loads=(uint32_t)s->world.stats.completed_loads;stats->stream_retired=s->retired;
#if DL_RUNTIME_TRACE
    if(++s->trace_frames%120u==0)TRACE_SNAPSHOT(s,game);
    trace_cost(s,trace_start,trace_job,trace_commit,false);
#endif
}
void dl_stream_rom_filter_input(const DlGame *game,DlInput *input){
    if(!active)return;
    StreamRom *s=active;bool near_closed=false;int near_object=-1;float nearest=INFINITY;
#if DL_RUNTIME_TRACE
    DlInput requested=*input;
#endif
    uint32_t resident=s->view_regions;
    if(s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED&&dl_stream_scene_gate_waiting(&s->scene,game->player,resident)){
        input->forward=0;input->strafe=0;input->jump=false;
    }
    DlVec3 eye=dl_player_eye(game);
    for(int i=0;i<game->level->object_count;++i){
        const DlObjectDef *object=&game->level->objects[i];
        float x=eye.x-object->position.x,y=eye.y-object->position.y,z=eye.z-object->position.z;
        float distance=x*x+y*y+z*z;
        if(object->kind==DL_OBJECT_DOOR&&!dl_game_object_open(game,i)&&distance<=object->radius*object->radius&&distance<nearest)
            {near_closed=true;near_object=i;nearest=distance;}
    }
    uint32_t transition=0,needed=all_regions(s);
    if(s->scene.bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED){
        needed=near_object>=0?dl_stream_scene_door_target(&s->scene,s->policy_state.current_region,
            s->scene.bootstrap->stream->object_entity_ids[near_object],&transition):0;
        if(s->queued_use&&(s->queued_object!=near_object||!transition||transition!=s->queued_transition)){
            s->queued_use=false;s->queued_object=-1;s->queued_transition=0;
        }
    }
    if(near_closed&&(resident&needed)!=needed){
        if(input->use){s->queued_use=true;s->queued_object=near_object;s->queued_transition=transition;}input->use=false;
    }else if(s->queued_use){input->use=near_closed&&near_object==s->queued_object;s->queued_use=false;s->queued_object=-1;s->queued_transition=0;}
#if DL_RUNTIME_TRACE
    if(!s->trace.finished){
    uint32_t diagnostic_start=TICKS_READ();
    uint64_t recorder_before=s->trace.trace_ticks;
    /* Read-only intent and LOS probes; gameplay keeps its existing predicates. */
    bool suppressed=requested.forward!=input->forward||requested.strafe!=input->strafe||(requested.jump&&!input->jump);
    uint32_t physical=resident_mask(s);
    unsigned readiness_flags=(s->preparing?1u:0)|(s->assembling?2u:0)|(s->gpu_ready?4u:0);
    bool motion_changed=s->trace_motion_gate!=s->policy_state.pending_transition||
        s->trace_motion_physical!=physical||s->trace_motion_published!=s->view_regions||
        s->trace_motion_readiness!=readiness_flags||s->trace_motion_forward!=requested.forward||s->trace_motion_strafe!=requested.strafe;
    if((requested.forward||requested.strafe||requested.jump)&&(s->policy_state.pending_transition||suppressed)&&
        (suppressed||motion_changed||s->trace_frames-s->trace_motion_frame>=30u)){
        s->trace_motion_frame=s->trace_frames;s->trace_motion_gate=s->policy_state.pending_transition;
        s->trace_motion_physical=physical;s->trace_motion_published=s->view_regions;s->trace_motion_readiness=readiness_flags;
        s->trace_motion_forward=requested.forward;s->trace_motion_strafe=requested.strafe;
        DlRuntimeReadiness readiness={physical,s->view_regions,s->preparing?s->prepared_regions:0,s->preparing&&!s->assembling,s->gpu_ready};
        DlRuntimeMotionDiagnostic m;
        if(dl_runtime_diagnose_motion_intent(&s->scene,game,&requested,input,DL_TICK_SECONDS,readiness,&m)&&m.gate&&m.gate_distance<=1.f){
            DlRuntimeRecord r={.type=DL_RT_MOTION,.kind=dl_runtime_readiness_name(m.readiness),.reason=dl_runtime_motion_direction_name(m.direction)};
            r.data[0]=m.gate;r.data[1]=m.from_region;r.data[2]=m.to_region;r.data[3]=m.door_entity;r.data[4]=m.required_mask;
            r.data[5]=m.missing_physical;r.data[6]=m.missing_published;r.data[7]=m.gate_waiting;r.data[8]=m.horizontal_suppressed;r.data[9]=m.jump_suppressed;
            r.data[10]=readiness.cpu_ready;r.data[11]=readiness.gpu_ready;r.data[12]=readiness.pending_mask;
            r.position[0]=m.position.x;r.position[1]=m.position.y;r.position[2]=m.position.z;
            r.position[3]=m.requested_delta.x;r.position[4]=m.requested_delta.y;r.position[5]=m.requested_delta.z;
            dl_runtime_trace_push(&s->trace,r);
        }
    }
    if(requested.use){
        for(int object=0;object<game->level->object_count;++object){DlRuntimeInteractionDiagnostic interaction;
            const DlObjectDef *definition=&game->level->objects[object];
            DlVec3 origin=definition->zone?game->player:eye;
            float x=origin.x-definition->position.x,y=origin.y-definition->position.y,z=origin.z-definition->position.z;
            if(x*x+y*y+z*z>definition->radius*definition->radius)continue;
            if(!dl_runtime_diagnose_interaction(&s->scene,game,object,&interaction)||!interaction.in_range)continue;
            const DlGameQueryDiagnostic *q=interaction.interaction_los.clear?&interaction.ordinary_los:&interaction.interaction_los;
            const DlRuntimeQuerySource *source=interaction.interaction_los.clear?&interaction.ordinary_source:&interaction.interaction_source;
            DlRuntimeRecord r={.type=DL_RT_INTERACTION,.kind=dl_runtime_interaction_reason_name(interaction.reason),.reason=dl_runtime_source_name(source->kind)};
            r.data[0]=(uint32_t)object;r.data[1]=s->scene.bootstrap->stream->object_entity_ids[object];r.data[2]=interaction.eligible;r.data[3]=interaction.in_range;
            r.data[4]=interaction.ordinary_los.clear;r.data[5]=interaction.interaction_los.clear;r.data[6]=(uint32_t)q->collider_index;
            r.data[7]=source->entity;r.data[8]=source->region;r.data[9]=source->record;
            r.data[10]=source->gate;r.data[11]=(uint32_t)source->object_index;r.data[12]=q->query_cache_ready;r.data[13]=!input->use;
            r.data[14]=!interaction.interaction_los.clear;dl_runtime_trace_push(&s->trace,r);
        }
    }
    uint32_t diagnostic_ticks=TICKS_READ()-diagnostic_start;
    uint64_t recorder_ticks=s->trace.trace_ticks-recorder_before;
    if(diagnostic_ticks>recorder_ticks)s->trace.trace_ticks+=diagnostic_ticks-recorder_ticks;
    }
#endif
}
void dl_stream_rom_submitted(void){
    if(!active)return;
    uint64_t serial=dl_render_stream_submitted();
    for(size_t i=0;i<active->world.slot_count;++i){
        DlRegionSlot *slot=&active->slots[i];if(slot->state!=DL_STREAM_RESIDENT)continue;
        DlRegionHandle h={(uint32_t)i,slot->generation};const DlMissionChunk *chunk;
        assertf(dl_world_stream_borrow(&active->world,h,&chunk)==DL_STREAM_OK,"Cannot pin submitted region");
        assertf(dl_world_stream_submit(&active->world,h,serial,serial)==DL_STREAM_OK,"Invalid graphics serial");
        assertf(dl_world_stream_release(&active->world,h)==DL_STREAM_OK,"Cannot release submitted region");
    }
}
