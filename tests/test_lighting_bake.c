#include "lighting_bake.h"
#include "animation.h"
#include <stdio.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
static uint8_t fixture[4096],closed[5*24],opened[5*24];
static size_t fixture_size;
static const DlVec3 points[]={{0,0,0},{1,0,0},{0,1,0}};
static const uint16_t one[]={0,1,2},two[]={0,1,2,2,1,0};
static const DlMesh meshes[]={ {.vertices=points,.vertex_count=3,.indices=one,.index_count=3},
    {.vertices=points,.vertex_count=3,.indices=two,.index_count=6}};
static const DlModelInstance models[]={ {.mesh=0,.role=DL_MODEL_STATIC},
    {.mesh=0,.role=DL_MODEL_GUARD}, {.mesh=1,.role=DL_MODEL_DOOR,.double_sided=true},
    {.mesh=0,.role=DL_MODEL_OBJECTIVE}};
static DlLevel level={.meshes=meshes,.mesh_count=2,.models=models,.model_count=4,
    .environment={.enabled=true},.baked_lighting="rom:/lighting/lighting-000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f.bin"};
typedef struct {const uint8_t *data;size_t length,offset,chunk,max_read;} Reader;
static size_t read_bytes(void *context,void *out,size_t wanted){
    Reader *r=context;
    if(wanted>r->max_read)r->max_read=wanted;
    size_t n=r->length-r->offset;if(n>wanted)n=wanted;if(n>r->chunk)n=r->chunk;
    memcpy(out,r->data+r->offset,n);r->offset+=n;return n;
}
static void put16(uint8_t *p,unsigned n){p[0]=(uint8_t)(n>>8);p[1]=(uint8_t)n;}
static void put32(uint8_t *p,uint32_t n){p[0]=(uint8_t)(n>>24);p[1]=(uint8_t)(n>>16);p[2]=(uint8_t)(n>>8);p[3]=(uint8_t)n;}
/* Independent bit-at-a-time IEEE CRC32 fixture writer, not decoder code. */
static uint32_t crc32(const uint8_t *p,size_t bytes){
    uint32_t crc=0xffffffff;
    while(bytes--){crc^=*p++;for(int bit=0;bit<8;++bit)crc=(crc>>1)^((crc&1)?0xedb88320u:0);}
    return crc^0xffffffff;
}
static void checksum(void){put32(fixture+56,crc32(fixture+64,fixture_size-64));}
static void prepare(void){
    memset(fixture,0,sizeof fixture);memcpy(fixture,"DLC1",4);put16(fixture+4,1);put16(fixture+6,64);
    for(int i=0;i<32;++i)fixture[8+i]=(uint8_t)i;
    fixture_size=64+2*12+18+2*36;put32(fixture+40,(uint32_t)fixture_size);
    put16(fixture+44,4);put16(fixture+46,2);put32(fixture+48,5);put32(fixture+52,90);
    put16(fixture+64,0);put16(fixture+66,1);put32(fixture+68,0);put16(fixture+72,1);
    put16(fixture+76,2);put16(fixture+78,2);put32(fixture+80,2);put16(fixture+84,2);
    for(size_t i=88;i<fixture_size;++i)fixture[i]=(uint8_t)(i*13+7);
    checksum();
}
static DlLightingBakeResult decode(size_t available,size_t file_size,size_t chunk,Reader *reader){
    *reader=(Reader){fixture,available,0,chunk,0};
    memset(closed,0x55,sizeof closed);memset(opened,0x55,sizeof opened);
    return dl_lighting_bake_decode(&level,read_bytes,reader,file_size,closed,opened,5);
}
static int buffer_boundaries(void){
    enum { TRIANGLES=61 };
    uint16_t indices[TRIANGLES*3]={0};
    DlMesh mesh={.vertices=points,.vertex_count=3,.indices=indices,.index_count=TRIANGLES*3};
    DlModelInstance model={.mesh=0,.role=DL_MODEL_STATIC};
    DlLevel big=level;big.models=&model;big.model_count=1;big.meshes=&mesh;big.mesh_count=1;
    uint8_t states[2][TRIANGLES*24+2];
    prepare();fixture_size=64+12+TRIANGLES*18;
    put32(fixture+40,(uint32_t)fixture_size);put16(fixture+44,1);put16(fixture+46,1);
    put32(fixture+48,TRIANGLES);put32(fixture+52,TRIANGLES*18);put16(fixture+66,TRIANGLES);
    for(size_t i=76;i<fixture_size;++i)fixture[i]=(uint8_t)(i*19+3);
    checksum();Reader reader={fixture,fixture_size,0,512,0};
    memset(states,0x55,sizeof states);
    DlLightingBakeResult result=dl_lighting_bake_decode(&big,read_bytes,&reader,fixture_size,states[0]+1,states[1]+1,TRIANGLES);
    CHECK(result.status==DL_LIGHTING_BAKE_OK&&reader.offset==fixture_size&&reader.max_read==512);
    size_t source=76;
    for(int state=0;state<2;++state){
        CHECK(states[state][0]==0x55&&states[state][sizeof states[state]-1]==0x55);
        for(int tri=0;tri<TRIANGLES;++tri){
            const uint8_t *out=states[state]+1+tri*24;
            for(int corner=0;corner<3;++corner){
                CHECK(memcmp(out+corner*4,fixture+source+corner*3,3)==0);
                CHECK(memcmp(out+12+corner*4,fixture+source+corner*3,3)==0);
                CHECK(out[corner*4+3]==255&&out[12+corner*4+3]==255);
            }
            source+=9;
        }
    }
    const size_t cuts[]={511,512,513,1023,1024,1025};
    for(size_t i=0;i<sizeof cuts/sizeof cuts[0];++i){
        reader=(Reader){fixture,cuts[i],0,512,0};
        result=dl_lighting_bake_decode(&big,read_bytes,&reader,fixture_size,states[0]+1,states[1]+1,TRIANGLES);
        CHECK(result.status==DL_LIGHTING_BAKE_TRUNCATED);
        CHECK(states[0][0]==0x55&&states[1][sizeof states[1]-1]==0x55);
    }
    return 0;
}
int main(void){
    prepare();Reader reader;
    for(size_t chunk=1;chunk<=512;chunk*=2){
        DlLightingBakeResult result=decode(fixture_size,fixture_size,chunk,&reader);
        CHECK(result.status==DL_LIGHTING_BAKE_OK&&result.models==2&&result.triangles==3&&result.payload_bytes==90);
        CHECK(reader.offset==fixture_size&&reader.max_read<=512);
        size_t source=88;
        for(int model=0;model<2;++model)for(int state=0;state<2;++state){
            uint8_t *output=state?opened:closed;int start=model?2:0,count=model?2:1;
            for(int tri=0;tri<count;++tri){
                uint8_t *out=output+(start+tri)*24;
                for(int corner=0;corner<3;++corner){
                    CHECK(memcmp(out+corner*4,fixture+source+corner*3,3)==0&&out[corner*4+3]==255);
                    CHECK(memcmp(out+12+corner*4,fixture+source+(model?9:0)+corner*3,3)==0&&out[12+corner*4+3]==255);
                }
                source+=model?18:9;
            }
        }
        for(int byte=0;byte<24;++byte)CHECK(closed[24+byte]==0x55&&opened[24+byte]==0x55&&closed[96+byte]==0x55&&opened[96+byte]==0x55);
    }
    for(size_t cut=0;cut<fixture_size;++cut)CHECK(decode(cut,fixture_size,17,&reader).status==DL_LIGHTING_BAKE_TRUNCATED);
    CHECK(decode(fixture_size,fixture_size-1,512,&reader).status==DL_LIGHTING_BAKE_LAYOUT);
    CHECK(decode(fixture_size,fixture_size+1,512,&reader).status==DL_LIGHTING_BAKE_LAYOUT);
    fixture[8]^=1;CHECK(decode(fixture_size,fixture_size,512,&reader).status==DL_LIGHTING_BAKE_IDENTITY);prepare();
    fixture[fixture_size-1]^=1;CHECK(decode(fixture_size,fixture_size,512,&reader).status==DL_LIGHTING_BAKE_CHECKSUM);prepare();
    const size_t invalid_header[]={0,4,6,60};
    for(size_t i=0;i<sizeof invalid_header/sizeof invalid_header[0];++i){fixture[invalid_header[i]]^=1;CHECK(decode(fixture_size,fixture_size,512,&reader).status==DL_LIGHTING_BAKE_HEADER);prepare();}
    const size_t invalid_layout[]={40,44,46,48,52,64,66,68,72,74,76,78,80,84,86};
    for(size_t i=0;i<sizeof invalid_layout/sizeof invalid_layout[0];++i){fixture[invalid_layout[i]]^=1;checksum();CHECK(decode(fixture_size,fixture_size,512,&reader).status==DL_LIGHTING_BAKE_LAYOUT);prepare();}
    const char *path=level.baked_lighting;level.baked_lighting="rom:/lighting/lighting-malformed.bin";
    CHECK(decode(fixture_size,fixture_size,512,&reader).status==DL_LIGHTING_BAKE_PATH);level.baked_lighting=path;
    CHECK(dl_lighting_bake_decode(&level,read_bytes,&reader,fixture_size,closed,opened,4).status==DL_LIGHTING_BAKE_LAYOUT);
    CHECK(buffer_boundaries()==0);
    printf("lighting bake: streamed RGB/RGBA expansion, dynamic holes, all 178 truncations, 512-byte boundary splits, wrong identity, topology/order/sides/reserved corruption, CRC and bounds passed\n");
    return 0;
}
