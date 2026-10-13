#include "stream_rom.h"
#include "stream_scene.h"
#include <libdragon.h>
#include <malloc.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

enum { SLOT_COUNT=5, READ_SLICE=256, DMA_BYTES=272, MAX_DIRECTORY=65536, MAX_CHUNK=65536 };
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
} StreamRom;
static StreamRom *active;
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
    completion(active);
    if(active->io_pending)dma_wait();
    for(size_t i=0;i<active->world.slot_count;++i)free(active->slots[i].buffer);
    free(active->dma);free(active->directory);free(active);active=NULL;
}
bool dl_stream_rom_active(void){return active!=NULL;}
bool dl_stream_rom_music_ready(void){return !active||!active->io_pending||!pi_busy();}
const DlWorldStream *dl_stream_rom_world(void){return active?&active->world:NULL;}
uint32_t dl_stream_rom_retired(void){return active?active->retired:0;}
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
        uint32_t wanted=dl_stream_scene_policy_update(&s->scene,game->player,resident_mask(s),&s->policy_state);
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
    if(renderer_ready)dl_render_release_scene();
    completion(s);
    assertf(dl_stream_scene_refresh(&s->scene,&s->world,game)==DL_STREAM_SCENE_OK,"Invalid streamed working set");
    game->level=dl_stream_scene_level(&s->scene);
    dl_game_set_active_enemies(game,dl_stream_scene_enemy_mask(&s->scene,game));
    s->view_regions=resident_mask(s);
    /* Region DMA owns only staging, but renderer DFS asset loads also use PI.
     * Preserve the completed staging result for the next bounded pump. */
    if(s->io_pending&&pi_busy())dma_wait();
    if(renderer_ready)dl_render_prepare_scene(game);
    debugf("DL64 stream_view resident_mask=%lu triangles=%d colliders=%d active_enemies=%u loads=%llu retired=%lu reserved=%lu allocated_slots=%lu adapter_bytes=%lu\n",
        (unsigned long)s->view_regions,s->scene.banks[s->scene.active_bank].triangle_count,
        game->level->collider_count,(unsigned)game->active_enemy_mask,
        (unsigned long long)s->world.stats.completed_loads,(unsigned long)s->retired,
        (unsigned long)s->world.stats.reserved_bytes,(unsigned long)s->world.stats.allocated_slot_bytes,
        (unsigned long)s->platform_allocated);
}
void dl_stream_rom_start(DlGame *game,const DlLevel *bootstrap,int preset){
    assertf(!active&&bootstrap&&bootstrap->stream,"Invalid stream mission start");
    const DlStreamDefinition *definition=bootstrap->stream;
    /* Chunk slots grow only to the cooked worst reachable set; scene geometry
     * and collision banks retain the same capacities across larger missions. */
    assertf(definition->region_count&&definition->region_count<=DL_STREAM_SCENE_MAX_REGIONS&&
        (definition->policy==DL_STREAM_POLICY_AUTHORED||definition->region_count==2)&&definition->directory_bytes<=MAX_DIRECTORY&&
        definition->max_region_bytes<=MAX_CHUNK,"Streaming demo exceeds its bounded adapter contract");
    active=calloc(1,sizeof *active);assertf(active,"Cannot allocate stream scene adapter");
    StreamRom *s=active;s->queued_object=-1;
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
    assertf(dl_stream_scene_init(&s->scene,bootstrap,&s->mission)==DL_STREAM_SCENE_OK,"Invalid streaming bootstrap");
    assertf(dl_game_start(game,bootstrap,preset),"Invalid stream test start");
    uint32_t wanted=wanted_mask(s,game);s->wanted_regions=wanted;request_wanted(s,wanted);
    uint32_t started=TICKS_READ();
    while((resident_mask(s)&wanted)!=wanted){
        dl_world_stream_pump(&s->world,READ_SLICE);
        assertf((uint32_t)(TICKS_READ()-started)<TICKS_PER_SECOND*5u,"Initial stream load timed out");
        for(size_t i=0;i<s->world.slot_count;++i)assertf(s->slots[i].state!=DL_STREAM_FAILED,
            "Initial stream load failed region=%lu status=%s/%s read=%lu crc=%08lx expected=%08lx header=%02x%02x%02x%02x",
            (unsigned long)s->slots[i].region_id,dl_world_stream_status_name(s->slots[i].failure),
            dl_mission_status_name(s->slots[i].validation_failure),(unsigned long)s->slots[i].bytes_read,
            (unsigned long)(s->slots[i].crc32^UINT32_MAX),(unsigned long)s->slots[i].region.chunk_crc32,
            s->slots[i].buffer[0],s->slots[i].buffer[1],s->slots[i].buffer[2],s->slots[i].buffer[3]);
    }
    refresh(s,game,false);
}
void dl_stream_rom_service(DlGame *game,DlRenderStats *stats){
    stats->streaming=active!=NULL;
    if(!active)return;
    StreamRom *s=active;completion(s);uint32_t wanted=wanted_mask(s,game);s->wanted_regions=wanted;
    request_wanted(s,wanted);dl_world_stream_pump(&s->world,READ_SLICE);
    uint32_t resident=resident_mask(s);
    if(resident!=s->view_regions)refresh(s,game,true);
    dl_game_set_active_enemies(game,dl_stream_scene_enemy_mask(&s->scene,game));
    stats->stream_current=dl_stream_rom_current(game);
    stats->stream_regions=0;for(size_t i=0;i<s->world.slot_count;++i)stats->stream_regions+=s->slots[i].state==DL_STREAM_RESIDENT;
    stats->stream_loading=(resident&wanted)!=wanted;
    stats->stream_reserved_bytes=(uint32_t)s->world.stats.reserved_bytes;
    stats->stream_allocated_bytes=(uint32_t)s->world.stats.allocated_slot_bytes;
    stats->stream_loads=(uint32_t)s->world.stats.completed_loads;stats->stream_retired=s->retired;
}
void dl_stream_rom_filter_input(const DlGame *game,DlInput *input){
    if(!active)return;
    StreamRom *s=active;bool near_closed=false;int near_object=-1;float nearest=INFINITY;
    uint32_t resident=resident_mask(s);
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
