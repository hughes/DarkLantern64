#include "static_lighting.h"
#include "render_shading.h"
#include "content_limits.h"
#include <math.h>

static DlVec3 add(DlVec3 a,DlVec3 b){return (DlVec3){a.x+b.x,a.y+b.y,a.z+b.z};}
static DlVec3 sub(DlVec3 a,DlVec3 b){return (DlVec3){a.x-b.x,a.y-b.y,a.z-b.z};}
static DlVec3 mul(DlVec3 a,float s){return (DlVec3){a.x*s,a.y*s,a.z*s};}
static float dot(DlVec3 a,DlVec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
static DlVec3 cross(DlVec3 a,DlVec3 b){
    return (DlVec3){a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};
}
static bool finite3(DlVec3 p){return isfinite(p.x)&&isfinite(p.y)&&isfinite(p.z);}
static float clampf(float v,float lo,float hi){return v<lo?lo:v>hi?hi:v;}

bool dl_static_lighting_eligible(const DlLevel *level,int index){
    if(!level||!level->environment.enabled||!level->models||!level->meshes||
       index<0||index>=level->model_count)return false;
    const DlModelInstance *model=&level->models[index];
    return model->mesh<level->mesh_count&&model->role!=DL_MODEL_GUARD&&
        model->role!=DL_MODEL_OBJECTIVE&&!level->meshes[model->mesh].animation;
}

/* Keep the arithmetic and its grouping identical to render.c's legacy
 * transform_model_vertices / transform_scaled_point. An affine composition
 * has different float rounding at shadow boundaries. */
static DlVec3 point(DlVec3 p,const DlModelInstance *model,DlVec3 sine,DlVec3 cosine,
                     bool opened,float hinge_x,float hinge_z){
    p.x*=model->scale.x;p.y*=model->scale.y;p.z*=model->scale.z;
    if(opened){float x=p.x-hinge_x,z=p.z-hinge_z;p.x=hinge_x+z;p.z=hinge_z-x;}
    p=(DlVec3){p.x,cosine.x*p.y-sine.x*p.z,sine.x*p.y+cosine.x*p.z};
    p=(DlVec3){cosine.y*p.x+sine.y*p.z,p.y,-sine.y*p.x+cosine.y*p.z};
    p=(DlVec3){cosine.z*p.x-sine.z*p.y,sine.z*p.x+cosine.z*p.y,p.z};
    return add(p,model->position);
}
static void color(uint8_t out[4],const DlLevel *level,const DlModelInstance *model,DlVec3 light){
    float exposure=level->environment.exposure;
    out[0]=(uint8_t)(int)clampf(model->color[0]*sqrtf(fmaxf(0,light.x*exposure))+model->emissive[0],0,255);
    out[1]=(uint8_t)(int)clampf(model->color[1]*sqrtf(fmaxf(0,light.y*exposure))+model->emissive[1],0,255);
    out[2]=(uint8_t)(int)clampf(model->color[2]*sqrtf(fmaxf(0,light.z*exposure))+model->emissive[2],0,255);
    out[3]=255;
}
bool dl_static_lighting_model(const DlLevel *level,int index,bool door_open,
                              DlStaticLightingColor *output,size_t capacity){
    if(!output||!dl_static_lighting_eligible(level,index))return false;
    const DlModelInstance *model=&level->models[index];
    const DlMesh *mesh=&level->meshes[model->mesh];
    if(!mesh->vertices||!mesh->indices||mesh->vertex_count<=0||mesh->vertex_count>DL_MAX_MESH_VERTICES||
       mesh->index_count<0||mesh->index_count%3||capacity<(size_t)(mesh->index_count/3)||
       !finite3(model->position)||!finite3(model->rotation)||!finite3(model->scale)||
       model->scale.x==0||model->scale.y==0||model->scale.z==0||
       !isfinite(level->environment.exposure)||level->environment.exposure<0)return false;
    DlVec3 minimum=mesh->vertices[0],maximum=minimum;
    for(int v=0;v<mesh->vertex_count;++v){
        DlVec3 p=mesh->vertices[v];if(!finite3(p))return false;
        minimum=(DlVec3){fminf(minimum.x,p.x),fminf(minimum.y,p.y),fminf(minimum.z,p.z)};
        maximum=(DlVec3){fmaxf(maximum.x,p.x),fmaxf(maximum.y,p.y),fmaxf(maximum.z,p.z)};
    }
    for(int j=0;j<mesh->index_count;++j)if(mesh->indices[j]>=mesh->vertex_count)return false;
    float hinge_x=minimum.x*model->scale.x,hinge_z=(minimum.z+maximum.z)*0.5f*model->scale.z;
    DlVec3 rotation=model->rotation;
    DlVec3 sine={sinf(rotation.x),sinf(rotation.y),sinf(rotation.z)};
    DlVec3 cosine={cosf(rotation.x),cosf(rotation.y),cosf(rotation.z)};
    bool opened=model->role==DL_MODEL_DOOR&&door_open;
    DlGame game={.level=level,.door_open=door_open};
    for(int t=0;t<mesh->index_count/3;++t){
        DlVec3 points[3];
        for(int c=0;c<3;++c)points[c]=point(mesh->vertices[mesh->indices[t*3+c]],model,sine,cosine,opened,hinge_x,hinge_z);
        DlVec3 normal=cross(sub(points[1],points[0]),sub(points[2],points[0]));
        float length=sqrtf(dot(normal,normal));
        normal=length>0.000001f?mul(normal,1/length):(DlVec3){0,1,0};
        for(int c=0;c<3;++c){
            DlVec3 n=mesh->normals?dl_render_normal(mesh->normals[mesh->indices[t*3+c]],model->scale,sine,cosine,opened):normal;
            DlVec3 front=dl_surface_light(&game,add(points[c],mul(n,0.035f)),n);
            DlVec3 back=model->double_sided?dl_surface_light(&game,add(points[c],mul(n,-0.035f)),mul(n,-1)):front;
            color(output[t].front[c],level,model,front);color(output[t].back[c],level,model,back);
        }
    }
    return true;
}
