#include "stream_scene.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef DL_STREAM_SCENE_INTEGRATION
#include "../build/streaming-bootstrap/generated/demo_level.h"
#endif

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
enum { DIRECTORY=264, FIRST=272, SECOND=480, CHUNK_BYTES=200, FILE_BYTES=680 };
static uint8_t package[FILE_BYTES],buffers[2][CHUNK_BYTES];
static DlMission mission;
static DlRegionSlot slots[2];
static DlWorldStream stream;
static DlStreamScene scene;
static DlRegionHandle handles[2];
static const DlVec3 points[]={{0,0,0},{1,0,0},{0,1,0}};
static const uint16_t indices[]={0,1,2};
static const DlMesh meshes[]={ {.vertices=points,.vertex_count=3,.indices=indices,.index_count=3} };
static const DlModelInstance models[]={
    {.id="guard",.mesh=0,.position={2,0,0},.scale={1,1,1},.role=DL_MODEL_GUARD,.texture=-1,.enemy_index=0},
    {.id="door",.mesh=0,.position={-0.05f,1,0},.scale={1,1,1},.role=DL_MODEL_DOOR,.texture=-1,.enemy_index=-1},
    {.id="loot",.mesh=0,.position={3,0.5f,1},.scale={1,1,1},.role=DL_MODEL_LOOT,.texture=-1,.enemy_index=-1}
};
static const DlObjectDef objects[]={
    {.id="door",.kind=DL_OBJECT_DOOR,.position={-0.05f,1,0},.radius=2},
    {.id="loot",.kind=DL_OBJECT_LOOT,.position={3,0.5f,1},.radius=1,.value=25}
};
static const DlEnemyDef enemies[]={ {.id="guard",.type=DL_ENEMY_WATCHMAN,.behavior=DL_BEHAVIOR_SENTRY,
    .spawn={2,0,0},.speed=1,.sight_range=10,.hearing_range=8} };
static const DlLight lights[]={ {.position={0,2,0},.radius=5,.intensity=0.5f} };
static const uint32_t model_links[]={3,2,4},object_links[]={2,4},enemy_links[]={3};
static const DlStreamMaterial materials[]={ {.id=1,.color={128,192,255,255},.double_sided=true} };
static DlStreamDefinition definition={.package_path="rom:/missions/probe.dlm",.directory_bytes=DIRECTORY,
    .file_bytes=FILE_BYTES,.max_region_bytes=CHUNK_BYTES,.region_count=2,.model_entity_ids=model_links,
    .object_entity_ids=object_links,.enemy_entity_ids=enemy_links,.materials=materials,.material_count=1};
static DlLevel bootstrap={.version=2,.title="adapter test",.meshes=meshes,.mesh_count=1,.models=models,.model_count=3,
    .objects=objects,.object_count=2,.enemies=enemies,.enemy_count=1,.lights=lights,.light_count=1,
    .spawn={-2,0,0},.spawn_yaw=1,.stream=&definition,.baked_lighting="rom:/obsolete.bin"};
static void put16(uint8_t *p,uint16_t n){p[0]=(uint8_t)(n>>8);p[1]=(uint8_t)n;}
static void put32(uint8_t *p,uint32_t n){p[0]=(uint8_t)(n>>24);p[1]=(uint8_t)(n>>16);p[2]=(uint8_t)(n>>8);p[3]=(uint8_t)n;}
static void putfloat(uint8_t *p,float n){uint32_t bits;memcpy(&bits,&n,4);put32(p,bits);}
static uint32_t crc32(const uint8_t *p,size_t bytes){
    uint32_t crc=UINT32_MAX;
    while(bytes--){crc^=*p++;for(unsigned bit=0;bit<8;++bit)crc=(crc>>1)^((crc&1)?0xedb88320u:0);}
    return crc^UINT32_MAX;
}
static void section(uint8_t *p,unsigned kind,unsigned offset,unsigned count,unsigned stride){
    put16(p,(uint16_t)kind);put32(p+4,offset);put32(p+8,count*stride);put32(p+12,count);put32(p+16,stride);
}
static void make_chunk(uint8_t *p,unsigned region){
    memcpy(p,"DLR1",4);put16(p+4,1);put16(p+6,32);put32(p+8,CHUNK_BYTES);put32(p+12,3);
    if(region==2)putfloat(p+16,2);
    section(p+32,1,112,1,44);section(p+56,2,160,1,32);section(p+80,3,192,2,4);
    putfloat(p+112,region==1?-3:-1);putfloat(p+124,region==1?-1:1);putfloat(p+140,1);
    put32(p+148,1);put32(p+152,region);
    if(region==1){
        putfloat(p+160,-0.05f);putfloat(p+164,1);putfloat(p+172,0.05f);
        putfloat(p+176,1);putfloat(p+180,1);put32(p+188,2);
    }else{
        putfloat(p+164,-0.25f);putfloat(p+172,2);putfloat(p+176,0.25f);putfloat(p+180,2);
    }
    put32(p+192,region==1?1:3);put32(p+196,region==1?2:4);
}
static void prepare(void){
    memset(package,0,sizeof package);make_chunk(package+FIRST,1);make_chunk(package+SECOND,2);
    memcpy(package,"DLM1",4);put16(package+4,1);put16(package+6,64);put32(package+8,FILE_BYTES);
    put32(package+12,2);put32(package+16,4);put32(package+20,DIRECTORY);put32(package+24,1);
    for(unsigned i=0;i<2;++i){
        uint8_t *p=package+64+i*64;put32(p,i+1);put32(p+4,i?SECOND:FIRST);
        put32(p+8,CHUNK_BYTES);put32(p+12,CHUNK_BYTES);put32(p+16,crc32(package+(i?SECOND:FIRST),CHUNK_BYTES));
        put32(p+20,i);put32(p+24,1);
        putfloat(p+32,i?0:-4);putfloat(p+36,-1);putfloat(p+40,-2);
        putfloat(p+44,i?4:0);putfloat(p+48,3);putfloat(p+52,2);
    }
    const uint16_t kinds[]={0,2,1,3};
    for(unsigned i=0;i<4;++i){uint8_t *p=package+192+i*16;put32(p,i+1);put32(p+4,i<2?1:2);put16(p+8,kinds[i]);}
    put32(package+256,2);put32(package+260,1);put32(package+28,crc32(package,DIRECTORY));
}
static size_t read_memory(void *context,uint32_t offset,void *out,size_t bytes){
    (void)context;if(offset>FILE_BYTES||bytes>FILE_BYTES-offset)return SIZE_MAX;
    memcpy(out,package+offset,bytes);return bytes;
}
static int initialize(void){
    prepare();CHECK(dl_mission_open(package,DIRECTORY,FILE_BYTES,&mission)==DL_MISSION_OK);
    for(unsigned i=0;i<2;++i)slots[i]=(DlRegionSlot){.buffer=buffers[i],.capacity=CHUNK_BYTES};
    CHECK(dl_world_stream_init(&stream,&mission,slots,2,read_memory,NULL)==DL_STREAM_OK);
    CHECK(dl_stream_scene_init(&scene,&bootstrap,&mission)==DL_STREAM_SCENE_OK);
    return 0;
}
static int load(unsigned id){
    CHECK(dl_world_stream_request(&stream,id,&handles[id-1])==DL_STREAM_OK);
    DlStreamState state;
    for(unsigned i=0;i<32;++i){
        CHECK(dl_world_stream_state(&stream,handles[id-1],&state)==DL_STREAM_OK);
        if(state==DL_STREAM_RESIDENT)return 0;
        CHECK(state!=DL_STREAM_FAILED);dl_world_stream_pump(&stream,53);
    }
    CHECK(false);return 1;
}
static const DlModelInstance *model_by_id(const DlLevel *level,const char *id){
    for(int i=0;i<level->model_count;++i)if(level->models[i].id&&strcmp(level->models[i].id,id)==0)return &level->models[i];
    return NULL;
}
static int working_views(void){
    CHECK(initialize()==0);CHECK(!dl_stream_scene_level(&scene));
    CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *level=dl_stream_scene_level(&scene);
    CHECK(level->model_count==1&&model_by_id(level,"door")&&level->collider_count==2&&
        level->objects==objects&&level->enemies==enemies);
    CHECK(load(1)==0);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    level=dl_stream_scene_level(&scene);CHECK(level->model_count==2&&level->mesh_count==2&&level->collider_count==2);
    CHECK(level->spawn.x==-2&&level->spawn_yaw==1&&level->lights==lights&&level->baked_lighting==NULL);
    CHECK(model_by_id(level,"door")&&!model_by_id(level,"loot")&&!model_by_id(level,"guard"));
    CHECK(level->colliders[0].door&&level->collider_objects[0]==0&&!level->colliders[1].door);
    CHECK(level->models[1].role==DL_MODEL_STATIC&&level->models[1].color[0]==128&&level->models[1].double_sided);
    CHECK(level->meshes[1].vertices[0].x==-3&&level->meshes[1].indices[2]==2);
    CHECK(slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    DlGame game;dl_game_init(&game,level);CHECK(dl_stream_scene_enemy_mask(&scene,&game)==0);
    CHECK(!dl_position_clear(&game,(DlVec3){2,0,0},0.18f,1.65f));
    CHECK(dl_stream_scene_region_at(&scene,(DlVec3){-2,0,0})==1);
    CHECK(dl_stream_scene_region_at(&scene,(DlVec3){2,0,0})==2);
    CHECK(dl_stream_scene_region_at(&scene,(DlVec3){20,0,0})==0);
    game.open_objects=1;game.door_open=true;game.collected_objects=2;game.loot_value=25;
    game.enemies[0].awareness=0.75f;game.enemies[0].state=DL_CHASE;game.enemies[0].patrol_index=5;
    CHECK(load(2)==0);CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
    game.level=dl_stream_scene_level(&scene);level=game.level;
    CHECK(level->model_count==5&&level->mesh_count==3&&level->collider_count==2);
    CHECK(model_by_id(level,"guard")->enemy_index==0&&model_by_id(level,"loot"));
    CHECK(dl_stream_scene_enemy_mask(&scene,&game)==1);
    CHECK(dl_position_clear(&game,(DlVec3){2,0,0},0.18f,1.65f));
    CHECK(dl_game_model_open(&game,model_by_id(level,"door")));
    CHECK(!dl_game_model_visible(&game,model_by_id(level,"loot")));
    CHECK(dl_world_stream_retire(&stream,handles[1])==DL_STREAM_OK);
    CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
    game.level=dl_stream_scene_level(&scene);CHECK(!model_by_id(game.level,"guard"));
    CHECK(dl_stream_scene_enemy_mask(&scene,&game)==0);
    CHECK(game.collected_objects==2&&game.loot_value==25&&game.door_open);
    CHECK(game.enemies[0].awareness==0.75f&&game.enemies[0].state==DL_CHASE&&game.enemies[0].patrol_index==5);
    for(unsigned i=0;i<64;++i){
        CHECK(load(2)==0);CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
        game.level=dl_stream_scene_level(&scene);CHECK(!dl_game_model_visible(&game,model_by_id(game.level,"loot")));
        CHECK(dl_world_stream_retire(&stream,handles[1])==DL_STREAM_OK);
        CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);game.level=dl_stream_scene_level(&scene);
        CHECK(stream.stats.reserved_bytes==CHUNK_BYTES&&slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    }
    /* Live actor placement can cross home-region boundaries without identity changes. */
    game.enemies[0].position=(DlVec3){-2,0,0};CHECK(dl_stream_scene_enemy_mask(&scene,&game)==1);
    CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);game.level=dl_stream_scene_level(&scene);
    CHECK(model_by_id(game.level,"guard")&&model_by_id(game.level,"guard")->enemy_index==0);
    CHECK(load(2)==0);CHECK(dl_world_stream_retire(&stream,handles[0])==DL_STREAM_OK);
    CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);game.level=dl_stream_scene_level(&scene);
    CHECK(!dl_stream_scene_region_resident(&scene,1)&&dl_stream_scene_region_resident(&scene,2));
    CHECK(model_by_id(game.level,"door")&&dl_game_model_open(&game,model_by_id(game.level,"door")));
    CHECK(game.level->models[0].mesh==bootstrap.models[1].mesh&&game.level->objects==objects);
    CHECK(game.door_open&&game.collected_objects==2&&game.loot_value==25);
    return 0;
}
static int atomic_failures(void){
    CHECK(initialize()==0);CHECK(load(1)==0);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *old=dl_stream_scene_level(&scene);DlVec3 vertex=old->meshes[1].vertices[0];uint32_t revision=scene.revision;
    uint8_t *p=slots[handles[0].slot].buffer;
    put32(p+148,2);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_CORRUPT);
    CHECK(dl_stream_scene_level(&scene)==old&&scene.revision==revision&&old->meshes[1].vertices[0].x==vertex.x);
    CHECK(slots[handles[0].slot].cpu_pins==0);
    put32(p+148,1);put32(p+196,1);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_CORRUPT);
    put32(p+196,4);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_CORRUPT);
    put32(p+196,2);put32(p+112,0x7fc00000u);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_CORRUPT);
    putfloat(p+112,-3);put32(p+188,5);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_CORRUPT);
    put32(p+188,2);put32(p+88,4);put32(p+92,1);
    CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&slots[handles[0].slot].chunk)==DL_MISSION_OK);
    CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_CORRUPT);
    CHECK(dl_stream_scene_level(&scene)==old&&scene.revision==revision&&slots[handles[0].slot].cpu_pins==0);
    return 0;
}
static int initialization_limits(void){
    CHECK(initialize()==0);DlStreamScene scratch;DlLevel bad=bootstrap;
    bad.model_count=DL_MAX_MODELS+1;CHECK(dl_stream_scene_init(&scratch,&bad,&mission)==DL_STREAM_SCENE_CAPACITY);
    bad=bootstrap;bad.collider_count=1;CHECK(dl_stream_scene_init(&scratch,&bad,&mission)==DL_STREAM_SCENE_ARGUMENT);
    DlStreamDefinition invalid=definition;bad=bootstrap;bad.stream=&invalid;invalid.max_region_bytes=CHUNK_BYTES-1;
    CHECK(dl_stream_scene_init(&scratch,&bad,&mission)==DL_STREAM_SCENE_ARGUMENT);
    invalid=definition;invalid.region_count=3;CHECK(dl_stream_scene_init(&scratch,&bad,&mission)==DL_STREAM_SCENE_ARGUMENT);
    invalid=definition;invalid.material_count=DL_STREAM_SCENE_MAX_MATERIALS+1;
    CHECK(dl_stream_scene_init(&scratch,&bad,&mission)==DL_STREAM_SCENE_CAPACITY);
    invalid=definition;uint32_t wrong_links[]={2,3,4};invalid.model_entity_ids=wrong_links;
    CHECK(dl_stream_scene_init(&scratch,&bad,&mission)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(dl_stream_scene_init(NULL,&bootstrap,&mission)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(dl_stream_scene_refresh(NULL,&stream,NULL)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(strcmp(dl_stream_scene_status_name(DL_STREAM_SCENE_CORRUPT),"corrupt")==0);
    return 0;
}
typedef struct {const uint8_t *data;size_t bytes;} LargeReader;
static size_t read_large(void *context,uint32_t offset,void *out,size_t bytes){
    const LargeReader *reader=context;
    if(offset>reader->bytes||bytes>reader->bytes-offset)return SIZE_MAX;
    memcpy(out,reader->data+offset,bytes);return bytes;
}
static int refresh_capacity(void){
    enum { MAX_TRIANGLES=DL_STREAM_SCENE_MAX_TRIANGLES+1,MAX_BYTES=64+MAX_TRIANGLES*44 };
    static uint8_t large_package[128+MAX_BYTES],large_buffer[MAX_BYTES];
    static DlStreamScene scratch;
    enum { MATERIALS=DL_STREAM_SCENE_MAX_GROUPS/2+1 };
    DlStreamMaterial many_materials[MATERIALS];
    for(unsigned i=0;i<sizeof many_materials/sizeof many_materials[0];++i)
        many_materials[i]=(DlStreamMaterial){.id=i+1,.color={128,128,128,255}};
    for(unsigned attempt=0;attempt<2;++attempt){
        unsigned triangles=attempt?DL_STREAM_SCENE_MAX_GROUPS+1:MAX_TRIANGLES;
        unsigned stride=attempt?80:44;
        size_t chunk_bytes=64+(size_t)triangles*stride,file_bytes=128+chunk_bytes;
        memset(large_package,0,sizeof large_package);
        uint8_t *p=large_package+128;memcpy(p,"DLR1",4);put16(p+4,attempt?2:1);put16(p+6,32);
        put32(p+8,(uint32_t)chunk_bytes);put32(p+12,1);section(p+32,1,64,triangles,stride);
        for(unsigned i=0;i<triangles;++i){
            uint8_t *triangle=p+64+i*stride;putfloat(triangle+12,1);putfloat(triangle+28,1);
            put32(triangle+36,attempt?i%MATERIALS+1:1);put32(triangle+40,i+1);
            if(attempt)triangle[77]=(uint8_t)(i<MATERIALS?DL_MISSION_TRIANGLE_UV:0);
        }
        memcpy(large_package,"DLM1",4);put16(large_package+4,1);put16(large_package+6,64);
        put32(large_package+8,(uint32_t)file_bytes);put32(large_package+12,1);
        put32(large_package+20,128);put32(large_package+24,1);
        put32(large_package+64,1);put32(large_package+68,128);put32(large_package+72,(uint32_t)chunk_bytes);
        put32(large_package+76,(uint32_t)chunk_bytes);put32(large_package+80,crc32(p,chunk_bytes));
        for(unsigned axis=0;axis<3;++axis){putfloat(large_package+96+axis*4,-4);putfloat(large_package+108+axis*4,4);}
        put32(large_package+28,crc32(large_package,128));
        DlMission parsed;CHECK(dl_mission_open(large_package,128,file_bytes,&parsed)==DL_MISSION_OK);
        DlStreamDefinition def={.package_path="probe",.directory_bytes=128,.file_bytes=(uint32_t)file_bytes,
            .max_region_bytes=(uint32_t)chunk_bytes,.region_count=1,.materials=many_materials,
            .material_count=attempt?MATERIALS:1};
        DlLevel base={.stream=&def};CHECK(dl_stream_scene_init(&scratch,&base,&parsed)==DL_STREAM_SCENE_OK);
        DlRegionSlot slot={.buffer=large_buffer,.capacity=sizeof large_buffer};DlWorldStream manager;
        LargeReader reader={large_package,file_bytes};
        CHECK(dl_world_stream_init(&manager,&parsed,&slot,1,read_large,&reader)==DL_STREAM_OK);
        DlRegionHandle handle;CHECK(dl_world_stream_request(&manager,1,&handle)==DL_STREAM_OK);
        for(unsigned i=0;i<256&&slot.state!=DL_STREAM_RESIDENT;++i)dl_world_stream_pump(&manager,256);
        CHECK(slot.state==DL_STREAM_RESIDENT);
        CHECK(dl_stream_scene_refresh(&scratch,&manager,NULL)==DL_STREAM_SCENE_CAPACITY);
        CHECK(!dl_stream_scene_level(&scratch)&&scratch.revision==0&&slot.cpu_pins==0);
    }
    return 0;
}
static int textured_v2_scene(void){
    enum { V2_SECOND=512,V2_CHUNK=232,V2_FILE=744 };
    static uint8_t bytes[V2_FILE],data[2][V2_CHUNK];static DlStreamScene adapter;
    prepare();memset(bytes,0,sizeof bytes);memcpy(bytes,package,DIRECTORY);put32(bytes+8,V2_FILE);
    for(unsigned i=0;i<2;++i){
        uint8_t *chunk=bytes+(i?V2_SECOND:FIRST);
        memcpy(chunk,package+(i?SECOND:FIRST),CHUNK_BYTES);
        memmove(chunk+224,chunk+192,8);memmove(chunk+192,chunk+160,32);
        memset(chunk+156,0,36);put16(chunk+4,2);put32(chunk+8,V2_CHUNK);
        section(chunk+32,1,112,1,80);section(chunk+56,2,192,1,32);section(chunk+80,3,224,2,4);
        putfloat(chunk+156,-2.5f);putfloat(chunk+160,6.25f);putfloat(chunk+164,2);putfloat(chunk+168,6.25f);
        putfloat(chunk+172,2);putfloat(chunk+176,7.5f);
        chunk[181]=127;chunk[184]=127;chunk[187]=127;chunk[189]=3;
        uint8_t *region=bytes+64+i*64;put32(region+4,i?V2_SECOND:FIRST);
        put32(region+8,V2_CHUNK);put32(region+12,V2_CHUNK);put32(region+16,crc32(chunk,V2_CHUNK));
    }
    put32(bytes+28,0);put32(bytes+28,crc32(bytes,DIRECTORY));
    DlMission parsed;CHECK(dl_mission_open(bytes,DIRECTORY,sizeof bytes,&parsed)==DL_MISSION_OK);
    const DlTexture textures[]={ {.path="rom:/textures/test.sprite",.width=32,.height=32} };
    DlStreamMaterial textured=materials[0];textured.texture_id=1;
    DlStreamDefinition def=definition;def.file_bytes=V2_FILE;def.max_region_bytes=V2_CHUNK;def.materials=&textured;
    DlLevel base=bootstrap;base.stream=&def;base.textures=textures;base.texture_count=1;
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_OK);
    DlRegionSlot loaded[2]={{.buffer=data[0],.capacity=sizeof data[0]},{.buffer=data[1],.capacity=sizeof data[1]}};
    DlWorldStream manager;LargeReader reader={bytes,sizeof bytes};
    CHECK(dl_world_stream_init(&manager,&parsed,loaded,2,read_large,&reader)==DL_STREAM_OK);
    DlRegionHandle first={0},second;
    CHECK(dl_world_stream_request(&manager,1,&first)==DL_STREAM_OK);
    CHECK(dl_world_stream_request(&manager,2,&second)==DL_STREAM_OK);
    for(unsigned i=0;i<32&&(loaded[0].state!=DL_STREAM_RESIDENT||loaded[1].state!=DL_STREAM_RESIDENT);++i)
        dl_world_stream_pump(&manager,256);
    CHECK(loaded[0].state==DL_STREAM_RESIDENT&&loaded[1].state==DL_STREAM_RESIDENT);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *old=dl_stream_scene_level(&adapter);
    CHECK(old->textures==textures&&old->texture_count==1);
    CHECK(old->models[3].texture==0&&old->meshes[1].uvs&&old->meshes[1].normals);
    CHECK(old->meshes[1].uvs[0].u==-2.5f&&old->meshes[1].uvs[0].v==6.25f&&old->meshes[1].uvs[2].v==7.5f);
    CHECK(old->meshes[1].normals[0].y==127&&old->meshes[1].normals[0].x==0);
    CHECK(old->meshes[1].vertices[0].x==-3&&old->meshes[1].indices[2]==2);
    /* Texture links cannot silently fall back to the untextured v1 behavior. */
    data[first.slot][189]=DL_MISSION_TRIANGLE_NORMAL;
    CHECK(dl_stream_scene_refresh(&adapter,&manager,NULL)==DL_STREAM_SCENE_CORRUPT&&dl_stream_scene_level(&adapter)==old);
    data[first.slot][189]=3;memset(data[first.slot]+180,0,3);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,NULL)==DL_STREAM_SCENE_CORRUPT&&dl_stream_scene_level(&adapter)==old);
    textured.texture_id=2;
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(loaded[0].cpu_pins==0&&loaded[1].cpu_pins==0);
    return 0;
}
#ifdef DL_STREAM_SCENE_INTEGRATION
static int cooked_fixture(const char *path){
    FILE *file=fopen(path,"rb");CHECK(file);CHECK(fseek(file,0,SEEK_END)==0);long length=ftell(file);
    CHECK(length>0&&fseek(file,0,SEEK_SET)==0);
    uint8_t *bytes=malloc((size_t)length);CHECK(bytes);CHECK(fread(bytes,1,(size_t)length,file)==(size_t)length);fclose(file);
    const DlStreamDefinition *def=dl_demo_level.stream;DlMission parsed;
    CHECK(dl_mission_open(bytes,def->directory_bytes,(size_t)length,&parsed)==DL_MISSION_OK);
    static uint8_t data[2][32768];static DlStreamScene adapter;
    DlRegionSlot loaded[2]={{.buffer=data[0],.capacity=sizeof data[0]}, {.buffer=data[1],.capacity=sizeof data[1]}};
    DlWorldStream manager;LargeReader reader={bytes,(size_t)length};
    CHECK(dl_world_stream_init(&manager,&parsed,loaded,2,read_large,&reader)==DL_STREAM_OK);
    CHECK(dl_stream_scene_init(&adapter,&dl_demo_level,&parsed)==DL_STREAM_SCENE_OK);
    DlRegionHandle handle;
    CHECK(dl_world_stream_request(&manager,parsed.entry_region_id,&handle)==DL_STREAM_OK);
    for(unsigned i=0;i<256&&loaded[handle.slot].state!=DL_STREAM_RESIDENT;++i)dl_world_stream_pump(&manager,256);
    CHECK(loaded[handle.slot].state==DL_STREAM_RESIDENT);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,NULL)==DL_STREAM_SCENE_OK);
    DlGame game;dl_game_init(&game,dl_stream_scene_level(&adapter));
    CHECK(game.level->objects==dl_demo_level.objects&&game.level->enemies==dl_demo_level.enemies);
    CHECK(dl_stream_scene_enemy_mask(&adapter,&game)==0);
    int loot=-1;
    for(int i=0;i<game.level->object_count;++i)if(game.level->objects[i].kind==DL_OBJECT_LOOT)loot=i;
    CHECK(loot>=0);game.collected_objects=UINT64_C(1)<<loot;game.loot_value=25;
    game.door_open=true;game.enemies[0].awareness=0.8f;
    for(uint32_t id=1;id<=parsed.region_count;++id){
        CHECK(dl_world_stream_request(&manager,id,&handle)==DL_STREAM_OK);
        for(unsigned i=0;i<256&&loaded[handle.slot].state!=DL_STREAM_RESIDENT;++i)dl_world_stream_pump(&manager,256);
        CHECK(loaded[handle.slot].state==DL_STREAM_RESIDENT);
    }
    CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_OK);
    game.level=dl_stream_scene_level(&adapter);
    CHECK(dl_stream_scene_enemy_mask(&adapter,&game)==1);
    const DlModelInstance *loot_model=model_by_id(game.level,game.level->objects[loot].id);
    CHECK(loot_model&&!dl_game_model_visible(&game,loot_model));
    CHECK(game.enemies[0].awareness==0.8f&&game.loot_value==25&&game.door_open);
    printf("cooked bootstrap integration passed regions=%u groups=%d statics=%d models=%d colliders=%d\n",
        parsed.region_count,adapter.banks[adapter.active_bank].group_count,adapter.banks[adapter.active_bank].triangle_count,
        game.level->model_count,game.level->collider_count);
    free(bytes);return 0;
}
#endif
static int authored_five_region_floor_views(void){
    enum { COUNT=5,DIR=464,STRIDE=256,CHUNK=244,BYTES=DIR+(COUNT-1)*STRIDE+CHUNK };
    static uint8_t bytes[BYTES],data[2][CHUNK];static DlStreamScene adapter;
    memset(bytes,0,sizeof bytes);memcpy(bytes,"DLM1",4);put16(bytes+4,1);put16(bytes+6,64);
    put32(bytes+8,BYTES);put32(bytes+12,COUNT);put32(bytes+16,COUNT);put32(bytes+20,DIR);put32(bytes+24,1);
    DlStreamPolicyRegion policy[COUNT];DlStreamTransition gates[8];
    for(unsigned i=0;i<COUNT;++i){
        unsigned offset=DIR+i*STRIDE;uint8_t *chunk=bytes+offset;
        memcpy(chunk,"DLR1",4);put16(chunk+4,3);put16(chunk+6,32);put32(chunk+8,CHUNK);put32(chunk+12,3);
        putfloat(chunk+16,(float)i*10);section(chunk+32,1,112,1,80);section(chunk+56,3,192,1,4);section(chunk+80,4,208,1,36);
        const float triangle[9]={-1,0,-1,1,0,-1,-1,0,1},floor[9]={-1,0,-1,1,0,-1,-1,1,1};
        for(unsigned v=0;v<9;++v){putfloat(chunk+112+v*4,triangle[v]);putfloat(chunk+208+v*4,floor[v]);}
        put32(chunk+148,1);put32(chunk+152,i+1);chunk[181]=127;chunk[184]=127;chunk[187]=127;chunk[189]=3;
        put32(chunk+192,i+1);
        uint8_t *region=bytes+64+i*64;put32(region,i+1);put32(region+4,offset);
        put32(region+8,CHUNK);put32(region+12,CHUNK);put32(region+16,crc32(chunk,CHUNK));
        putfloat(region+32,-100);putfloat(region+36,-1);putfloat(region+40,-10);
        putfloat(region+44,100);putfloat(region+48,3);putfloat(region+52,10);
        uint8_t *entity=bytes+384+i*16;put32(entity,i+1);put32(entity+4,i+1);
        policy[i]=(DlStreamPolicyRegion){.activation_minimum={(float)i*10-5.1f,-1,-2},
            .activation_maximum={(float)i*10+5.1f,3,2},.working_mask=UINT32_C(1)<<i};
        if(i<COUNT-1){
            gates[i*2]=(DlStreamTransition){.from_region=i+1,.to_region=i+2,
                .minimum={(float)i*10+4.8f,-1,-1},.maximum={(float)i*10+5.2f,3,1}};
            gates[i*2+1]=gates[i*2];gates[i*2+1].from_region=i+2;gates[i*2+1].to_region=i+1;
        }
    }
    put32(bytes+28,crc32(bytes,DIR));DlMission parsed;
    CHECK(dl_mission_open(bytes,DIR,BYTES,&parsed)==DL_MISSION_OK);
    DlStreamDefinition def={.package_path="five",.directory_bytes=DIR,.file_bytes=BYTES,.max_region_bytes=CHUNK,
        .region_count=COUNT,.materials=materials,.material_count=1,.policy=DL_STREAM_POLICY_AUTHORED,
        .policy_regions=policy,.transitions=gates,.transition_count=8,.max_resident_regions=2};
    DlLevel base={.stream=&def,.spawn={0,0,0}};
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_region_at_preferred(&adapter,(DlVec3){5,0,0},1)==1);
    CHECK(dl_stream_scene_region_at_preferred(&adapter,(DlVec3){5,0,0},2)==2);
    CHECK(dl_stream_scene_region_at(&adapter,(DlVec3){19,0,0})==3);
    CHECK(dl_stream_scene_demand(&adapter,(DlVec3){-1,0,0},1)==1);
    CHECK(dl_stream_scene_demand(&adapter,(DlVec3){.4f,0,0},1)==3); /* early bounded approach */
    CHECK(dl_stream_scene_demand(&adapter,(DlVec3){4,0,0},1)==3);
    CHECK(dl_stream_scene_demand(&adapter,(DlVec3){13,0,0},2)==6);
    CHECK(dl_stream_scene_transition_at(&adapter,1,2,(DlVec3){5.2f,0,0}));
    CHECK(!dl_stream_scene_transition_at(&adapter,1,3,(DlVec3){5.2f,0,0}));
    DlStreamPolicyState policy_state={.current_region=2};
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){13,0,0},2,&policy_state)==6);
    CHECK(policy_state.pending_transition==3&&policy_state.current_region==2);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){7,0,0},2,&policy_state)==3);
    CHECK(policy_state.pending_transition==2&&policy_state.current_region==2);
    DlStreamTransition original_back=gates[1],original_forward=gates[2];
    gates[1].minimum.x=7.8f;gates[1].maximum.x=8.2f;gates[2].minimum.x=11.8f;gates[2].maximum.x=12.2f;
    policy_state=(DlStreamPolicyState){.current_region=2};
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){10,0,0},2,&policy_state)==3); /* stable tie by gate order */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){10,0,0},2,&policy_state)==3); /* stationary hysteresis */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){10.05f,0,0},2,&policy_state)==6); /* prefetch the approached gate */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){10.04f,0,0},2,&policy_state)==6); /* tiny landing correction retains approach */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){9.90f,0,0},2,&policy_state)==3); /* deliberate reversal selects return gate */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){10.3f,0,0},2,&policy_state)==6);
    gates[1]=original_back;gates[2]=original_forward;
    policy[2].working_mask=12;def.max_resident_regions=3;
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_OK);
    policy_state=(DlStreamPolicyState){.current_region=2};
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){15.2f,0,0},4,&policy_state)==14);
    CHECK(policy_state.current_region==2); /* one published target chunk is insufficient */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){15.2f,0,0},14,&policy_state)==14);
    CHECK(policy_state.current_region==3);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){20.4f,0,0},14,&policy_state)==12);
    policy[2].working_mask=4;def.max_resident_regions=2;
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_OK);
    DlRegionSlot loaded[2]={{.buffer=data[0],.capacity=CHUNK},{.buffer=data[1],.capacity=CHUNK}};
    DlWorldStream manager;LargeReader reader={bytes,BYTES};DlRegionHandle previous={0},current={0};
    CHECK(dl_world_stream_init(&manager,&parsed,loaded,2,read_large,&reader)==DL_STREAM_OK);
    CHECK(dl_world_stream_request(&manager,1,&previous)==DL_STREAM_OK);
    while(loaded[previous.slot].state!=DL_STREAM_RESIDENT)dl_world_stream_pump(&manager,256);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,NULL)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_level(&adapter)->floor_count==1&&dl_stream_scene_level(&adapter)->collider_count==1);
    CHECK(dl_stream_scene_level(&adapter)->colliders[0].center.x==5);
    CHECK(dl_stream_scene_gate_waiting(&adapter,(DlVec3){4.6f,0,0},1));
    DlGame game;dl_game_init(&game,dl_stream_scene_level(&adapter));game.elapsed=7;game.loot_value=23;
    for(uint32_t id=2;id<=COUNT;++id){
        CHECK(dl_world_stream_request(&manager,id,&current)==DL_STREAM_OK);
        while(loaded[current.slot].state!=DL_STREAM_RESIDENT)dl_world_stream_pump(&manager,256);
        CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_OK);
        game.level=dl_stream_scene_level(&adapter);CHECK(game.level->floor_count==2);
        CHECK(game.elapsed==7&&game.loot_value==23&&adapter.banks[adapter.active_bank].resident_count==2);
        CHECK(!dl_stream_scene_gate_waiting(&adapter,(DlVec3){(float)(id-2)*10+5,0,0},(UINT32_C(1)<<(id-2))|(UINT32_C(1)<<(id-1))));
        if(id==2){
            const DlMissionChunk *borrow;
            CHECK(dl_world_stream_borrow(&manager,previous,&borrow)==DL_STREAM_OK);
            CHECK(dl_world_stream_submit(&manager,previous,7,9)==DL_STREAM_OK);
            CHECK(dl_world_stream_release(&manager,previous)==DL_STREAM_OK);
            CHECK(dl_world_stream_cancel(&manager,previous)==DL_STREAM_OK);
            DlRegionHandle next;CHECK(dl_world_stream_request(&manager,3,&next)==DL_STREAM_CAPACITY);
            CHECK(loaded[current.slot].state==DL_STREAM_RESIDENT);
            CHECK(dl_world_stream_complete(&manager,7,8)==DL_STREAM_OK);
            CHECK(dl_world_stream_request(&manager,3,&next)==DL_STREAM_CAPACITY);
            CHECK(dl_world_stream_complete(&manager,7,9)==DL_STREAM_OK);
            CHECK(dl_world_stream_request(&manager,3,&next)==DL_STREAM_OK);
            dl_world_stream_pump(&manager,128);
            CHECK(loaded[next.slot].state==DL_STREAM_READING);
            CHECK(dl_world_stream_cancel(&manager,next)==DL_STREAM_OK);
            CHECK(loaded[next.slot].state==DL_STREAM_FREE&&loaded[current.slot].state==DL_STREAM_RESIDENT);
            previous=current;
            CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_OK);
            game.level=dl_stream_scene_level(&adapter);continue;
        }
        CHECK(dl_world_stream_cancel(&manager,previous)==DL_STREAM_OK);previous=current;
        CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_OK);
        game.level=dl_stream_scene_level(&adapter);CHECK(game.level->floor_count==1);
    }
    const DlLevel *old=game.level;putfloat(data[current.slot]+208+12,-1);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_CORRUPT);
    CHECK(dl_stream_scene_level(&adapter)==old&&loaded[current.slot].cpu_pins==0);
    const float precision_floor[9]={0,0,0,.001f,.001f,0,0,0,1};
    for(unsigned v=0;v<9;++v)putfloat(data[current.slot]+208+v*4,precision_floor[v]);
    loaded[current.slot].chunk.origin.x=1000;DlMissionFloorTriangle local_floor;
    CHECK(dl_mission_floor(&loaded[current.slot].chunk,0,&local_floor)==DL_MISSION_OK);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_CORRUPT);
    CHECK(dl_stream_scene_level(&adapter)==old&&loaded[current.slot].cpu_pins==0);
    /* A much larger origin collapses the world footprint entirely. */
    loaded[current.slot].chunk.origin.x=100000000;
    CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_CORRUPT);
    CHECK(dl_stream_scene_level(&adapter)==old&&loaded[current.slot].cpu_pins==0);
    return 0;
}
static int authored_swept_gate_crossings(void){
    prepare();DlMission parsed;CHECK(dl_mission_open(package,DIRECTORY,FILE_BYTES,&parsed)==DL_MISSION_OK);
    DlStreamPolicyRegion policy[]={
        {.activation_minimum={-4,-1,-2},.activation_maximum={0,3,2},.working_mask=1},
        {.activation_minimum={0,-1,-2},.activation_maximum={4,3,2},.working_mask=2}};
    DlStreamTransition gates[]={
        {.from_region=1,.to_region=2,.minimum={-.02f,-1,-.5f},.maximum={.02f,3,.5f}},
        {.from_region=2,.to_region=1,.minimum={-.02f,-1,-.5f},.maximum={.02f,3,.5f}}};
    DlStreamDefinition def=definition;def.policy=DL_STREAM_POLICY_AUTHORED;def.policy_regions=policy;
    def.transitions=gates;def.transition_count=2;def.max_resident_regions=2;
    DlLevel base=bootstrap;base.stream=&def;static DlStreamScene adapter;
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_OK);
    DlStreamPolicyState state={0};
    /* Two 60 Hz fixed ticks can move 0.07 m across a 0.04 m authored gate.
     * Neither endpoint occupies the gate, and its bounds remain unchanged. */
    CHECK(!dl_stream_scene_transition_at(&adapter,1,2,(DlVec3){.035f,0,0}));
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.035f,0,0},3,&state)==3);
    CHECK(state.current_region==1&&state.has_previous_position);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){.035f,0,0},3,&state)==3);
    CHECK(state.current_region==2&&state.previous_position.x==.035f);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.035f,0,0},3,&state)==3);
    CHECK(state.current_region==1); /* Immediate reverse uses the new segment. */
    policy[0].activation_maximum.x=.1f;policy[1].activation_minimum.x=-.1f;
    dl_stream_scene_policy_reset(&state);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.035f,0,0},3,&state)==3);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){.035f,0,0},3,&state)==3);
    CHECK(state.current_region==1&&state.previous_position.x==-.035f);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){.14f,0,0},3,&state)==3);
    CHECK(state.current_region==2); /* Gate crossed before preferred overlap ended. */
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.035f,0,0},3,&state)==3);
    CHECK(state.current_region==2&&state.previous_position.x==.14f);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.14f,0,0},3,&state)==3);
    CHECK(state.current_region==1);
    policy[0].activation_maximum.x=0;policy[1].activation_minimum.x=0;
    dl_stream_scene_policy_reset(&state);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-1,0,1},3,&state)==3);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){1,0,1},3,&state)==3);
    CHECK(state.current_region==1); /* A segment outside the gate is no crossing. */
    dl_stream_scene_policy_reset(&state);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.035f,0,0},1,&state)==3);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){.035f,0,0},1,&state)==3);
    CHECK(state.current_region==1&&state.pending_transition==1&&state.previous_position.x==-.035f);
    CHECK(dl_stream_scene_gate_waiting(&adapter,(DlVec3){.035f,0,0},1));
    /* Only the from region is published: the same thin authored volume is
     * present as a real collision barrier even if a caller attempts the step. */
    DlRegionSlot loaded[2]={{.buffer=buffers[0],.capacity=CHUNK_BYTES},{.buffer=buffers[1],.capacity=CHUNK_BYTES}};
    DlWorldStream manager;DlRegionHandle first,second;
    CHECK(dl_world_stream_init(&manager,&parsed,loaded,2,read_memory,NULL)==DL_STREAM_OK);
    CHECK(dl_world_stream_request(&manager,1,&first)==DL_STREAM_OK);
    while(loaded[first.slot].state!=DL_STREAM_RESIDENT)dl_world_stream_pump(&manager,256);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,NULL)==DL_STREAM_SCENE_OK);
    DlGame game;dl_game_init(&game,dl_stream_scene_level(&adapter));game.open_objects=1;game.door_open=true;
    CHECK(game.level->collider_count==2&&!game.level->colliders[1].door);
    CHECK(game.level->colliders[1].center.x==0&&game.level->colliders[1].half_size.x==.02f);
    CHECK(!dl_position_clear(&game,(DlVec3){.035f,0,0},.18f,1.65f));
    CHECK(dl_world_stream_request(&manager,2,&second)==DL_STREAM_OK);
    while(loaded[second.slot].state!=DL_STREAM_RESIDENT)dl_world_stream_pump(&manager,256);
    CHECK(dl_stream_scene_refresh(&adapter,&manager,&game)==DL_STREAM_SCENE_OK);
    game.level=dl_stream_scene_level(&adapter);
    CHECK(dl_position_clear(&game,(DlVec3){.035f,0,0},.18f,1.65f));
    CHECK(!dl_stream_scene_gate_waiting(&adapter,(DlVec3){.035f,0,0},3));
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){.1f,0,0},3,&state)==3);
    CHECK(state.current_region==2); /* Completed publication commits retained crossing. */
    dl_stream_scene_policy_reset(&state);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.035f,0,0},1,&state)==3);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){.035f,0,0},1,&state)==3);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.05f,0,0},1,&state)==3);
    CHECK(state.current_region==1&&state.previous_position.x==-.05f);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-.05f,0,0},3,&state)==3);
    CHECK(state.current_region==1); /* Readiness does not commit a reversed attempt. */
    state.current_region=2;state.pending_transition=2;state.previous_position=(DlVec3){3,0,0};
    dl_stream_scene_policy_reset(&state);
    CHECK(!state.current_region&&!state.pending_transition&&!state.has_previous_position);
    CHECK(state.previous_position.x==0&&state.previous_position.y==0&&state.previous_position.z==0);
    CHECK(dl_stream_scene_policy_update(&adapter,(DlVec3){-3.5f,0,0},1,&state)==3);
    CHECK(state.current_region==1&&state.previous_position.x==-3.5f); /* Restart locates fresh start. */
    return 0;
}
static int authored_door_target(void){
    prepare();DlMission parsed;CHECK(dl_mission_open(package,DIRECTORY,FILE_BYTES,&parsed)==DL_MISSION_OK);
    DlStreamPolicyRegion policy[]={
        {.activation_minimum={-4,-1,-2},.activation_maximum={0,3,2},.working_mask=1},
        {.activation_minimum={0,-1,-2},.activation_maximum={4,3,2},.working_mask=2}};
    DlStreamTransition gates[]={
        {.from_region=1,.to_region=2,.minimum={-.1f,-1,-2},.maximum={.1f,3,2},.door_entity_id=2},
        {.from_region=2,.to_region=1,.minimum={-.1f,-1,-2},.maximum={.1f,3,2},.door_entity_id=2}};
    DlStreamDefinition def=definition;def.policy=DL_STREAM_POLICY_AUTHORED;def.policy_regions=policy;def.transitions=gates;def.transition_count=2;def.max_resident_regions=2;
    DlLevel base=bootstrap;base.stream=&def;static DlStreamScene adapter;
    CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_OK);
    uint32_t gate=0;CHECK(dl_stream_scene_door_target(&adapter,1,2,&gate)==3&&gate==1);
    CHECK(dl_stream_scene_door_target(&adapter,2,2,&gate)==3&&gate==2);
    CHECK(dl_stream_scene_door_target(&adapter,1,4,&gate)==0&&gate==0);
    gates[0].door_entity_id=3;CHECK(dl_stream_scene_init(&adapter,&base,&parsed)==DL_STREAM_SCENE_ARGUMENT);
    return 0;
}
static int staged_publication(void){
    CHECK(initialize()==0);CHECK(load(1)==0);
    CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *published=dl_stream_scene_level(&scene);unsigned revision=scene.revision;
    int models_before=published->model_count,contacts_before=published->collider_count;
    uint64_t key=published->meshes[published->models[models_before-1].mesh].stream_key;
    CHECK(key==((UINT64_C(1)<<40)|(UINT64_C(1)<<8)));
    CHECK(load(2)==0);CHECK(dl_stream_scene_prepare(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_level(&scene)==published&&scene.revision==revision);
    CHECK(published->model_count==models_before&&published->collider_count==contacts_before);
    const DlLevel *pending=dl_stream_scene_pending(&scene);
    CHECK(pending!=published&&pending->model_count>models_before);
    dl_stream_scene_publish(&scene);
    CHECK(dl_stream_scene_level(&scene)==pending&&scene.revision==revision+1);
    bool found=false;
    for(int i=0;i<pending->model_count;++i)if(pending->meshes[pending->models[i].mesh].stream_key==key)found=true;
    CHECK(found);return 0;
}
static int stable_key_material_bits(void){
    const uint32_t first=UINT32_C(0x02000001),second=UINT32_C(0x01000001);
    DlStreamMaterial wide[]={ {.id=first,.color={128,192,255,255}}, {.id=second,.color={128,192,255,255}} };
    DlStreamDefinition def=definition;def.materials=wide;def.material_count=2;
    DlLevel base=bootstrap;base.stream=&def;
    prepare();put32(package+FIRST+148,first);put32(package+SECOND+148,second);
    put32(package+64+16,crc32(package+FIRST,CHUNK_BYTES));put32(package+128+16,crc32(package+SECOND,CHUNK_BYTES));
    put32(package+28,0);put32(package+28,crc32(package,DIRECTORY));
    CHECK(dl_mission_open(package,DIRECTORY,FILE_BYTES,&mission)==DL_MISSION_OK);
    for(unsigned i=0;i<2;++i)slots[i]=(DlRegionSlot){.buffer=buffers[i],.capacity=CHUNK_BYTES};
    CHECK(dl_world_stream_init(&stream,&mission,slots,2,read_memory,NULL)==DL_STREAM_OK);
    CHECK(dl_stream_scene_init(&scene,&base,&mission)==DL_STREAM_SCENE_OK);
    CHECK(load(1)==0&&load(2)==0&&dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *level=dl_stream_scene_level(&scene);uint64_t keys[2]={0};unsigned count=0;
    for(int i=0;i<level->model_count;++i)if(level->models[i].role==DL_MODEL_STATIC){
        CHECK(count<2);keys[count++]=level->meshes[level->models[i].mesh].stream_key;
    }
    CHECK(count==2&&keys[0]!=keys[1]);
    CHECK(keys[0]==((UINT64_C(1)<<40)|((uint64_t)first<<8)));
    CHECK(keys[1]==((UINT64_C(2)<<40)|((uint64_t)second<<8)));
    return 0;
}
static int incremental_assembly(void){
    CHECK(initialize()==0);CHECK(load(1)==0);
    CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *published=dl_stream_scene_level(&scene);unsigned revision=scene.revision;
    int models_before=published->model_count,contacts_before=published->collider_count;
    CHECK(load(2)==0);
    DlGame guards={.enemy_count=1};guards.enemies[0].position=(DlVec3){2,0,0};
    CHECK(dl_stream_scene_prepare_begin(&scene,&stream,&guards)==DL_STREAM_SCENE_OK);
    CHECK(slots[0].cpu_pins==1&&slots[1].cpu_pins==1&&dl_stream_scene_pending(&scene)==NULL);
    bool ready=true;
    CHECK(dl_stream_scene_prepare_step(&scene,0,&ready)==DL_STREAM_SCENE_OK&&!ready);
    CHECK(scene.assembly.record==0&&scene.assembly.phase==0);
    dl_stream_scene_publish(&scene);CHECK(scene.revision==revision&&dl_stream_scene_level(&scene)==published);
    CHECK(dl_stream_scene_prepare_step(&scene,1,&ready)==DL_STREAM_SCENE_OK&&!ready);
    CHECK(dl_world_stream_retire(&stream,handles[1])==DL_STREAM_OK);
    CHECK(slots[1].state==DL_STREAM_RETIRING&&slots[1].cpu_pins==1);
    dl_stream_scene_prepare_cancel(&scene);
    CHECK(slots[0].cpu_pins==0&&slots[1].cpu_pins==0&&slots[1].state==DL_STREAM_FREE);
    CHECK(!scene.assembly.active&&dl_stream_scene_pending(&scene)==NULL&&scene.revision==revision);
    CHECK(dl_stream_scene_prepare_step(&scene,1,&ready)==DL_STREAM_SCENE_NOT_READY);
    CHECK(load(2)==0);
    CHECK(dl_stream_scene_prepare_begin(&scene,&stream,&guards)==DL_STREAM_SCENE_OK);
    /* Guard presentation uses the begin snapshot, not a mutable game pointer. */
    guards.enemies[0].position=(DlVec3){100,100,100};
    unsigned steps=0;
    do{
        CHECK(dl_stream_scene_prepare_step(&scene,1,&ready)==DL_STREAM_SCENE_OK);
        CHECK(++steps<100&&scene.revision==revision&&dl_stream_scene_level(&scene)==published);
        CHECK(published->model_count==models_before&&published->collider_count==contacts_before);
    }while(!ready);
    CHECK(steps>15&&slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    const DlLevel *pending=dl_stream_scene_pending(&scene);CHECK(pending);
    bool guard=false;for(int i=0;i<pending->model_count;++i)if(pending->models[i].role==DL_MODEL_GUARD)guard=true;
    CHECK(guard);dl_stream_scene_publish(&scene);CHECK(scene.revision==revision+1&&dl_stream_scene_level(&scene)==pending);
    published=pending;revision=scene.revision;
    CHECK(dl_stream_scene_prepare_begin(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_prepare_step(&scene,2,&ready)==DL_STREAM_SCENE_OK&&!ready);
    /* Lazy corruption after some valid records fails without publishing or
     * leaking the long-lived borrows (real slots remain immutable in service). */
    put32(buffers[0]+148,0);
    CHECK(dl_stream_scene_prepare_step(&scene,1,&ready)==DL_STREAM_SCENE_CORRUPT&&!ready);
    CHECK(!scene.assembly.active&&slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    CHECK(scene.revision==revision&&dl_stream_scene_level(&scene)==published&&dl_stream_scene_pending(&scene)==NULL);
    put32(buffers[0]+148,1);return 0;
}
static int pending_guard_membership(void){
    CHECK(initialize()==0);CHECK(load(1)==0);
    DlGame game={.enemy_count=1};game.enemies[0].position=(DlVec3){-2,0,0};
    CHECK(!dl_stream_scene_pending_guards_match(&scene,&game));
    CHECK(dl_stream_scene_prepare(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
    CHECK(model_by_id(dl_stream_scene_pending(&scene),"guard")&&dl_stream_scene_pending_guards_match(&scene,&game));
    game.enemies[0].position=(DlVec3){-1,0,1};CHECK(dl_stream_scene_pending_guards_match(&scene,&game));
    game.enemies[0].position=(DlVec3){100,100,100};CHECK(!dl_stream_scene_pending_guards_match(&scene,&game));
    game.enemy_count=0;CHECK(!dl_stream_scene_pending_guards_match(&scene,&game));game.enemy_count=1;
    dl_stream_scene_prepare_cancel(&scene);
    game.enemies[0].position=(DlVec3){2,0,0};
    CHECK(dl_stream_scene_prepare(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
    CHECK(!model_by_id(dl_stream_scene_pending(&scene),"guard")&&dl_stream_scene_pending_guards_match(&scene,&game));
    game.enemies[0].position=(DlVec3){-2,0,0};CHECK(!dl_stream_scene_pending_guards_match(&scene,&game));
    dl_stream_scene_prepare_cancel(&scene);
    /* An enemy without an authored visual row needs no extra model capacity. */
    const uint32_t visible_links[]={2,4};DlStreamDefinition def=definition;def.model_entity_ids=visible_links;
    DlLevel base=bootstrap;base.models=models+1;base.model_count=2;base.stream=&def;static DlStreamScene invisible;
    CHECK(dl_stream_scene_init(&invisible,&base,&mission)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_prepare(&invisible,&stream,&game)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_pending_guards_match(&invisible,&game));
    game.enemies[0].position=(DlVec3){100,100,100};CHECK(dl_stream_scene_pending_guards_match(&invisible,&game));
    dl_stream_scene_prepare_cancel(&invisible);CHECK(slots[0].cpu_pins==0);return 0;
}
static int masked_preparation(void){
    CHECK(initialize()==0);CHECK(load(1)==0);CHECK(dl_stream_scene_refresh(&scene,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *published=dl_stream_scene_level(&scene);unsigned revision=scene.revision;
    DlGame game;dl_game_init(&game,published);game.loot_value=25;game.elapsed=7;game.door_open=true;
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,2)==DL_STREAM_SCENE_NOT_READY);
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,4)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,UINT32_C(0x80000000))==DL_STREAM_SCENE_ARGUMENT);
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,UINT32_MAX)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(!scene.assembly.active&&!scene.pending_ready&&slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    CHECK(scene.revision==revision&&dl_stream_scene_level(&scene)==published);
    CHECK(dl_stream_scene_prepare_begin_mask(NULL,&stream,&game,1)==DL_STREAM_SCENE_ARGUMENT);
    CHECK(load(2)==0);CHECK(dl_stream_scene_refresh(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
    published=dl_stream_scene_level(&scene);revision=scene.revision;int full_models=published->model_count;
    CHECK(model_by_id(published,"guard")&&model_by_id(published,"loot")&&model_by_id(published,"door"));
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,1)==DL_STREAM_SCENE_OK);
    CHECK(scene.assembly.borrowed==1&&scene.assembly.resident_mask==1);
    CHECK(slots[handles[0].slot].cpu_pins==1&&slots[handles[1].slot].cpu_pins==0);
    bool ready=false;CHECK(dl_stream_scene_prepare_step(&scene,2,&ready)==DL_STREAM_SCENE_OK&&!ready);
    dl_stream_scene_prepare_cancel(&scene);
    CHECK(slots[0].cpu_pins==0&&slots[1].cpu_pins==0&&scene.revision==revision&&dl_stream_scene_level(&scene)==published);
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,1)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_prepare_step(&scene,SIZE_MAX,&ready)==DL_STREAM_SCENE_OK&&ready);
    const DlLevel *pending=dl_stream_scene_pending(&scene);
    CHECK(pending&&pending!=published&&model_by_id(pending,"door")&&!model_by_id(pending,"guard")&&!model_by_id(pending,"loot"));
    CHECK(pending->objects==objects&&pending->enemies==enemies&&pending->model_count==2&&pending->collider_count==2);
    CHECK(scene.banks[scene.active_bank^1u].resident_count==1&&scene.banks[scene.active_bank^1u].resident_ids[0]==1);
    CHECK(dl_stream_scene_pending_guards_match(&scene,&game));
    CHECK(slots[0].state==DL_STREAM_RESIDENT&&slots[1].state==DL_STREAM_RESIDENT&&slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    CHECK(game.elapsed==7&&game.loot_value==25&&game.door_open&&dl_stream_scene_level(&scene)==published);
    dl_stream_scene_publish(&scene);CHECK(scene.revision==revision+1&&dl_stream_scene_region_resident(&scene,1)&&!dl_stream_scene_region_resident(&scene,2));
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,2)==DL_STREAM_SCENE_OK);
    CHECK(slots[handles[0].slot].cpu_pins==0&&slots[handles[1].slot].cpu_pins==1);
    CHECK(dl_stream_scene_prepare_step(&scene,SIZE_MAX,&ready)==DL_STREAM_SCENE_OK&&ready);
    pending=dl_stream_scene_pending(&scene);
    CHECK(pending&&model_by_id(pending,"door")&&model_by_id(pending,"guard")&&model_by_id(pending,"loot"));
    CHECK(scene.banks[scene.active_bank^1u].resident_count==1&&scene.banks[scene.active_bank^1u].resident_ids[0]==2);
    CHECK(dl_stream_scene_pending_guards_match(&scene,&game));dl_stream_scene_prepare_cancel(&scene);
    CHECK(dl_stream_scene_prepare_begin_mask(&scene,&stream,&game,0)==DL_STREAM_SCENE_OK);
    CHECK(scene.assembly.borrowed==0&&slots[0].cpu_pins==0&&slots[1].cpu_pins==0);
    CHECK(dl_stream_scene_prepare_step(&scene,SIZE_MAX,&ready)==DL_STREAM_SCENE_OK&&ready);
    pending=dl_stream_scene_pending(&scene);
    CHECK(pending&&pending->model_count==1&&model_by_id(pending,"door")&&pending->collider_count==2);
    CHECK(scene.banks[scene.active_bank^1u].resident_count==0&&dl_stream_scene_pending_guards_match(&scene,&game));
    dl_stream_scene_publish(&scene);
    /* Compatibility begin still includes both physical slots, independent of
     * the smaller published view. Its explicit lifetime remains unchanged. */
    CHECK(dl_stream_scene_prepare_begin(&scene,&stream,&game)==DL_STREAM_SCENE_OK);
    CHECK(scene.assembly.resident_mask==3&&scene.assembly.borrowed==2&&slots[0].cpu_pins==1&&slots[1].cpu_pins==1);
    CHECK(dl_stream_scene_prepare_step(&scene,SIZE_MAX,&ready)==DL_STREAM_SCENE_OK&&ready);
    pending=dl_stream_scene_pending(&scene);CHECK(pending&&pending->model_count==full_models);
    CHECK(model_by_id(pending,"door")&&model_by_id(pending,"guard")&&model_by_id(pending,"loot"));
    dl_stream_scene_publish(&scene);CHECK(dl_stream_scene_region_resident(&scene,1)&&dl_stream_scene_region_resident(&scene,2));
    CHECK(slots[0].cpu_pins==0&&slots[1].cpu_pins==0);return 0;
}
static int authored_door_barrier_interaction(void){
    CHECK(initialize()==0);CHECK(load(1)==0);
    DlStreamPolicyRegion policy[]={
        {.activation_minimum={-4,-1,-2},.activation_maximum={0,3,2},.working_mask=1},
        {.activation_minimum={0,-1,-2},.activation_maximum={4,3,2},.working_mask=2}};
    DlStreamTransition gates[]={
        {.from_region=1,.to_region=2,.minimum={-.1f,-1,-2},.maximum={.1f,3,2},.door_entity_id=2},
        {.from_region=2,.to_region=1,.minimum={-.1f,-1,-2},.maximum={.1f,3,2},.door_entity_id=2}};
    DlStreamDefinition def=definition;def.policy=DL_STREAM_POLICY_AUTHORED;def.policy_regions=policy;
    def.transitions=gates;def.transition_count=2;def.max_resident_regions=2;
    DlLevel base=bootstrap;base.stream=&def;static DlStreamScene adapter;
    CHECK(dl_stream_scene_init(&adapter,&base,&mission)==DL_STREAM_SCENE_OK);
    CHECK(dl_stream_scene_refresh(&adapter,&stream,NULL)==DL_STREAM_SCENE_OK);
    const DlLevel *level=dl_stream_scene_level(&adapter);int barriers=0;
    for(int i=0;i<level->collider_count;++i)if(!level->colliders[i].door&&level->collider_objects[i]==0)++barriers;
    CHECK(barriers==1&&level->collider_count==2);
    DlLevel uncached=*level;uncached.query_cache=NULL;
    for(unsigned mode=0;mode<2;++mode){DlGame game={.level=mode?&uncached:level,.player={-1.1f,0,0},
            .door_open=true,.open_objects=1,.enemy_count=1};
        CHECK(!dl_line_of_sight(&game,dl_player_eye(&game),objects[0].position));
        CHECK(!dl_position_clear(&game,(DlVec3){0,0,0},.18f,1.65f));
        dl_game_update(&game,&(DlInput){.use=true},1.0f/60);
        CHECK(!game.door_open&&game.object_revision==1&&game.event_count>0&&game.events[0].kind==DL_EVENT_DOOR);
        dl_game_update(&game,&(DlInput){.use=true},1.0f/60);
        CHECK(game.door_open&&game.object_revision==2);
        CHECK(!dl_line_of_sight(&game,dl_player_eye(&game),objects[0].position));
        CHECK(!dl_position_clear(&game,(DlVec3){0,0,0},.18f,1.65f));
    }
    /* Once the destination is published the synthetic barrier disappears;
     * the open authored door then permits both movement and ordinary LOS. */
    CHECK(load(2)==0&&dl_stream_scene_refresh(&adapter,&stream,NULL)==DL_STREAM_SCENE_OK);
    level=dl_stream_scene_level(&adapter);DlGame game={.level=level,.player={-1.1f,0,0},.door_open=true,.open_objects=1,.enemy_count=1};
    CHECK(level->collider_count==2&&dl_line_of_sight(&game,dl_player_eye(&game),objects[0].position));
    CHECK(dl_position_clear(&game,(DlVec3){0,0,0},.18f,1.65f));return 0;
}
static int equal_geometry(const DlLevel *a,const DlLevel *b){
    CHECK(a&&b&&a->mesh_count==b->mesh_count&&a->model_count==b->model_count&&a->collider_count==b->collider_count);
    for(int i=0;i<a->mesh_count;++i){const DlMesh *x=&a->meshes[i],*y=&b->meshes[i];
        CHECK(x->vertex_count==y->vertex_count&&x->index_count==y->index_count&&x->stream_key==y->stream_key);
        CHECK(memcmp(x->vertices,y->vertices,(size_t)x->vertex_count*sizeof *x->vertices)==0);
        CHECK(memcmp(x->indices,y->indices,(size_t)x->index_count*sizeof *x->indices)==0);
        CHECK((x->uvs!=NULL)==(y->uvs!=NULL)&&(x->normals!=NULL)==(y->normals!=NULL));
        if(x->uvs)CHECK(memcmp(x->uvs,y->uvs,(size_t)x->vertex_count*sizeof *x->uvs)==0);
        if(x->normals)CHECK(memcmp(x->normals,y->normals,(size_t)x->vertex_count*sizeof *x->normals)==0);
    }
    for(int i=0;i<a->model_count;++i){const DlModelInstance *x=&a->models[i],*y=&b->models[i];
        CHECK(x->mesh==y->mesh&&x->role==y->role&&x->texture==y->texture&&x->double_sided==y->double_sided);
        CHECK(memcmp(&x->position,&y->position,sizeof x->position)==0&&memcmp(x->color,y->color,4)==0);
        for(int state=0;state<2;++state){CHECK((x->baked_colors[state]!=NULL)==(y->baked_colors[state]!=NULL));
            if(x->baked_colors[state])CHECK(memcmp(x->baked_colors[state],y->baked_colors[state],(size_t)a->meshes[x->mesh].index_count/3*24)==0);}
    }
    CHECK(dl_game_query_cache_ready(a)&&dl_game_query_cache_ready(b));return 0;
}
static int contiguous_validated_fill(void){
    enum {TRIANGLES=5,TRIANGLE_OFFSET=112,COLLIDER_OFFSET=752,ENTITY_OFFSET=784,CHUNK=792,SECOND_OFFSET=1072,BYTES=1864};
    static uint8_t bytes[2][BYTES],data[2][2][CHUNK];static DlStreamScene adapters[2];
    DlMission parsed[2];DlWorldStream managers[2];DlRegionSlot loaded[2][2]={0};
    DlStreamDefinition defs[2];DlLevel bases[2];unsigned steps[2]={0};
    const uint8_t flags[]={3,3,1,2,2},orders[2][TRIANGLES]={{0,1,2,3,4},{0,2,3,1,4}};
    const DlTexture texture={.path="rom:/test.sprite",.width=32,.height=32};
    DlStreamMaterial palette[]={materials[0],{.id=2,.color={32,64,96,255},.texture_id=1}};
    prepare();
    for(unsigned variant=0;variant<2;++variant){memcpy(bytes[variant],package,DIRECTORY);put32(bytes[variant]+8,BYTES);
        for(unsigned region=0;region<2;++region){uint8_t *chunk=bytes[variant]+(region?SECOND_OFFSET:FIRST);
            memcpy(chunk,"DLR1",4);put16(chunk+4,4);put16(chunk+6,32);put32(chunk+8,CHUNK);put32(chunk+12,3);
            if(region)putfloat(chunk+16,2);
            section(chunk+32,1,TRIANGLE_OFFSET,TRIANGLES,128);section(chunk+56,2,COLLIDER_OFFSET,1,32);
            section(chunk+80,3,ENTITY_OFFSET,2,4);
            memcpy(chunk+COLLIDER_OFFSET,package+(region?SECOND:FIRST)+160,32);
            memcpy(chunk+ENTITY_OFFSET,package+(region?SECOND:FIRST)+192,8);
            for(unsigned record=0;record<TRIANGLES;++record){unsigned face=orders[variant][record];uint8_t *row=chunk+TRIANGLE_OFFSET+record*128;
                for(unsigned corner=0;corner<3;++corner){putfloat(row+corner*12,-3+(float)face*.25f+(float)corner*.125f);
                    putfloat(row+corner*12+4,(float)corner*.1f);putfloat(row+corner*12+8,(float)face*.2f);
                    putfloat(row+44+corner*8,-(float)face-.25f);putfloat(row+48+corner*8,(float)corner+.75f);row[69+corner*3]=127;}
                put32(row+36,face==2?2:1);put32(row+40,region*TRIANGLES+face+1);row[77]=flags[face];
                for(unsigned state=0;state<2;++state)for(unsigned corner=0;corner<6;++corner){uint8_t *color=row+80+state*24+corner*4;
                    color[0]=(uint8_t)(32+face);color[1]=(uint8_t)(64+state);color[2]=(uint8_t)(96+corner);color[3]=255;}
            }
            uint8_t *directory=bytes[variant]+64+region*64;put32(directory+4,region?SECOND_OFFSET:FIRST);
            put32(directory+8,CHUNK);put32(directory+12,CHUNK);put32(directory+16,crc32(chunk,CHUNK));
        }
        put32(bytes[variant]+28,0);put32(bytes[variant]+28,crc32(bytes[variant],DIRECTORY));
        CHECK(dl_mission_open(bytes[variant],DIRECTORY,BYTES,&parsed[variant])==DL_MISSION_OK);
        defs[variant]=definition;defs[variant].file_bytes=BYTES;defs[variant].max_region_bytes=CHUNK;
        defs[variant].materials=palette;defs[variant].material_count=2;
        bases[variant]=bootstrap;bases[variant].stream=&defs[variant];bases[variant].textures=&texture;bases[variant].texture_count=1;
        CHECK(dl_stream_scene_init(&adapters[variant],&bases[variant],&parsed[variant])==DL_STREAM_SCENE_OK);
        for(unsigned slot=0;slot<2;++slot)loaded[variant][slot]=(DlRegionSlot){.buffer=data[variant][slot],.capacity=CHUNK};
        LargeReader reader={bytes[variant],BYTES};
        CHECK(dl_world_stream_init(&managers[variant],&parsed[variant],loaded[variant],2,read_large,&reader)==DL_STREAM_OK);
        for(unsigned region=1;region<=2;++region){DlRegionHandle handle;
            CHECK(dl_world_stream_request(&managers[variant],region,&handle)==DL_STREAM_OK);}
        while(loaded[variant][0].state!=DL_STREAM_RESIDENT||loaded[variant][1].state!=DL_STREAM_RESIDENT)dl_world_stream_pump(&managers[variant],256);
        CHECK(dl_stream_scene_prepare_begin(&adapters[variant],&managers[variant],NULL)==DL_STREAM_SCENE_OK);
        bool ready=false,fallback=false;
        while(!ready){CHECK(dl_stream_scene_prepare_step(&adapters[variant],1,&ready)==DL_STREAM_SCENE_OK);
            if(adapters[variant].assembly.active&&!adapters[variant].assembly.contiguous_groups)fallback=true;
            CHECK(++steps[variant]<256&&adapters[variant].revision==0);}
        CHECK(fallback==(variant!=0)&&loaded[variant][0].cpu_pins==0&&loaded[variant][1].cpu_pins==0);
    }
    CHECK(steps[1]==steps[0]+TRIANGLES*2);
    CHECK(equal_geometry(dl_stream_scene_pending(&adapters[0]),dl_stream_scene_pending(&adapters[1]))==0);
    for(unsigned variant=0;variant<2;++variant)dl_stream_scene_publish(&adapters[variant]);
    /* A later malformed record still aborts during SCAN and preserves the
     * active geometry, including the v4 alpha validation in the direct path. */
    const DlLevel *published=dl_stream_scene_level(&adapters[0]);unsigned revision=adapters[0].revision;
    CHECK(dl_stream_scene_prepare_begin(&adapters[0],&managers[0],NULL)==DL_STREAM_SCENE_OK);bool ready=false;
    CHECK(dl_stream_scene_prepare_step(&adapters[0],3,&ready)==DL_STREAM_SCENE_OK&&!ready);
    data[0][0][TRIANGLE_OFFSET+128+83]=0;
    CHECK(dl_stream_scene_prepare_step(&adapters[0],1,&ready)==DL_STREAM_SCENE_CORRUPT&&!ready);
    CHECK(dl_stream_scene_level(&adapters[0])==published&&adapters[0].revision==revision&&!dl_stream_scene_pending(&adapters[0]));
    CHECK(loaded[0][0].cpu_pins==0&&loaded[0][1].cpu_pins==0);return 0;
}
int main(int argc,char **argv){
    if(working_views()||atomic_failures()||initialization_limits()||refresh_capacity()||textured_v2_scene()||authored_five_region_floor_views()||authored_swept_gate_crossings()||authored_door_target()||staged_publication()||stable_key_material_bits()||incremental_assembly()||pending_guard_membership()||masked_preparation()||authored_door_barrier_interaction()||contiguous_validated_fill())return 1;
#ifdef DL_STREAM_SCENE_INTEGRATION
    if(argc>1&&cooked_fixture(argv[1]))return 1;
#else
    (void)argc;(void)argv;
#endif
    puts("bounded streamed scene views, barriers, stable gameplay links and atomic refresh passed");return 0;
}
