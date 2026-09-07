#include "lighting_bake.h"
#include "static_lighting.h"
#include "content_limits.h"
#include <string.h>

/* Nibble table avoids a 1 KiB CRC table without doing eight bit steps/byte. */
static const uint32_t crc_table[16]={
    0x00000000,0x1db71064,0x3b6e20c8,0x26d930ac,0x76dc4190,0x6b6b51f4,0x4db26158,0x5005713c,
    0xedb88320,0xf00f9344,0xd6d6a3e8,0xcb61b38c,0x9b64c2b0,0x86d3d2d4,0xa00ae278,0xbdbdf21c};
typedef struct {
    DlLightingBakeRead read;void *context;
    uint8_t buffer[512];size_t at,available,remaining;
    uint32_t crc;
} Stream;
static bool bytes(Stream *stream,void *destination,size_t count,bool checksum){
    uint8_t *out=destination;
    while(count){
        if(stream->at==stream->available){
            size_t wanted=stream->remaining<sizeof stream->buffer?stream->remaining:sizeof stream->buffer;
            if(!wanted)return false;
            size_t got=stream->read(stream->context,stream->buffer,wanted);
            if(!got||got>wanted)return false;
            stream->at=0;stream->available=got;stream->remaining-=got;
        }
        size_t n=stream->available-stream->at;if(n>count)n=count;
        memcpy(out,stream->buffer+stream->at,n);
        if(checksum)for(size_t i=0;i<n;++i){
            uint32_t crc=stream->crc^out[i];
            crc=(crc>>4)^crc_table[crc&15];stream->crc=(crc>>4)^crc_table[crc&15];
        }
        out+=n;stream->at+=n;count-=n;
    }
    return true;
}
static uint16_t be16(const uint8_t *p){return (uint16_t)((uint16_t)p[0]<<8|p[1]);}
static uint32_t be32(const uint8_t *p){return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3];}
static int hex(char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:c>='A'&&c<='F'?c-'A'+10:-1;}
static bool path_key(const char *path,uint8_t key[32]){
    if(!path)return false;
    const char *name=strrchr(path,'/');name=name?name+1:path;
    if(strlen(name)!=9+64+4||strncmp(name,"lighting-",9)!=0)return false;
    name+=9;
    if(strcmp(name+64,".bin")!=0)return false;
    for(int i=0;i<32;++i){int a=hex(name[i*2]),b=hex(name[i*2+1]);if(a<0||b<0)return false;key[i]=(uint8_t)(a*16+b);}
    return true;
}
const char *dl_lighting_bake_status_name(DlLightingBakeStatus status){
    static const char *const names[]={"ok","argument","path","header","identity","layout","truncated","checksum"};
    return (unsigned)status<sizeof names/sizeof names[0]?names[status]:"unknown";
}
DlLightingBakeResult dl_lighting_bake_decode(const DlLevel *level,
    DlLightingBakeRead read,void *context,size_t file_bytes,
    void *closed_colors,void *open_colors,size_t triangle_capacity){
    DlLightingBakeResult result={DL_LIGHTING_BAKE_ARGUMENT,0,0,0,0};
    if(!level||!read||!closed_colors||!open_colors||closed_colors==open_colors||
       !level->environment.enabled||!level->models||!level->meshes||level->model_count<=0||
       level->model_count>DL_MAX_MODELS||level->mesh_count<=0||triangle_capacity>DL_MAX_SCENE_TRIANGLES)return result;
    uint8_t key[32];result.status=DL_LIGHTING_BAKE_PATH;
    if(!path_key(level->baked_lighting,key))return result;
    uint32_t triangles=0,records=0,payload=0;
    for(int i=0;i<level->model_count;++i){
        const DlModelInstance *model=&level->models[i];
        result.status=DL_LIGHTING_BAKE_LAYOUT;
        if(model->mesh>=level->mesh_count)return result;
        const DlMesh *mesh=&level->meshes[model->mesh];
        if(mesh->index_count<=0||mesh->index_count%3||mesh->index_count/3>DL_MAX_SCENE_TRIANGLES)return result;
        uint32_t count=(uint32_t)mesh->index_count/3;
        if(count>triangle_capacity-triangles)return result;
        triangles+=count;
        if(dl_static_lighting_eligible(level,i)){++records;payload+=count*(model->double_sided?36:18);result.triangles+=count;}
    }
    if(!records||file_bytes!=64+records*12+payload){result.status=DL_LIGHTING_BAKE_LAYOUT;return result;}
    Stream stream={.read=read,.context=context,.remaining=file_bytes,.crc=0xffffffff};
    uint8_t header[64];result.status=DL_LIGHTING_BAKE_TRUNCATED;
    if(!bytes(&stream,header,sizeof header,false))return result;
    result.status=DL_LIGHTING_BAKE_HEADER;
    if(memcmp(header,"DLC1",4)||be16(header+4)!=1||be16(header+6)!=64||be32(header+60))return result;
    result.status=DL_LIGHTING_BAKE_IDENTITY;
    if(memcmp(header+8,key,32))return result;
    result.status=DL_LIGHTING_BAKE_LAYOUT;
    if(be32(header+40)!=file_bytes||be16(header+44)!=level->model_count||be16(header+46)!=records||
       be32(header+48)!=triangles||be32(header+52)!=payload)return result;
    uint32_t start=0;
    for(int i=0;i<level->model_count;++i){
        const DlModelInstance *model=&level->models[i];uint32_t count=(uint32_t)level->meshes[model->mesh].index_count/3;
        if(dl_static_lighting_eligible(level,i)){
            uint8_t row[12];result.status=DL_LIGHTING_BAKE_TRUNCATED;
            if(!bytes(&stream,row,sizeof row,true))return result;
            result.status=DL_LIGHTING_BAKE_LAYOUT;
            if(be16(row)!=i||be16(row+2)!=count||be32(row+4)!=start||
               be16(row+8)!=(model->double_sided?2:1)||be16(row+10))return result;
        }
        start+=count;
    }
    uint8_t *states[2]={closed_colors,open_colors};start=0;
    for(int i=0;i<level->model_count;++i){
        const DlModelInstance *model=&level->models[i];uint32_t count=(uint32_t)level->meshes[model->mesh].index_count/3;
        if(dl_static_lighting_eligible(level,i))for(int state=0;state<2;++state)for(uint32_t t=0;t<count;++t){
            uint8_t rgb[18],*out=states[state]+(start+t)*24;
            result.status=DL_LIGHTING_BAKE_TRUNCATED;
            if(!bytes(&stream,rgb,model->double_sided?18:9,true))return result;
            for(int corner=0;corner<3;++corner){
                memcpy(out+corner*4,rgb+corner*3,3);out[corner*4+3]=255;
                memcpy(out+12+corner*4,rgb+(model->double_sided?9:0)+corner*3,3);out[12+corner*4+3]=255;
            }
        }
        start+=count;
    }
    result.crc32=stream.crc^0xffffffff;result.models=records;result.payload_bytes=payload;
    result.status=result.crc32==be32(header+56)?DL_LIGHTING_BAKE_OK:DL_LIGHTING_BAKE_CHECKSUM;
    return result;
}
