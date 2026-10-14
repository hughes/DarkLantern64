/* Host-only fixture driver. Decode the real cooked package through the shared
 * coordinator before invoking the same policy helper used by the renderer. */
#include "stream_coordinator.h"
#include "lighting_policy.h"
#include "demo_level.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static DlStreamCoordinator coordinator;
static DlGame game;
static uint8_t *bytes;
static size_t length;
static size_t read_bytes(void *context,uint32_t offset,void *out,size_t wanted){
    (void)context;if(offset>length||wanted>length-offset)return SIZE_MAX;
    memcpy(out,bytes+offset,wanted);return wanted;
}
static void report(const char *build,uint64_t number,bool changed){
    DlLightingSettings settings={.bake_enabled=true,.independent_doors=dl_lighting_independent_doors(game.level)};
    DlLightingPolicyReport rows;dl_lighting_policy_report(game.level,settings,changed,&rows);
    DlLightingPolicyIdentity identity={.report=number,.build=build,.source=dl_demo_level.renderer_source_sha256,
        .package=dl_demo_level.stream->package_sha256,.standalone="unconfigured",.revision=coordinator.scene.revision,
        .published=coordinator.view_regions,.door_revision=dl_game_lighting_revision(&game),
        .doors=settings.independent_doors,.changed=changed};
    char line[512];clock_t started=clock();
    size_t count=dl_lighting_policy_header_text(line,sizeof line,&identity,&rows);if(!count)exit(3);fwrite(line,1,count,stdout);
    for(int mode=0;mode<DL_LIGHT_MODE_COUNT;++mode)for(int reason=0;reason<DL_LIGHT_REASON_COUNT;++reason)
        if(rows.cases[mode][reason].models){count=dl_lighting_policy_case_text(line,sizeof line,number,
            (DlLightingMode)mode,(DlLightingReason)reason,rows.cases[mode][reason]);if(!count)exit(3);fwrite(line,1,count,stdout);}
    fflush(stdout);count=dl_lighting_policy_cost_text(line,sizeof line,number,(uint32_t)(clock()-started),CLOCKS_PER_SEC);
    if(!count)exit(3);
    fwrite(line,1,count,stdout);
}
int main(int argc,char **argv){
    if(argc!=3)return 2;
    FILE *file=fopen(argv[1],"rb");if(!file)return 2;
    if(fseek(file,0,SEEK_END)!=0)return 2;
    long size=ftell(file);if(size<=0||fseek(file,0,SEEK_SET)!=0)return 2;
    length=(size_t)size;bytes=malloc(length);if(!bytes||fread(bytes,1,length,file)!=length)return 2;fclose(file);
    const DlStreamDefinition *definition=dl_demo_level.stream;DlMission mission;
    if(dl_mission_open(bytes,definition->directory_bytes,(uint32_t)length,&mission)!=DL_MISSION_OK)return 3;
    DlRegionSlot slots[5]={0};
    for(unsigned i=0;i<definition->max_resident_regions;++i){slots[i].capacity=definition->max_region_bytes;
        slots[i].buffer=malloc(slots[i].capacity);if(!slots[i].buffer)return 2;}
    DlStreamCoordinatorCallbacks callbacks={.read=read_bytes};
    if(dl_stream_coordinator_init(&coordinator,&dl_demo_level,&mission,slots,definition->max_resident_regions,
        &callbacks,NULL,NULL)!=DL_COORDINATOR_OK)return 3;
    if(dl_stream_coordinator_start(&coordinator,&game,-1)!=DL_COORDINATOR_OK)return 3;
    bool ready=false;unsigned steps=0;
    while(!ready&&++steps<65536)if(dl_stream_coordinator_initial_step(&coordinator,&game,4096,&ready)!=DL_COORDINATOR_OK)return 3;
    if(!ready||dl_stream_coordinator_initial_publish(&coordinator,&game)!=DL_COORDINATOR_OK)return 3;
    dl_stream_coordinator_initial_renderer_ready(&coordinator,&game);report(argv[2],1,false);
    unsigned doors=dl_lighting_independent_doors(game.level);
    for(unsigned state=0;state<(1u<<doors);++state){game.open_objects=0;unsigned door=0;
        for(int object=0;object<game.level->object_count;++object)if(game.level->objects[object].kind==DL_OBJECT_DOOR){
            if(state&(1u<<door))game.open_objects|=UINT64_C(1)<<object;
            ++door;}
        game.door_open=game.open_objects!=0;report(argv[2],state+2,true);}
    dl_stream_coordinator_cancel(&coordinator);
    for(unsigned i=0;i<definition->max_resident_regions;++i)free(slots[i].buffer);
    free(bytes);return 0;
}
