#include "mission.h"
#include <stdio.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
enum { DIRECTORY_BYTES=248, CHUNK_OFFSET=256, CHUNK_BYTES=200, SECOND_OFFSET=464,
    SECOND_BYTES=68, FILE_BYTES=532 };
static uint8_t package[FILE_BYTES];
static void put16(uint8_t *p,uint16_t n){p[0]=(uint8_t)(n>>8);p[1]=(uint8_t)n;}
static void put32(uint8_t *p,uint32_t n){p[0]=(uint8_t)(n>>24);p[1]=(uint8_t)(n>>16);p[2]=(uint8_t)(n>>8);p[3]=(uint8_t)n;}
static void putfloat(uint8_t *p,float n){uint32_t bits;memcpy(&bits,&n,4);put32(p,bits);}
/* Independent bit-at-a-time fixture CRC; directory checksum is zeroed first. */
static uint32_t fixture_crc(const uint8_t *p,size_t bytes){
    uint32_t crc=UINT32_MAX;
    while(bytes--){crc^=*p++;for(unsigned bit=0;bit<8;++bit)crc=(crc>>1)^((crc&1)?0xedb88320u:0);}
    return crc^UINT32_MAX;
}
static void directory_checksum(void){put32(package+28,0);put32(package+28,fixture_crc(package,DIRECTORY_BYTES));}
static void section(uint8_t *row,unsigned kind,unsigned offset,unsigned bytes,unsigned count,unsigned stride){
    put16(row,(uint16_t)kind);put32(row+4,offset);put32(row+8,bytes);put32(row+12,count);put32(row+16,stride);
}
static void prepare(void){
    memset(package,0,sizeof package);
    memcpy(package,"DLM1",4);put16(package+4,1);put16(package+6,64);
    put32(package+8,FILE_BYTES);put32(package+12,2);put32(package+16,3);
    put32(package+20,DIRECTORY_BYTES);put32(package+24,1);
    for(unsigned i=0;i<32;++i)package[32+i]=(uint8_t)(i*7);
    uint8_t *chunk=package+CHUNK_OFFSET;
    memcpy(chunk,"DLR1",4);put16(chunk+4,1);put16(chunk+6,32);put32(chunk+8,CHUNK_BYTES);put32(chunk+12,3);
    putfloat(chunk+16,100);putfloat(chunk+20,-2);putfloat(chunk+24,3);
    section(chunk+32,1,112,44,1,44);section(chunk+56,2,160,32,1,32);section(chunk+80,3,192,8,2,4);
    for(unsigned i=0;i<9;++i)putfloat(chunk+112+i*4,(float)i*0.25f);
    put32(chunk+148,7);put32(chunk+152,12);
    putfloat(chunk+160,2);putfloat(chunk+164,1);putfloat(chunk+168,3);
    putfloat(chunk+172,0.5f);putfloat(chunk+176,1.5f);putfloat(chunk+180,0.25f);
    putfloat(chunk+184,0.25f);put32(chunk+188,1);put32(chunk+192,1);put32(chunk+196,2);
    uint8_t *second=package+SECOND_OFFSET;
    memcpy(second,"DLR1",4);put16(second+4,1);put16(second+6,32);put32(second+8,SECOND_BYTES);put32(second+12,1);
    section(second+32,3,64,4,1,4);put32(second+64,3);
    for(unsigned i=0;i<2;++i){
        uint8_t *region=package+64+i*64;
        put32(region,i+1);put32(region+4,i?SECOND_OFFSET:CHUNK_OFFSET);
        put32(region+8,i?SECOND_BYTES:CHUNK_BYTES);put32(region+12,i?SECOND_BYTES:CHUNK_BYTES);
        put32(region+16,fixture_crc(package+(i?SECOND_OFFSET:CHUNK_OFFSET),i?SECOND_BYTES:CHUNK_BYTES));
        put32(region+20,i);put32(region+24,1);
        putfloat(region+32,(float)i*10);putfloat(region+36,-2);putfloat(region+40,-3);
        putfloat(region+44,(float)(i+1)*10);putfloat(region+48,4);putfloat(region+52,5);
    }
    for(unsigned i=0;i<3;++i){
        uint8_t *entity=package+192+i*16;
        put32(entity,i+1);put32(entity+4,i==2?2:1);put16(entity+8,i==0?2:i==1?3:0);
    }
    put32(package+240,2);put32(package+244,1);directory_checksum();
}
static DlMissionStatus open(DlMission *mission){return dl_mission_open(package,DIRECTORY_BYTES,FILE_BYTES,mission);}
static int valid_package(void){
    prepare();DlMission mission;CHECK(open(&mission)==DL_MISSION_OK);
    CHECK(mission.directory==package&&mission.directory_bytes==DIRECTORY_BYTES);
    CHECK(mission.region_count==2&&mission.entity_count==3&&mission.entry_region_id==1);
    CHECK(mission.file_bytes==FILE_BYTES&&mission.identity[31]==(uint8_t)(31*7));
    DlMissionRegion region;CHECK(dl_mission_region(&mission,1,&region)==DL_MISSION_OK);
    CHECK(region.id==1&&region.chunk_offset==CHUNK_OFFSET&&region.chunk_bytes==CHUNK_BYTES&&region.decoded_bytes==CHUNK_BYTES);
    CHECK(region.minimum.x==0&&region.maximum.x==10&&region.minimum.y==-2);
    CHECK(region.chunk_crc32==fixture_crc(package+CHUNK_OFFSET,CHUNK_BYTES));
    uint32_t id=0;CHECK(dl_mission_dependency(&mission,1,0,&id)==DL_MISSION_OK&&id==2);
    CHECK(dl_mission_dependency(&mission,2,0,&id)==DL_MISSION_OK&&id==1);
    DlMissionEntity entity;CHECK(dl_mission_entity(&mission,1,&entity)==DL_MISSION_OK);
    CHECK(entity.home_region_id==1&&entity.kind==DL_MISSION_ENTITY_DOOR);
    CHECK(dl_mission_entity(&mission,3,&entity)==DL_MISSION_OK&&entity.home_region_id==2);
    DlMissionChunk chunk;CHECK(dl_mission_chunk_open(package+CHUNK_OFFSET,CHUNK_BYTES,&chunk)==DL_MISSION_OK);
    CHECK(chunk.origin.x==100&&chunk.origin.y==-2&&chunk.section_count==3);
    DlMissionSection descriptor;CHECK(dl_mission_section(&chunk,DL_MISSION_SECTION_TRIANGLES,&descriptor)==DL_MISSION_OK);
    CHECK(descriptor.offset==112&&descriptor.bytes==44&&descriptor.count==1);
    DlMissionTriangle triangle;CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_OK);
    CHECK(triangle.positions[2].z==2&&triangle.material_id==7&&triangle.surface_id==12);
    CHECK(triangle.attribute_flags==0&&triangle.uvs[0]==0&&triangle.normals[0]==0&&chunk.version==1);
    DlMissionCollider collider;CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_OK);
    CHECK(collider.center.x==2&&collider.half_size.y==1.5f&&collider.yaw==0.25f&&collider.entity_id==1);
    CHECK(dl_mission_entity_id(&chunk,&mission,1,1,&id)==DL_MISSION_OK&&id==2);
    CHECK(dl_mission_chunk_open(package+SECOND_OFFSET,SECOND_BYTES,&chunk)==DL_MISSION_OK);
    CHECK(dl_mission_section(&chunk,DL_MISSION_SECTION_TRIANGLES,&descriptor)==DL_MISSION_NOT_FOUND);
    CHECK(dl_mission_entity_id(&chunk,&mission,2,0,&id)==DL_MISSION_OK&&id==3);
    CHECK(dl_mission_entity_id(&chunk,&mission,1,0,&id)==DL_MISSION_REFERENCE);
    return 0;
}
static int crc_cases(void){
    const char *known="123456789";
    CHECK((dl_mission_crc32_update(UINT32_MAX,known,9)^UINT32_MAX)==0xcbf43926u);
    uint32_t crc=UINT32_MAX;
    for(unsigned i=0;i<9;++i)crc=dl_mission_crc32_update(crc,known+i,1);
    CHECK((crc^UINT32_MAX)==0xcbf43926u);
    CHECK(dl_mission_crc32_update(crc,NULL,0)==crc);
    return 0;
}
static int empty_entity_directory(void){
    memset(package,0,sizeof package);memcpy(package,"DLM1",4);
    put16(package+4,1);put16(package+6,64);put32(package+8,192);put32(package+12,1);
    put32(package+20,128);put32(package+24,1);
    put32(package+64,1);put32(package+68,128);put32(package+72,64);put32(package+76,64);
    putfloat(package+108,1);putfloat(package+112,1);putfloat(package+116,1);
    uint8_t *p=package+128;memcpy(p,"DLR1",4);put16(p+4,1);put16(p+6,32);
    put32(p+8,64);put32(p+12,1);section(p+32,1,64,0,0,44);
    put32(package+80,fixture_crc(p,64));put32(package+28,fixture_crc(package,128));
    DlMission mission;CHECK(dl_mission_open(package,128,192,&mission)==DL_MISSION_OK);
    CHECK(mission.entity_count==0&&mission.dependency_count==0);
    DlMissionEntity entity;CHECK(dl_mission_entity(&mission,1,&entity)==DL_MISSION_REFERENCE);
    uint32_t id;CHECK(dl_mission_dependency(&mission,1,0,&id)==DL_MISSION_REFERENCE);
    DlMissionChunk chunk;CHECK(dl_mission_chunk_open(p,64,&chunk)==DL_MISSION_OK);
    DlMissionTriangle triangle;CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_REFERENCE);
    return 0;
}
static int directory_errors(void){
    struct Case{unsigned offset;uint32_t value;DlMissionStatus status;};
    static const struct Case cases[]={
        {8,FILE_BYTES+1,DL_MISSION_LAYOUT},{12,UINT32_MAX,DL_MISSION_TRUNCATED},
        {16,UINT32_MAX,DL_MISSION_TRUNCATED},{20,DIRECTORY_BYTES-4,DL_MISSION_LAYOUT},
        {24,0,DL_MISSION_REFERENCE},{24,3,DL_MISSION_REFERENCE},
        {64,2,DL_MISSION_REFERENCE},{68,255,DL_MISSION_LAYOUT},{68,240,DL_MISSION_LAYOUT},
        {72,UINT32_MAX,DL_MISSION_LAYOUT},{76,CHUNK_BYTES+1,DL_MISSION_LAYOUT},
        {84,1,DL_MISSION_LAYOUT},{88,UINT32_MAX,DL_MISSION_LAYOUT},{92,1,DL_MISSION_LAYOUT},
        {96,0x7fc00000u,DL_MISSION_VALUE},{100,0x7f800000u,DL_MISSION_VALUE},
        {108,0xbf800000u,DL_MISSION_VALUE},{108,0,DL_MISSION_VALUE},{120,1,DL_MISSION_LAYOUT},
        {132,448,DL_MISSION_LAYOUT},{136,UINT32_MAX,DL_MISSION_LAYOUT},
        {148,0,DL_MISSION_LAYOUT},{152,0,DL_MISSION_LAYOUT},
        {192,0,DL_MISSION_REFERENCE},{196,0,DL_MISSION_REFERENCE},{196,3,DL_MISSION_REFERENCE},
        {200,0x00050000u,DL_MISSION_VALUE},{200,0x00020001u,DL_MISSION_LAYOUT},
        {204,1,DL_MISSION_LAYOUT},{240,0,DL_MISSION_REFERENCE},{240,1,DL_MISSION_REFERENCE},
        {244,3,DL_MISSION_REFERENCE}
    };
    for(unsigned i=0;i<sizeof cases/sizeof cases[0];++i){
        prepare();put32(package+cases[i].offset,cases[i].value);directory_checksum();DlMission mission;
        DlMissionStatus status=open(&mission);
        if(status!=cases[i].status){fprintf(stderr,"directory offset=%u got=%s expected=%s\n",cases[i].offset,
            dl_mission_status_name(status),dl_mission_status_name(cases[i].status));return 1;}
        CHECK(!mission.directory&&mission.region_count==0);
    }
    prepare();DlMission mission;package[32]^=1;CHECK(open(&mission)==DL_MISSION_CHECKSUM);
    prepare();package[0]='X';CHECK(open(&mission)==DL_MISSION_HEADER);
    prepare();put16(package+4,2);CHECK(open(&mission)==DL_MISSION_HEADER);
    prepare();put16(package+6,60);CHECK(open(&mission)==DL_MISSION_HEADER);
    prepare();CHECK(dl_mission_open(package,63,FILE_BYTES,&mission)==DL_MISSION_TRUNCATED);
    for(size_t bytes=0;bytes<DIRECTORY_BYTES;++bytes){
        CHECK(dl_mission_open(package,bytes,FILE_BYTES,&mission)!=DL_MISSION_OK);
        CHECK(!mission.directory);
    }
    CHECK(dl_mission_open(package,DIRECTORY_BYTES,FILE_BYTES-1,&mission)==DL_MISSION_LAYOUT);
    CHECK(dl_mission_open(NULL,DIRECTORY_BYTES,FILE_BYTES,&mission)==DL_MISSION_ARGUMENT);
    CHECK(dl_mission_open(package,DIRECTORY_BYTES,FILE_BYTES,NULL)==DL_MISSION_ARGUMENT);
    /* Three regions would exceed the actual directory; no huge-count walk. */
    prepare();put32(package+12,3);directory_checksum();CHECK(open(&mission)==DL_MISSION_TRUNCATED);
    /* Make room for two dependencies in the first region: distinct, non-self. */
    prepare();put32(package+88,2);put32(package+148,2);put32(package+152,0);
    put32(package+244,2);directory_checksum();CHECK(open(&mission)==DL_MISSION_REFERENCE);
    return 0;
}
static int chunk_errors(void){
    struct Case{unsigned offset;uint32_t value;DlMissionStatus status;};
    static const struct Case cases[]={
        {8,CHUNK_BYTES+1,DL_MISSION_LAYOUT},{12,0,DL_MISSION_LAYOUT},{12,4,DL_MISSION_LAYOUT},
        {16,0x7f800000u,DL_MISSION_VALUE},{28,1,DL_MISSION_LAYOUT},
        {32,0x00010001u,DL_MISSION_LAYOUT},{36,108,DL_MISSION_LAYOUT},{36,96,DL_MISSION_LAYOUT},
        {36,192,DL_MISSION_LAYOUT},{40,UINT32_MAX,DL_MISSION_LAYOUT},{44,UINT32_MAX,DL_MISSION_LAYOUT},
        {48,40,DL_MISSION_LAYOUT},{52,1,DL_MISSION_LAYOUT},{56,0x00010000u,DL_MISSION_LAYOUT},
        {60,144,DL_MISSION_LAYOUT},{80,0x00040000u,DL_MISSION_LAYOUT},
        {84,UINT32_MAX,DL_MISSION_LAYOUT},{88,UINT32_MAX,DL_MISSION_LAYOUT}
    };
    for(unsigned i=0;i<sizeof cases/sizeof cases[0];++i){
        prepare();uint8_t *p=package+CHUNK_OFFSET;put32(p+cases[i].offset,cases[i].value);DlMissionChunk chunk;
        CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==cases[i].status);
        CHECK(!chunk.data&&chunk.section_count==0);
    }
    prepare();DlMissionChunk chunk;CHECK(dl_mission_chunk_open(NULL,CHUNK_BYTES,&chunk)==DL_MISSION_ARGUMENT);
    CHECK(dl_mission_chunk_open(package+CHUNK_OFFSET,31,&chunk)==DL_MISSION_TRUNCATED);
    for(size_t bytes=0;bytes<CHUNK_BYTES;++bytes){
        CHECK(dl_mission_chunk_open(package+CHUNK_OFFSET,bytes,&chunk)!=DL_MISSION_OK);
        CHECK(!chunk.data);
    }
    CHECK(dl_mission_chunk_open(package+CHUNK_OFFSET,CHUNK_BYTES,NULL)==DL_MISSION_ARGUMENT);
    uint8_t *p=package+CHUNK_OFFSET;p[0]='X';CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==DL_MISSION_HEADER);
    prepare();put16(p+4,4);CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==DL_MISSION_HEADER);
    prepare();put16(p+6,24);CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==DL_MISSION_HEADER);
    prepare();put32(p+8,80);CHECK(dl_mission_chunk_open(p,80,&chunk)==DL_MISSION_TRUNCATED);
    /* A legal descriptor order need not imply payload order. */
    prepare();section(p+32,1,144,44,1,44);section(p+56,2,112,32,1,32);
    CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==DL_MISSION_OK);
    return 0;
}
static int lazy_records(void){
    prepare();DlMission mission;CHECK(open(&mission)==DL_MISSION_OK);
    DlMissionChunk chunk;uint8_t *p=package+CHUNK_OFFSET;
    DlMissionTriangle triangle;DlMissionCollider collider;uint32_t id=77;
    CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==DL_MISSION_OK);
    CHECK(dl_mission_triangle(&chunk,1,&triangle)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_collider(&chunk,&mission,1,&collider)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_entity_id(&chunk,&mission,1,2,&id)==DL_MISSION_REFERENCE&&id==77);
    DlMissionRegion region;CHECK(dl_mission_region(&mission,0,&region)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_region(&mission,3,&region)==DL_MISSION_REFERENCE);
    DlMissionEntity entity;CHECK(dl_mission_entity(&mission,0,&entity)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_entity(&mission,4,&entity)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_dependency(&mission,1,1,&id)==DL_MISSION_REFERENCE&&id==77);
    put32(p+112,0x7fc00000u);CHECK(dl_mission_chunk_open(p,CHUNK_BYTES,&chunk)==DL_MISSION_OK);
    memset(&triangle,0x55,sizeof triangle);DlMissionTriangle before=triangle;
    CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_VALUE&&memcmp(&triangle,&before,sizeof triangle)==0);
    prepare();put32(p+148,0);CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_REFERENCE);
    prepare();put32(p+152,0);CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_REFERENCE);
    prepare();put32(p+172,0);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_VALUE);
    prepare();putfloat(p+176,-1);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_VALUE);
    prepare();put32(p+184,0xff800000u);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_VALUE);
    prepare();put32(p+160,0x7fc00000u);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_VALUE);
    prepare();put32(p+188,4);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_REFERENCE);
    prepare();put32(p+188,0);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_OK&&collider.entity_id==0);
    prepare();put32(p+188,3);CHECK(dl_mission_collider(&chunk,&mission,0,&collider)==DL_MISSION_OK&&collider.entity_id==3);
    prepare();put32(p+192,0);CHECK(dl_mission_entity_id(&chunk,&mission,1,0,&id)==DL_MISSION_REFERENCE);
    prepare();put32(p+192,3);CHECK(dl_mission_entity_id(&chunk,&mission,1,0,&id)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_entity_id(&chunk,&mission,0,0,&id)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_triangle(NULL,0,&triangle)==DL_MISSION_ARGUMENT);
    CHECK(dl_mission_triangle(&chunk,0,NULL)==DL_MISSION_ARGUMENT);
    CHECK(strcmp(dl_mission_status_name(DL_MISSION_CHECKSUM),"checksum")==0);
    CHECK(strcmp(dl_mission_status_name((DlMissionStatus)99),"unknown")==0);
    return 0;
}
static void prepare_v2(uint8_t *bytes){
    memset(bytes,0,144);memcpy(bytes,"DLR1",4);put16(bytes+4,2);put16(bytes+6,32);
    put32(bytes+8,144);put32(bytes+12,1);section(bytes+32,1,64,80,1,80);
    putfloat(bytes+64+12,1);putfloat(bytes+64+28,1);put32(bytes+64+36,3);put32(bytes+64+40,7);
    for(unsigned i=0;i<6;++i)putfloat(bytes+64+44+i*4,(float)i-2.5f);
    bytes[64+69]=127;bytes[64+71]=(uint8_t)-127;bytes[64+76]=127;bytes[64+77]=3;
}
static int triangle_v2(void){
    uint8_t bytes[144];prepare_v2(bytes);DlMissionChunk chunk;DlMissionTriangle triangle;
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK&&chunk.version==2);
    CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_OK);
    CHECK(triangle.attribute_flags==3&&triangle.uvs[0]==-2.5f&&triangle.uvs[5]==2.5f);
    CHECK(triangle.normals[1]==127&&triangle.normals[3]==-127&&triangle.normals[8]==127);
    static const struct {unsigned offset;uint32_t value;DlMissionStatus status;} cases[]={
        {108,0x7f800000u,DL_MISSION_VALUE},{132,0x80000000u,DL_MISSION_VALUE},
        {140,0x00040000u,DL_MISSION_LAYOUT},{140,0x00030100u,DL_MISSION_LAYOUT}
    };
    for(unsigned i=0;i<sizeof cases/sizeof cases[0];++i){
        prepare_v2(bytes);put32(bytes+cases[i].offset,cases[i].value);
        CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK);
        CHECK(dl_mission_triangle(&chunk,0,&triangle)==cases[i].status);
    }
    prepare_v2(bytes);memset(bytes+64+68,0,3);
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK);
    CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_VALUE);
    bytes[64+77]=DL_MISSION_TRIANGLE_UV;CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_OK);
    prepare_v2(bytes);put32(bytes+48,44);CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_LAYOUT);
    prepare_v2(bytes);put16(bytes+4,1);CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_LAYOUT);
    return 0;
}
static void prepare_floor(uint8_t *bytes){
    memset(bytes,0,100);memcpy(bytes,"DLR1",4);put16(bytes+4,3);put16(bytes+6,32);
    put32(bytes+8,100);put32(bytes+12,1);section(bytes+32,4,64,36,1,36);
    putfloat(bytes+64+12,2);putfloat(bytes+64+16,.5f);putfloat(bytes+64+32,2);
}
static int floor_v3(void){
    uint8_t bytes[100];prepare_floor(bytes);DlMissionChunk chunk;DlMissionFloorTriangle floor;
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK&&chunk.version==3);
    CHECK(dl_mission_floor(&chunk,0,&floor)==DL_MISSION_OK&&floor.vertices[1].y==.5f);
    CHECK(dl_mission_floor(&chunk,1,&floor)==DL_MISSION_REFERENCE);
    CHECK(dl_mission_floor(NULL,0,&floor)==DL_MISSION_ARGUMENT);
    CHECK(dl_mission_floor(&chunk,0,NULL)==DL_MISSION_ARGUMENT);
    for(unsigned index=0;index<9;++index){
        prepare_floor(bytes);put32(bytes+64+index*4,0x7fc00000u);
        memset(&floor,0x55,sizeof floor);DlMissionFloorTriangle before=floor;
        CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK);
        CHECK(dl_mission_floor(&chunk,0,&floor)==DL_MISSION_VALUE);
        CHECK(!memcmp(&floor,&before,sizeof floor));
    }
    prepare_floor(bytes);putfloat(bytes+64+32,0);
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK);
    CHECK(dl_mission_floor(&chunk,0,&floor)==DL_MISSION_VALUE);
    prepare_floor(bytes);putfloat(bytes+64+16,4);
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_OK);
    CHECK(dl_mission_floor(&chunk,0,&floor)==DL_MISSION_VALUE);
    prepare_floor(bytes);put32(bytes+48,32);
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_LAYOUT);
    prepare_floor(bytes);put16(bytes+4,2);
    CHECK(dl_mission_chunk_open(bytes,sizeof bytes,&chunk)==DL_MISSION_LAYOUT);
    uint8_t triangle_bytes[144];prepare_v2(triangle_bytes);put16(triangle_bytes+4,3);
    DlMissionTriangle triangle;
    CHECK(dl_mission_chunk_open(triangle_bytes,sizeof triangle_bytes,&chunk)==DL_MISSION_OK);
    CHECK(dl_mission_triangle(&chunk,0,&triangle)==DL_MISSION_OK&&triangle.attribute_flags==3);
    return 0;
}
int main(void){
    if(valid_package()||crc_cases()||empty_entity_directory()||directory_errors()||chunk_errors()||lazy_records()||triangle_v2()||floor_v3())return 1;
    puts("mission directory, chunk layout and lazy record validation passed");return 0;
}
