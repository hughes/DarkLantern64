/* Host-only worker. The generated header contains the same float literals,
 * signed-byte normals and instance order as the target compiler consumes. */
#include "static_lighting.h"
#include "content_limits.h"
#include "demo_level.h"
#include <stdio.h>
#include <stdlib.h>

static bool be16(FILE *f,unsigned x){return fputc((x>>8)&255,f)!=EOF&&fputc(x&255,f)!=EOF;}
static bool be32(FILE *f,unsigned x){return be16(f,x>>16)&&be16(f,x&65535);}
int main(int argc,char **argv){
    if(argc!=2){fprintf(stderr,"Usage: bake_lighting OUTPUT\n");return 2;}
    const DlLevel *level=&dl_demo_level;
    unsigned records=0,triangles=0,rgb_bytes=0;
    if(!level->environment.enabled||level->model_count>DL_MAX_MODELS)return 3;
    for(int i=0;i<level->model_count;++i){
        const DlModelInstance *model=&level->models[i];
        if(model->mesh>=level->mesh_count)return 3;
        const DlMesh *mesh=&level->meshes[model->mesh];
        if(mesh->index_count<0||mesh->index_count%3)return 3;
        triangles+=(unsigned)mesh->index_count/3;
        if(dl_static_lighting_eligible(level,i)){
            ++records;rgb_bytes+=(unsigned)mesh->index_count*3*2*(model->double_sided?2:1);
        }
    }
    if(triangles>DL_MAX_SCENE_TRIANGLES)return 3;
    FILE *f=fopen(argv[1],"wb");if(!f){perror(argv[1]);return 4;}
    bool ok=fwrite("DLC1",1,4,f)==4&&be16(f,1)&&be16(f,64);
    for(int i=0;i<32;++i)ok=ok&&fputc(0,f)!=EOF; /* recipe digest finalized by Python */
    ok=ok&&be32(f,64+records*12+rgb_bytes)&&be16(f,(unsigned)level->model_count)&&be16(f,records)&&
        be32(f,triangles)&&be32(f,rgb_bytes)&&be32(f,0)&&be32(f,0);
    unsigned start=0;
    for(int i=0;i<level->model_count;++i){
        const DlModelInstance *model=&level->models[i];
        unsigned count=(unsigned)level->meshes[model->mesh].index_count/3;
        if(dl_static_lighting_eligible(level,i))ok=ok&&be16(f,(unsigned)i)&&be16(f,count)&&be32(f,start)&&be16(f,model->double_sided?2:1)&&be16(f,0);
        start+=count;
    }
    DlStaticLightingColor *colors=malloc(sizeof(*colors)*DL_MAX_SCENE_TRIANGLES);
    if(!colors)ok=false;
    for(int i=0;ok&&i<level->model_count;++i){
        if(!dl_static_lighting_eligible(level,i))continue;
        const DlModelInstance *model=&level->models[i];
        int count=level->meshes[model->mesh].index_count/3;
        for(int state=0;ok&&state<2;++state){
            ok=dl_static_lighting_model(level,i,state!=0,colors,DL_MAX_SCENE_TRIANGLES);
            for(int t=0;ok&&t<count;++t){
                for(int c=0;c<3;++c)ok=ok&&fwrite(colors[t].front[c],1,3,f)==3;
                if(model->double_sided)for(int c=0;c<3;++c)ok=ok&&fwrite(colors[t].back[c],1,3,f)==3;
            }
        }
    }
    free(colors);if(fclose(f)!=0)ok=false;
    if(!ok){fprintf(stderr,"Static lighting bake failed\n");return 5;}
    return 0;
}
