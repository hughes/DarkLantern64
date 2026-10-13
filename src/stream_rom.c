#include "stream_rom.h"
#include "stream_scene.h"
#include <libdragon.h>
#include <malloc.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

enum { SLOT_COUNT=2, READ_SLICE=256, DMA_BYTES=272, MAX_DIRECTORY=65536, MAX_CHUNK=65536 };
typedef struct {
    DlMission mission;
    DlWorldStream world;
    DlStreamScene scene;
    DlRegionSlot slots[SLOT_COUNT];
    uint8_t *directory, *dma;
    uint32_t rom, file_bytes, io_offset, io_bytes, io_prefix;
    bool io_pending, io_abandoned, queued_use;
    uint32_t view_regions, retired;
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
    for(int i=0;i<SLOT_COUNT;++i)free(active->slots[i].buffer);
    free(active->dma);free(active->directory);free(active);active=NULL;
}
bool dl_stream_rom_active(void){return active!=NULL;}
bool dl_stream_rom_music_ready(void){return !active||!active->io_pending||!pi_busy();}
const DlWorldStream *dl_stream_rom_world(void){return active?&active->world:NULL;}
uint32_t dl_stream_rom_retired(void){return active?active->retired:0;}
uint32_t dl_stream_rom_current(const DlGame *g){
    return active?dl_stream_scene_region_at(&active->scene,g->player):0;
}
static uint32_t resident_mask(const StreamRom *s){
    uint32_t mask=0;
    for(int i=0;i<SLOT_COUNT;++i)if(s->slots[i].state==DL_STREAM_RESIDENT)mask|=UINT32_C(1)<<(s->slots[i].region_id-1);
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
    uint32_t current=dl_stream_scene_region_at(&s->scene,game->player);
    assertf(current,"Player outside streaming probe bounds");
    uint32_t wanted=UINT32_C(1)<<(current-1);
    bool open=doors_open(game);
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
    for(int i=0;i<SLOT_COUNT;++i){
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
    /* The current console demonstration is deliberately a two-region proof,
     * with an authored opaque gate as its conservative visibility policy. */
    assertf(definition->region_count==2&&definition->directory_bytes<=MAX_DIRECTORY&&
        definition->max_region_bytes<=MAX_CHUNK,"Streaming demo exceeds its bounded adapter contract");
    active=calloc(1,sizeof *active);assertf(active,"Cannot allocate stream scene adapter");
    StreamRom *s=active;
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
    for(int i=0;i<SLOT_COUNT;++i){s->slots[i].buffer=memalign(16,capacity);s->slots[i].capacity=capacity;assertf(s->slots[i].buffer,"Cannot allocate region slot");}
    s->platform_allocated=sizeof *s+definition->directory_bytes+DMA_BYTES+SLOT_COUNT*capacity;
    assertf(dl_world_stream_init(&s->world,&s->mission,s->slots,SLOT_COUNT,read_region,s)==DL_STREAM_OK,"Cannot initialize streaming pool");
    assertf(dl_stream_scene_init(&s->scene,bootstrap,&s->mission)==DL_STREAM_SCENE_OK,"Invalid streaming bootstrap");
    assertf(dl_game_start(game,bootstrap,preset),"Invalid stream test start");
    uint32_t wanted=wanted_mask(s,game);request_wanted(s,wanted);
    uint32_t started=TICKS_READ();
    while((resident_mask(s)&wanted)!=wanted){
        dl_world_stream_pump(&s->world,READ_SLICE);
        assertf((uint32_t)(TICKS_READ()-started)<TICKS_PER_SECOND*5u,"Initial stream load timed out");
        for(int i=0;i<SLOT_COUNT;++i)assertf(s->slots[i].state!=DL_STREAM_FAILED,
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
    StreamRom *s=active;completion(s);uint32_t wanted=wanted_mask(s,game);
    request_wanted(s,wanted);dl_world_stream_pump(&s->world,READ_SLICE);
    uint32_t resident=resident_mask(s);
    if(resident!=s->view_regions)refresh(s,game,true);
    dl_game_set_active_enemies(game,dl_stream_scene_enemy_mask(&s->scene,game));
    stats->stream_current=dl_stream_scene_region_at(&s->scene,game->player);
    stats->stream_regions=0;for(int i=0;i<SLOT_COUNT;++i)stats->stream_regions+=s->slots[i].state==DL_STREAM_RESIDENT;
    stats->stream_loading=(resident&wanted)!=wanted;
    stats->stream_reserved_bytes=(uint32_t)s->world.stats.reserved_bytes;
    stats->stream_allocated_bytes=(uint32_t)s->world.stats.allocated_slot_bytes;
    stats->stream_loads=(uint32_t)s->world.stats.completed_loads;stats->stream_retired=s->retired;
}
void dl_stream_rom_filter_input(const DlGame *game,DlInput *input){
    if(!active)return;
    StreamRom *s=active;bool near_closed=false;
    DlVec3 eye=dl_player_eye(game);
    for(int i=0;i<game->level->object_count;++i){
        const DlObjectDef *object=&game->level->objects[i];
        float x=eye.x-object->position.x,y=eye.y-object->position.y,z=eye.z-object->position.z;
        if(object->kind==DL_OBJECT_DOOR&&!dl_game_object_open(game,i)&&x*x+y*y+z*z<=object->radius*object->radius)
            near_closed=true;
    }
    uint32_t all=(UINT32_C(1)<<s->mission.region_count)-1u;
    if(near_closed&&(resident_mask(s)&all)!=all){
        s->queued_use|=input->use;input->use=false;
    }else if(s->queued_use){input->use=near_closed;s->queued_use=false;}
}
void dl_stream_rom_submitted(void){
    if(!active)return;
    uint64_t serial=dl_render_stream_submitted();
    for(int i=0;i<SLOT_COUNT;++i){
        DlRegionSlot *slot=&active->slots[i];if(slot->state!=DL_STREAM_RESIDENT)continue;
        DlRegionHandle h={(uint32_t)i,slot->generation};const DlMissionChunk *chunk;
        assertf(dl_world_stream_borrow(&active->world,h,&chunk)==DL_STREAM_OK,"Cannot pin submitted region");
        assertf(dl_world_stream_submit(&active->world,h,serial,serial)==DL_STREAM_OK,"Invalid graphics serial");
        assertf(dl_world_stream_release(&active->world,h)==DL_STREAM_OK,"Cannot release submitted region");
    }
}
