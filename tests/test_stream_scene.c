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
int main(int argc,char **argv){
    if(working_views()||atomic_failures()||initialization_limits()||refresh_capacity()||textured_v2_scene())return 1;
#ifdef DL_STREAM_SCENE_INTEGRATION
    if(argc>1&&cooked_fixture(argv[1]))return 1;
#else
    (void)argc;(void)argv;
#endif
    puts("bounded streamed scene views, barriers, stable gameplay links and atomic refresh passed");return 0;
}
