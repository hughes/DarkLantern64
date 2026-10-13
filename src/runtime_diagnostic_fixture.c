#include "runtime_diagnostic_fixture.h"
#include <string.h>

enum { FIRST=240,SECOND=400 };
static const DlVec3 vertices[]={{0,-1,-.8f},{0,1,-.8f},{0,1,.8f}};
static const uint16_t indices[]={0,1,2};
static const DlMesh meshes[]={{.vertices=vertices,.vertex_count=3,.indices=indices,.index_count=3}};
static const DlModelInstance models[]={
    {.id="fixture-door",.mesh=0,.position={0,1,0},.scale={1,1,1},.role=DL_MODEL_DOOR,.texture=-1,.enemy_index=-1},
    {.id="fixture-loot",.mesh=0,.position={.7f,1,0},.scale={.2f,.2f,.2f},.role=DL_MODEL_LOOT,.texture=-1,.enemy_index=-1}
};
static const DlObjectDef objects[]={
    {.id="fixture-door",.kind=DL_OBJECT_DOOR,.position={0,1,0},.radius=1.5f},
    {.id="fixture-loot",.kind=DL_OBJECT_LOOT,.position={.7f,1,0},.radius=1.5f,.value=25}
};
static const uint32_t links[]={1,2};
static const DlStreamPolicyRegion policy[]={
    {.activation_minimum={-4,-1,-2},.activation_maximum={0,3,2},.working_mask=1},
    {.activation_minimum={0,-1,-2},.activation_maximum={4,3,2},.working_mask=2}
};
static const DlStreamTransition gates[]={
    {.from_region=1,.to_region=2,.minimum={-.05f,0,-.8f},.maximum={.05f,2,.8f},.door_entity_id=1},
    {.from_region=2,.to_region=1,.minimum={-.05f,0,-.8f},.maximum={.05f,2,.8f},.door_entity_id=1}
};
static const DlStreamDefinition definition={
    .package_path="memory:synthetic-doorway",.directory_bytes=DL_RUNTIME_DOORWAY_DIRECTORY_BYTES,
    .file_bytes=DL_RUNTIME_DOORWAY_FILE_BYTES,.max_region_bytes=DL_RUNTIME_DOORWAY_CHUNK_BYTES,.region_count=2,
    .model_entity_ids=links,.object_entity_ids=links,.policy=DL_STREAM_POLICY_AUTHORED,
    .policy_regions=policy,.transitions=gates,.transition_count=2,.max_resident_regions=2
};
static const DlLevel bootstrap={
    .version=2,.title="Synthetic doorway diagnostics",.meshes=meshes,.mesh_count=1,.models=models,.model_count=2,
    .objects=objects,.object_count=2,.spawn={-.3f,0,0},.spawn_yaw=1.57079632679489661923f,.stream=&definition
};
static void put16(uint8_t *p,uint16_t value){p[0]=(uint8_t)(value>>8);p[1]=(uint8_t)value;}
static void put32(uint8_t *p,uint32_t value){p[0]=(uint8_t)(value>>24);p[1]=(uint8_t)(value>>16);p[2]=(uint8_t)(value>>8);p[3]=(uint8_t)value;}
static void putfloat(uint8_t *p,float value){uint32_t bits;memcpy(&bits,&value,4);put32(p,bits);}
static uint32_t crc(const uint8_t *p,size_t bytes){return dl_mission_crc32_update(UINT32_MAX,p,bytes)^UINT32_MAX;}
static void section(uint8_t *p,uint16_t kind,uint32_t offset,uint32_t count,uint32_t stride){
    put16(p,kind);put32(p+4,offset);put32(p+8,count*stride);put32(p+12,count);put32(p+16,stride);
}
static void collider(uint8_t *p,DlVec3 center,DlVec3 half,uint32_t entity){
    putfloat(p,center.x);putfloat(p+4,center.y);putfloat(p+8,center.z);
    putfloat(p+12,half.x);putfloat(p+16,half.y);putfloat(p+20,half.z);put32(p+28,entity);
}
static void chunk(uint8_t *p,uint32_t region){
    memcpy(p,"DLR1",4);put16(p+4,1);put16(p+6,32);put32(p+8,DL_RUNTIME_DOORWAY_CHUNK_BYTES);put32(p+12,2);
    section(p+32,DL_MISSION_SECTION_COLLIDERS,80,2,32);section(p+56,DL_MISSION_SECTION_ENTITIES,144,1,4);
    collider(p+80,(DlVec3){region==1?-2:2,-.25f,0},(DlVec3){2,.25f,2},0);
    if(region==1)collider(p+112,(DlVec3){0,1,0},(DlVec3){.05f,1,.8f},1);
    else collider(p+112,(DlVec3){2.5f,1,0},(DlVec3){.1f,1,.8f},0);
    put32(p+144,region);
}
static void make_package(uint8_t *p){
    chunk(p+FIRST,1);chunk(p+SECOND,2);
    memcpy(p,"DLM1",4);put16(p+4,1);put16(p+6,64);put32(p+8,DL_RUNTIME_DOORWAY_FILE_BYTES);
    put32(p+12,2);put32(p+16,2);put32(p+20,DL_RUNTIME_DOORWAY_DIRECTORY_BYTES);put32(p+24,1);
    for(uint32_t i=0;i<2;++i){uint8_t *row=p+64+i*64;uint32_t offset=i?SECOND:FIRST;
        put32(row,i+1);put32(row+4,offset);put32(row+8,DL_RUNTIME_DOORWAY_CHUNK_BYTES);put32(row+12,DL_RUNTIME_DOORWAY_CHUNK_BYTES);
        put32(row+16,crc(p+offset,DL_RUNTIME_DOORWAY_CHUNK_BYTES));put32(row+20,i);put32(row+24,1);
        putfloat(row+32,i?0:-4);putfloat(row+36,-1);putfloat(row+40,-2);
        putfloat(row+44,i?4:0);putfloat(row+48,3);putfloat(row+52,2);
        uint8_t *entity=p+192+i*16;put32(entity,i+1);put32(entity+4,i+1);
        put16(entity+8,i?DL_MISSION_ENTITY_LOOT:DL_MISSION_ENTITY_DOOR);
    }
    put32(p+224,2);put32(p+228,1);put32(p+28,crc(p,DL_RUNTIME_DOORWAY_DIRECTORY_BYTES));
}
static size_t read_fixture(void *context,uint32_t offset,void *destination,size_t wanted){
    DlRuntimeDoorwayFixture *fixture=context;
    if(fixture->read_failed)return SIZE_MAX;
    if(fixture->read_stalled)return 0;
    if(offset>sizeof fixture->package||wanted>sizeof fixture->package-offset)return SIZE_MAX;
    if(fixture->read_limit&&wanted>fixture->read_limit)wanted=fixture->read_limit;
    memcpy(destination,fixture->package+offset,wanted);return wanted;
}
bool dl_runtime_doorway_init(DlRuntimeDoorwayFixture *fixture){
    if(!fixture)return false;
    memset(fixture,0,sizeof *fixture);make_package(fixture->package);
    if(dl_mission_open(fixture->package,DL_RUNTIME_DOORWAY_DIRECTORY_BYTES,DL_RUNTIME_DOORWAY_FILE_BYTES,&fixture->mission)!=DL_MISSION_OK)return false;
    for(unsigned i=0;i<2;++i)fixture->slots[i]=(DlRegionSlot){.buffer=fixture->buffers[i],.capacity=sizeof fixture->buffers[i]};
    if(dl_world_stream_init(&fixture->world,&fixture->mission,fixture->slots,2,read_fixture,fixture)!=DL_STREAM_OK||
        dl_stream_scene_init(&fixture->scene,&bootstrap,&fixture->mission)!=DL_STREAM_SCENE_OK||
        dl_stream_scene_refresh(&fixture->scene,&fixture->world,NULL)!=DL_STREAM_SCENE_OK)return false;
    dl_game_init(&fixture->game,dl_stream_scene_level(&fixture->scene));return true;
}
DlStreamStatus dl_runtime_doorway_request(DlRuntimeDoorwayFixture *fixture,uint32_t region){
    if(!fixture||region<1||region>2)return DL_STREAM_ARGUMENT;
    return dl_world_stream_request(&fixture->world,region,&fixture->handles[region-1]);
}
DlStreamStatus dl_runtime_doorway_load(DlRuntimeDoorwayFixture *fixture,uint32_t region){
    DlStreamStatus status=dl_runtime_doorway_request(fixture,region);
    if(status!=DL_STREAM_OK)return status;
    for(unsigned i=0;i<2048;++i){DlStreamState state;
        status=dl_world_stream_state(&fixture->world,fixture->handles[region-1],&state);
        if(status!=DL_STREAM_OK)return status;
        if(state==DL_STREAM_RESIDENT)return DL_STREAM_OK;
        if(state==DL_STREAM_FAILED)return fixture->slots[fixture->handles[region-1].slot].failure;
        dl_world_stream_pump(&fixture->world,DL_RUNTIME_DOORWAY_CHUNK_BYTES);
    }
    return DL_STREAM_NOT_READY;
}
DlStreamSceneStatus dl_runtime_doorway_publish(DlRuntimeDoorwayFixture *fixture){
    if(!fixture)return DL_STREAM_SCENE_ARGUMENT;
    DlStreamSceneStatus status=dl_stream_scene_refresh(&fixture->scene,&fixture->world,&fixture->game);
    if(status==DL_STREAM_SCENE_OK)fixture->game.level=dl_stream_scene_level(&fixture->scene);
    return status;
}
uint32_t dl_runtime_doorway_physical_mask(const DlRuntimeDoorwayFixture *fixture){
    if(!fixture)return 0;
    uint32_t mask=0;
    for(unsigned i=0;i<2;++i)if(fixture->slots[i].state==DL_STREAM_RESIDENT)mask|=UINT32_C(1)<<(fixture->slots[i].region_id-1);
    return mask;
}
uint32_t dl_runtime_doorway_published_mask(const DlRuntimeDoorwayFixture *fixture){
    if(!fixture)return 0;
    const DlStreamSceneBank *bank=&fixture->scene.banks[fixture->scene.active_bank];uint32_t mask=0;
    for(uint32_t i=0;i<bank->resident_count;++i)mask|=UINT32_C(1)<<(bank->resident_ids[i]-1);
    return mask;
}
