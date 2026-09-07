#include "render_lighting.h"
#include "animation.h"
#include "render_transform.h"
#include <math.h>
#include <stddef.h>

int dl_lighting_build_groups(const DlMesh *mesh,uint16_t *vertex_groups,
    uint16_t *representatives,int capacity){
    if(!mesh||!mesh->normals||!vertex_groups||!representatives||
       vertex_groups==representatives||mesh->vertex_count<=0||
       mesh->vertex_count>UINT16_MAX||capacity<mesh->vertex_count)return -1;
    const uint8_t *bones=mesh->animation?mesh->animation->vertex_bones:NULL;
    if(mesh->animation){
        if(!bones||!mesh->animation->bone_count||
           mesh->animation->bone_count>DL_ANIMATION_MAX_BONES)return -1;
        for(int v=0;v<mesh->vertex_count;++v)
            if(bones[v]>=mesh->animation->bone_count)return -1;
    }
    int count=0;
    for(int v=0;v<mesh->vertex_count;++v){
        DlNormal normal=mesh->normals[v];
        int group=0;
        for(;group<count;++group){
            int other=representatives[group];
            DlNormal candidate=mesh->normals[other];
            if(normal.x==candidate.x&&normal.y==candidate.y&&normal.z==candidate.z&&
               (!bones||bones[v]==bones[other]))break;
        }
        if(group==count)representatives[count++]=(uint16_t)v;
        vertex_groups[v]=(uint16_t)group;
    }
    return count;
}

static float positive(float x){return x>0?x:0;}
static uint8_t color_byte(float x){return (uint8_t)(int)(x<0?0:x>255?255:x);}
static float direction(DlVec3 normal){
    return normal.x*0.30f+normal.y*0.81f+normal.z*-0.50f;
}

void dl_lighting_scalar_prepare(const DlGame *game,const DlModelInstance *model,
    float light,DlScalarLighting *out){
    out->red=model->color[0];out->green=model->color[1];out->blue=model->color[2];
    if(model->role==DL_MODEL_CONTROL&&game->door_open){
        out->red=88;out->green=185;out->blue=117;
    }
    if(model->role==DL_MODEL_GUARD&&model->enemy_index>=0&&
       model->enemy_index<game->enemy_count&&
       game->enemies[model->enemy_index].state==DL_CHASE){
        out->red=180;out->green=72;out->blue=56;
    }
    out->exposure=0.24f+0.92f*light;
    out->red_add=light*12;out->green_add=light*6;
    out->objective=model->role==DL_MODEL_OBJECTIVE;
}

static inline DlLightingColor scalar_color(const DlScalarLighting *lighting,float d){
    float shape=0.72f+0.28f*positive(d);
    float exposure=lighting->exposure*shape;
    if(lighting->objective)exposure=0.62f+0.18f*shape;
    return (DlLightingColor){
        color_byte(lighting->red*exposure+lighting->red_add),
        color_byte(lighting->green*exposure+lighting->green_add),
        color_byte(lighting->blue*exposure),255};
}

DlLightingPair dl_lighting_scalar_pair(const DlScalarLighting *lighting,
    DlVec3 normal,bool double_sided){
    float d=direction(normal);
    DlLightingPair pair;
    pair.front=scalar_color(lighting,d);
    pair.back=double_sided?scalar_color(lighting,-d):pair.front;
    return pair;
}

static inline DlLightingColor night_color(DlLightingColor body,float d){
    float shape=0.70f+0.30f*positive(d);
    return (DlLightingColor){(uint8_t)(int)(body.r*shape),
        (uint8_t)(int)(body.g*shape),(uint8_t)(int)(body.b*shape),255};
}

DlLightingPair dl_lighting_night_pair(DlLightingColor body_color,
    DlVec3 normal,bool double_sided){
    float d=direction(normal);
    DlLightingPair pair;
    pair.front=night_color(body_color,d);
    pair.back=double_sided?night_color(body_color,-d):pair.front;
    return pair;
}

bool dl_lighting_prepare_normals(const DlMesh *mesh,const uint16_t *representatives,
    int count,DlLightingNormalGroup *out){
    if(!mesh||!mesh->normals||!representatives||!out||count<=0||count>mesh->vertex_count)return false;
    const uint8_t *bones=mesh->animation?mesh->animation->vertex_bones:NULL;
    if(mesh->animation&&(!bones||!mesh->animation->bone_count||
        mesh->animation->bone_count>DL_ANIMATION_MAX_BONES))return false;
    for(int group=0;group<count;++group){
        int v=representatives[group];
        if(v>=mesh->vertex_count)return false;
        DlNormal n=mesh->normals[v];
        if((!n.x&&!n.y&&!n.z)||(bones&&bones[v]>=mesh->animation->bone_count))return false;
    }
    for(int group=0;group<count;++group){
        int v=representatives[group];DlNormal n=mesh->normals[v];
        float length=sqrtf((float)n.x*n.x+(float)n.y*n.y+(float)n.z*n.z);
        out[group]=(DlLightingNormalGroup){{n.x/length,n.y/length,n.z/length},bones?bones[v]:0};
    }
    return true;
}

static bool local_directions(const DlAnimMatrix *palette,int bone_count,DlVec3 scale,DlVec3 *directions){
    if(scale.x<=0||scale.x!=scale.y||scale.x!=scale.z)return false;
    for(int bone=0;bone<bone_count;++bone){
        const float *m=palette[bone].m;
        directions[bone]=(DlVec3){
            (m[0]*.30f+m[4]*.81f+m[8]*-.50f)*scale.x,
            (m[1]*.30f+m[5]*.81f+m[9]*-.50f)*scale.x,
            (m[2]*.30f+m[6]*.81f+m[10]*-.50f)*scale.x};
    }
    return true;
}

static inline float group_direction(const DlLightingNormalGroup *group,const DlVec3 *directions){
    DlVec3 n=group->unit_normal,d=directions[group->bone];
    return n.x*d.x+n.y*d.y+n.z*d.z;
}

void dl_lighting_scalar_groups(const DlScalarLighting *lighting,
    const DlLightingNormalGroup *groups,int count,const DlAnimMatrix *normal_palette,
    int bone_count,DlVec3 instance_scale,bool double_sided,DlLightingPair *out){
    DlVec3 directions[DL_ANIMATION_MAX_BONES];
    if(local_directions(normal_palette,bone_count,instance_scale,directions)){
        for(int group=0;group<count;++group){
            float d=group_direction(&groups[group],directions);
            out[group].front=scalar_color(lighting,d);
            out[group].back=double_sided?scalar_color(lighting,-d):out[group].front;
        }
    }else{
        for(int group=0;group<count;++group)
            out[group]=dl_lighting_scalar_pair(lighting,dl_render_matrix_normal(
                &normal_palette[groups[group].bone],groups[group].unit_normal),double_sided);
    }
}

void dl_lighting_night_groups(DlLightingColor body_color,
    const DlLightingNormalGroup *groups,int count,const DlAnimMatrix *normal_palette,
    int bone_count,DlVec3 instance_scale,bool double_sided,DlLightingPair *out){
    DlVec3 directions[DL_ANIMATION_MAX_BONES];
    if(local_directions(normal_palette,bone_count,instance_scale,directions)){
        for(int group=0;group<count;++group){
            float d=group_direction(&groups[group],directions);
            out[group].front=night_color(body_color,d);
            out[group].back=double_sided?night_color(body_color,-d):out[group].front;
        }
    }else{
        for(int group=0;group<count;++group)
            out[group]=dl_lighting_night_pair(body_color,dl_render_matrix_normal(
                &normal_palette[groups[group].bone],groups[group].unit_normal),double_sided);
    }
}
