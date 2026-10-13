/* Host-only full-mission cook. Every triangle stores both RGBA faces in
 * closed/open door states, matching DLR1 v4 and renderer NightColors. */
#include "static_lighting.h"
#include "content_limits.h"
#include "demo_level.h"
#include <stdio.h>
#include <stdlib.h>

static bool be16(FILE *f,unsigned x){return fputc((x>>8)&255,f)!=EOF&&fputc(x&255,f)!=EOF;}
static bool be32(FILE *f,unsigned x){return be16(f,x>>16)&&be16(f,x&65535);}
int main(int argc,char **argv){
    if(argc!=2){fprintf(stderr,"Usage: bake_stream_lighting OUTPUT\n");return 2;}
    const DlLevel *level=&dl_demo_level;
    unsigned records=0,max_triangles=0;
    uint64_t triangles=0,color_bytes=0;
    if(!level->environment.enabled||level->model_count<0||level->model_count>UINT16_MAX||
       level->mesh_count<0||level->mesh_count>UINT16_MAX)return 3;
    for(int i=0;i<level->model_count;++i){
        const DlModelInstance *model=&level->models[i];
        if(model->mesh>=level->mesh_count)return 3;
        const DlMesh *mesh=&level->meshes[model->mesh];
        if(mesh->vertex_count<=0||mesh->vertex_count>DL_MAX_MESH_VERTICES||
           mesh->index_count<=0||mesh->index_count%3)return 3;
        unsigned count=(unsigned)mesh->index_count/3;
        triangles+=count;
        if(dl_static_lighting_eligible(level,i)){
            if(count>UINT16_MAX)return 3;
            ++records;color_bytes+=(uint64_t)count*48;
            if(count>max_triangles)max_triangles=count;
        }
    }
    uint64_t total=64+(uint64_t)records*12+color_bytes;
    if(triangles>UINT32_MAX||total>UINT32_MAX)return 3;
    FILE *f=fopen(argv[1],"wb");if(!f){perror(argv[1]);return 4;}
    bool ok=fwrite("DLS1",1,4,f)==4&&be16(f,1)&&be16(f,64);
    for(int i=0;i<32;++i)ok=ok&&fputc(0,f)!=EOF;
    ok=ok&&be32(f,(unsigned)total)&&be16(f,(unsigned)level->model_count)&&be16(f,records)&&
        be32(f,(unsigned)triangles)&&be32(f,(unsigned)color_bytes)&&be32(f,0)&&be32(f,0);
    unsigned start=0;
    for(int i=0;i<level->model_count;++i){
        unsigned count=(unsigned)level->meshes[level->models[i].mesh].index_count/3;
        if(dl_static_lighting_eligible(level,i))ok=ok&&be16(f,(unsigned)i)&&be16(f,count)&&be32(f,start)&&be16(f,2)&&be16(f,0);
        start+=count;
    }
    DlStaticLightingColor *colors=malloc(sizeof(*colors)*(max_triangles?max_triangles:1));
    if(!colors)ok=false;
    for(int i=0;ok&&i<level->model_count;++i){
        if(!dl_static_lighting_eligible(level,i))continue;
        int count=level->meshes[level->models[i].mesh].index_count/3;
        for(int state=0;ok&&state<2;++state){
            ok=dl_static_lighting_model(level,i,state!=0,colors,max_triangles);
            for(int t=0;ok&&t<count;++t){
                ok=fwrite(colors[t].front,1,12,f)==12&&fwrite(colors[t].back,1,12,f)==12;
            }
        }
    }
    free(colors);if(fclose(f)!=0)ok=false;
    if(!ok){fprintf(stderr,"Stream lighting bake failed\n");return 5;}
    return 0;
}
