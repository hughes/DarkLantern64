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
#if DL_RUNTIME_TRACE
#include "runtime_trace_text.h"
#include <unistd.h>
#endif

enum { SLOT_COUNT=5, READ_SLICE=8192, DMA_BYTES=8208, MAX_DIRECTORY=65536, MAX_CHUNK=65536 };
#define PREPARE_TICKS (TICKS_PER_SECOND*11u/2000u)
typedef struct {
    DlMission mission;
    DlStreamCoordinator coordinator;
    DlRegionSlot slots[SLOT_COUNT];
    uint8_t *directory, *dma;
    uint32_t rom, file_bytes, io_offset, io_bytes, io_prefix;
    bool io_pending, io_abandoned;
    size_t platform_allocated;
#if DL_RUNTIME_TRACE
    DlRuntimeTrace trace;
    uint64_t published_jobs[SLOT_COUNT];
    uint32_t trace_frames, trace_commit_start, trace_commit;
    DlRuntimeRecord trace_cost_group;
    uint32_t trace_motion_frame, trace_motion_gate, trace_motion_physical, trace_motion_published;
    float trace_motion_forward, trace_motion_strafe;
    unsigned trace_motion_readiness;
#endif
} StreamRom;
static StreamRom *active;
void dl_stream_rom_lighting_view(const DlGame *game,uint32_t *revision,uint32_t *mask){
    if(revision)*revision=0;
    if(mask)*mask=0;
    if(!active||!game||game->level!=dl_stream_scene_level(&active->coordinator.scene))return;
    const DlStreamScene *scene=&active->coordinator.scene;const DlStreamSceneBank *bank=&scene->banks[scene->active_bank];
    if(revision)*revision=scene->revision;
    if(mask)for(uint32_t i=0;i<bank->resident_count;++i)*mask|=UINT32_C(1)<<(bank->resident_ids[i]-1);
}
#if DL_RUNTIME_TRACE
static uint32_t resident_mask(const StreamRom *s);
#ifndef DL_RUNTIME_TRACE_BUILD_ID
#define DL_RUNTIME_TRACE_BUILD_ID "unidentified"
#endif
static uint32_t trace_session;
static uint32_t trace_clock(void *context){(void)context;return TICKS_READ();}
static void trace_sink(void *context,const DlRuntimeRecord *r){
    StreamRom *s=context;
    char row[512];size_t bytes=dl_runtime_trace_text(row,sizeof row,s->trace.session,r);
    if(bytes){
        /* Match the SDK's whole-word tail reads without accessing past this
         * bounded scratch buffer. The record and timestamps are unchanged. */
        for(size_t i=bytes;i<((bytes+3u)&~(size_t)3);++i)row[i]=0;
        ssize_t written=write(STDERR_FILENO,row,bytes);(void)written;
        return;
    }
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
    r.data[0]=dl_stream_rom_current(game);r.data[1]=s->coordinator.wanted_regions;r.data[2]=resident_mask(s);
    r.data[3]=s->coordinator.initial_ready?s->coordinator.view_regions:0;
    r.data[4]=s->coordinator.initial_ready?(s->coordinator.preparing?s->coordinator.prepared_regions:0):s->coordinator.wanted_regions;
    r.data[5]=s->coordinator.policy_state.pending_transition;r.data[6]=!s->coordinator.initial_ready||s->coordinator.preparing;
    r.data[7]=s->coordinator.initial_ready?(s->coordinator.preparing&&!s->coordinator.assembling):s->coordinator.initial_cpu_ready;
    r.data[8]=s->coordinator.gpu_ready;r.data[9]=s->coordinator.scene.revision;r.data[10]=(uint32_t)s->coordinator.world.slot_count;
    r.wide[0]=s->coordinator.world.completed_rsp;r.wide[1]=s->coordinator.world.completed_rdp;
    r.position[0]=game->player.x;r.position[1]=game->player.y;r.position[2]=game->player.z;
    uint64_t snapshot=dl_runtime_trace_push(&s->trace,r);
    for(size_t i=0;i<s->coordinator.world.slot_count;++i){const DlRegionSlot *slot=&s->slots[i];
        r=(DlRuntimeRecord){.type=DL_RT_SLOT,.job=slot->state==DL_STREAM_FREE?0:dl_runtime_region_job((uint32_t)i,slot->generation)};
        r.data[0]=(uint32_t)i;r.data[1]=slot->generation;r.data[2]=slot->region_id;r.data[3]=slot->state;
        r.data[4]=(uint32_t)slot->bytes_read;r.data[5]=slot->region.chunk_bytes;r.data[6]=slot->cpu_pins;
        r.wide[0]=slot->last_rsp;r.wide[1]=slot->last_rdp;r.wide[2]=snapshot;dl_runtime_trace_push(&s->trace,r);
    }
}
static void trace_publication(StreamRom *s,const DlGame *game,const char *reason){
    trace_event(s,"publish",reason,s->coordinator.candidate_job,s->coordinator.view_regions);
    for(size_t i=0;i<s->coordinator.world.slot_count;++i){const DlRegionSlot *slot=&s->slots[i];
        if(slot->state!=DL_STREAM_RESIDENT||!(s->coordinator.view_regions&(UINT32_C(1)<<(slot->region_id-1))))continue;
        uint64_t job=dl_runtime_region_job((uint32_t)i,slot->generation);
        if(s->published_jobs[i]!=job){s->published_jobs[i]=job;
            dl_runtime_trace_event(&s->trace,"publish",reason,job,slot->region_id,(uint32_t)i,slot->generation,(uint32_t)slot->bytes_read,slot->region.chunk_bytes,s->coordinator.view_regions);}
    }
    trace_snapshot(s,game);
}
static void trace_cost_flush(StreamRom *s){
    if(s->trace_cost_group.data[2]){dl_runtime_trace_push(&s->trace,s->trace_cost_group);
        s->trace_cost_group=(DlRuntimeRecord){0};}
}
static void trace_cost(StreamRom *s,DlProfileMark start,uint64_t job,uint32_t commit,bool startup){
    DlProfileMark end=dl_profile_mark();uint32_t elapsed=end.ticks-start.ticks,audio=end.audio_ticks-start.audio_ticks;
    DlRuntimeRecord r={.type=DL_RT_COST,.job=job};r.data[0]=s->coordinator.service_stage;r.data[1]=startup?elapsed:0;r.data[2]=1;r.data[3]=elapsed>=audio?elapsed-audio:0;
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
    if(!active)return;
#if DL_RUNTIME_TRACE
    StreamRom *s=active;
    DlRuntimeRecord r={.type=DL_RT_COST,.job=s->coordinator.candidate_job};r.data[0]=2;r.data[1]=elapsed;r.data[2]=1;r.data[3]=elapsed>=audio?elapsed-audio:0;
    r.wide[0]=elapsed;r.wide[1]=elapsed>=audio?elapsed-audio:0;r.wide[2]=audio;
    dl_runtime_trace_push(&s->trace,r);
#else
    (void)elapsed;(void)audio;
#endif
    dl_stream_coordinator_initial_renderer_ready(&active->coordinator,game);
#if DL_RUNTIME_TRACE
    dl_runtime_trace_flush(&active->trace,trace_sink,active);
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
static void abandon_read(void *context,const DlRegionSlot *slot){
    StreamRom *s=context;
    if(s->io_pending&&s->io_offset>=slot->region.chunk_offset&&
        s->io_offset-slot->region.chunk_offset<slot->region.chunk_bytes)s->io_abandoned=true;
}
static uint32_t platform_clock(void *context){(void)context;return TICKS_READ();}
static bool platform_io_busy(void *context){(void)context;return pi_busy();}
static void platform_completed(void *context,uint64_t *rsp,uint64_t *rdp){
    (void)context;dl_render_stream_completed(rsp,rdp);
}
static void platform_renderer_begin(void *context,const DlGame *game,uint64_t job){
    (void)context;(void)job;dl_render_stage_scene(game);
}
static bool platform_renderer_step(void *context,const DlGame *game,uint32_t budget,uint64_t job){
    (void)context;(void)job;return dl_render_stage_step(game,budget);
}
static void platform_renderer_cancel(void *context,uint64_t job){
    (void)context;(void)job;dl_render_cancel_scene();
}
static void platform_renderer_commit(void *context,const DlGame *game,uint64_t job){
    (void)job;dl_render_commit_scene(game);
#if DL_RUNTIME_TRACE
    StreamRom *s=context;s->trace_commit=TICKS_READ()-s->trace_commit_start;
#else
    (void)context;
#endif
}
static void log_view(StreamRom *s,const DlGame *game){
    const DlStreamCoordinator *c=&s->coordinator;
    debugf("DL64 stream_view resident_mask=%lu triangles=%d colliders=%d active_enemies=%u loads=%llu retired=%lu reserved=%lu allocated_slots=%lu adapter_bytes=%lu\n",
        (unsigned long)c->view_regions,c->scene.banks[c->scene.active_bank].triangle_count,
        game->level->collider_count,(unsigned)game->active_enemy_mask,
        (unsigned long long)c->world.stats.completed_loads,(unsigned long)c->retired,
        (unsigned long)c->world.stats.reserved_bytes,(unsigned long)c->world.stats.allocated_slot_bytes,
        (unsigned long)s->platform_allocated);
}
static void platform_observe(void *context,const DlStreamCoordinator *c,const DlGame *game,
    const DlStreamCoordinatorEvent *event){
    StreamRom *s=context;
    if(!strcmp(event->kind,"retire")){
        debugf("DL64 stream_retire region=%lu total=%lu\n",(unsigned long)event->region,(unsigned long)c->retired);return;
    }
    if(!strcmp(event->kind,"publish_begin")){
#if DL_RUNTIME_TRACE
        s->trace_commit_start=TICKS_READ();
#endif
        return;
    }
    if(!strcmp(event->kind,"publish")){
        TRACE_PUBLICATION(s,game,event->reason);
        if(!strcmp(event->reason,"candidate_ready")){
            debugf("DL64 stream_query_cache ready=%d colliders=%d floors=%d\n",
                dl_game_query_cache_ready(game->level),game->level->collider_count,game->level->floor_count);
            log_view(s,game);
        }
        return;
    }
    TRACE_EVENT(s,event->kind,event->reason,event->job,event->mask);
    if(game)TRACE_SNAPSHOT(s,game);
}
static void check_coordinator(StreamRom *s,DlStreamCoordinatorStatus status){
    assertf(status==DL_COORDINATOR_OK,"Streaming coordinator failed: %s world=%s scene=%s",
        dl_stream_coordinator_status_name(status),dl_world_stream_status_name(s->coordinator.world_failure),
        dl_stream_scene_status_name(s->coordinator.scene_failure));
}
static void completion(StreamRom *s){
    uint64_t rsp,rdp;platform_completed(s,&rsp,&rdp);
    check_coordinator(s,dl_stream_coordinator_complete(&s->coordinator,rsp,rdp));
}
void dl_stream_rom_close(void){
    if(!active)return;
    /* Renderer shutdown reaches its graphics ownership boundary first. PI can
     * still own independent staging storage until the outstanding read ends. */
    dl_stream_coordinator_cancel(&active->coordinator);completion(active);
    if(active->io_pending)dma_wait();
    dl_stream_rom_trace_finish();
    for(size_t i=0;i<active->coordinator.world.slot_count;++i)free(active->slots[i].buffer);
    free(active->dma);free(active->directory);free(active);active=NULL;
}
bool dl_stream_rom_active(void){return active!=NULL;}
bool dl_stream_rom_music_ready(void){return !active||!active->io_pending||!pi_busy();}
const DlWorldStream *dl_stream_rom_world(void){return active?&active->coordinator.world:NULL;}
uint32_t dl_stream_rom_retired(void){return active?active->coordinator.retired:0;}
uint32_t dl_stream_rom_service_stage(void){return active?active->coordinator.service_stage:0;}
uint32_t dl_stream_rom_view_mask(void){return active?active->coordinator.view_regions:0;}
bool dl_stream_rom_settled(void){return dl_stream_coordinator_settled(active?&active->coordinator:NULL);}
uint32_t dl_stream_rom_current(const DlGame *game){return dl_stream_coordinator_current(active?&active->coordinator:NULL,game);}
void dl_stream_rom_snapshot(const DlGame *game,DlStreamCoordinatorSnapshot *out){
    dl_stream_coordinator_snapshot(active?&active->coordinator:NULL,game,out);
}
#if DL_RUNTIME_TRACE
static uint32_t resident_mask(const StreamRom *s){return dl_stream_coordinator_resident_mask(&s->coordinator);}
#endif
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
    StreamRom *s=active;
#if DL_RUNTIME_TRACE
    dl_runtime_trace_init(&s->trace,++trace_session,trace_clock,NULL);
    debugf("DL64 rt_begin version=1 session=%lu build=%s package=%s ticks_per_second=%lu slots=%lu\n",
        (unsigned long)s->trace.session,DL_RUNTIME_TRACE_BUILD_ID,definition->package_sha256?definition->package_sha256:"unidentified",
        (unsigned long)TICKS_PER_SECOND,(unsigned long)(definition->max_resident_regions?definition->max_resident_regions:2));
#endif
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
    DlStreamCoordinatorCallbacks callbacks={
        .read=read_region,.abandon=abandon_read,.clock=platform_clock,.io_busy=platform_io_busy,
        .renderer_begin=platform_renderer_begin,.renderer_step=platform_renderer_step,
        .renderer_cancel=platform_renderer_cancel,.renderer_commit=platform_renderer_commit,
        .graphics_completed=platform_completed,.observe=platform_observe};
    DlStreamCoordinatorConfig config={READ_SLICE,READ_SLICE*2u,SIZE_MAX,PREPARE_TICKS,
        TICKS_PER_SECOND/250u,TICKS_PER_SECOND/1000u,2};
    check_coordinator(s,dl_stream_coordinator_init(&s->coordinator,bootstrap,&s->mission,
        s->slots,slot_count,&callbacks,&config,s));
#if DL_RUNTIME_TRACE
    s->coordinator.world.trace=&s->trace;
#endif
    check_coordinator(s,dl_stream_coordinator_start(&s->coordinator,game,preset));
    uint32_t started=TICKS_READ();bool ready=false;
    while(!ready){
        check_coordinator(s,dl_stream_coordinator_initial_step(&s->coordinator,game,READ_SLICE,&ready));
#if DL_RUNTIME_TRACE
        if(s->trace.count>=DL_RUNTIME_TRACE_CAPACITY/2)dl_runtime_trace_flush(&s->trace,trace_sink,s);
#endif
        assertf((uint32_t)(TICKS_READ()-started)<TICKS_PER_SECOND*5u,"Initial stream load timed out");
    }
    completion(s);
    check_coordinator(s,dl_stream_coordinator_initial_publish(&s->coordinator,game));
    /* Renderer DFS asset reads also use PI; preserve a finished staging result
     * for the next bounded pump before preparing those platform resources. */
    if(s->io_pending&&pi_busy())dma_wait();
    log_view(s,game);
#if DL_RUNTIME_TRACE
    trace_cost(s,trace_start,s->coordinator.candidate_job,0,true);dl_runtime_trace_flush(&s->trace,trace_sink,s);
#endif
}
void dl_stream_rom_service(DlGame *game,DlRenderStats *stats){
    stats->streaming=active!=NULL;if(!active)return;
    StreamRom *s=active;
#if DL_RUNTIME_TRACE
    DlProfileMark trace_start=dl_profile_mark();
    uint64_t trace_job=s->coordinator.preparing?s->coordinator.candidate_job:0;
    s->trace_commit=0;dl_runtime_trace_time(&s->trace);
#endif
    check_coordinator(s,dl_stream_coordinator_service(&s->coordinator,game));
    const DlStreamCoordinator *c=&s->coordinator;
    stats->stream_current=dl_stream_coordinator_current(c,game);
    stats->stream_regions=0;for(size_t i=0;i<c->world.slot_count;++i)stats->stream_regions+=s->slots[i].state==DL_STREAM_RESIDENT;
    stats->stream_loading=(c->view_regions&c->wanted_regions)!=c->wanted_regions;
    stats->stream_reserved_bytes=(uint32_t)c->world.stats.reserved_bytes;
    stats->stream_allocated_bytes=(uint32_t)c->world.stats.allocated_slot_bytes;
    stats->stream_loads=(uint32_t)c->world.stats.completed_loads;stats->stream_retired=c->retired;
#if DL_RUNTIME_TRACE
    if(c->preparing)trace_job=c->candidate_job;
    if(++s->trace_frames%120u==0)TRACE_SNAPSHOT(s,game);
    trace_cost(s,trace_start,trace_job,s->trace_commit,false);
#endif
}
void dl_stream_rom_filter_input(const DlGame *game,DlInput *input){
    if(!active)return;
    StreamRom *s=active;
#if DL_RUNTIME_TRACE
    DlInput requested=*input;DlVec3 eye=dl_player_eye(game);
#endif
    dl_stream_coordinator_filter_input(&s->coordinator,game,input);
#if DL_RUNTIME_TRACE
    if(!s->trace.finished){
    uint32_t diagnostic_start=TICKS_READ();
    uint64_t recorder_before=s->trace.trace_ticks;
    /* Read-only intent and LOS probes; gameplay keeps its existing predicates. */
    bool suppressed=requested.forward!=input->forward||requested.strafe!=input->strafe||(requested.jump&&!input->jump);
    uint32_t physical=resident_mask(s);
    unsigned readiness_flags=(s->coordinator.preparing?1u:0)|(s->coordinator.assembling?2u:0)|(s->coordinator.gpu_ready?4u:0);
    bool motion_changed=s->trace_motion_gate!=s->coordinator.policy_state.pending_transition||
        s->trace_motion_physical!=physical||s->trace_motion_published!=s->coordinator.view_regions||
        s->trace_motion_readiness!=readiness_flags||s->trace_motion_forward!=requested.forward||s->trace_motion_strafe!=requested.strafe;
    if((requested.forward||requested.strafe||requested.jump)&&(s->coordinator.policy_state.pending_transition||suppressed)&&
        (suppressed||motion_changed||s->trace_frames-s->trace_motion_frame>=30u)){
        s->trace_motion_frame=s->trace_frames;s->trace_motion_gate=s->coordinator.policy_state.pending_transition;
        s->trace_motion_physical=physical;s->trace_motion_published=s->coordinator.view_regions;s->trace_motion_readiness=readiness_flags;
        s->trace_motion_forward=requested.forward;s->trace_motion_strafe=requested.strafe;
        DlRuntimeReadiness readiness={physical,s->coordinator.view_regions,s->coordinator.preparing?s->coordinator.prepared_regions:0,s->coordinator.preparing&&!s->coordinator.assembling,s->coordinator.gpu_ready};
        DlRuntimeMotionDiagnostic m;
        if(dl_runtime_diagnose_motion_intent(&s->coordinator.scene,game,&requested,input,DL_TICK_SECONDS,readiness,&m)&&m.gate&&m.gate_distance<=1.f){
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
            if(!dl_runtime_diagnose_interaction(&s->coordinator.scene,game,object,&interaction)||!interaction.in_range)continue;
            const DlGameQueryDiagnostic *q=interaction.interaction_los.clear?&interaction.ordinary_los:&interaction.interaction_los;
            const DlRuntimeQuerySource *source=interaction.interaction_los.clear?&interaction.ordinary_source:&interaction.interaction_source;
            DlRuntimeRecord r={.type=DL_RT_INTERACTION,.kind=dl_runtime_interaction_reason_name(interaction.reason),.reason=dl_runtime_source_name(source->kind)};
            r.data[0]=(uint32_t)object;r.data[1]=s->coordinator.scene.bootstrap->stream->object_entity_ids[object];r.data[2]=interaction.eligible;r.data[3]=interaction.in_range;
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
    check_coordinator(active,dl_stream_coordinator_submitted(&active->coordinator,serial,serial));
}
