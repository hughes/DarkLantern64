#include "render.h"
#include "dl_profile.h"
#include "render_visibility.h"
#include "render_shading.h"
#include "render_camera.h"
#include "animation.h"
#include "content_limits.h"
#ifdef DL_RENDER_T3D
#include "render_batches.h"
#include "render_lighting.h"
#include "render_transform.h"
#include "render_texture_packing.h"
#include <t3d/t3d.h>
#include <malloc.h>
static void gpu_prepare(const DlGame *g);
static void gpu_release(void);
static void gpu_report(void);
#endif

#include <libdragon.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Full XYZ meshes: CPU affine/view transforms and frustum clipping, followed
 * by the RDP triangle rasterizer and hardware depth buffer. No grid dependency. */
enum { SCREEN_W=320, SCREEN_H=240, VIEW_TOP=27, VIEW_BOTTOM=192,
       MAX_MODELS=DL_MAX_MODELS, MAX_VERTICES=DL_MAX_SCENE_VERTICES,
       MAX_MESH_VERTICES=DL_MAX_MESH_VERTICES, MAX_TRIANGLES=DL_MAX_SCENE_TRIANGLES, CLIP_CAPACITY=12 };
static const float focal=164.0f, horizon=(VIEW_TOP+VIEW_BOTTOM)*0.5f;
static const float near_plane=0.12f, far_plane=64.0f;

typedef struct {
    int vertex_start,triangle_start,smooth_start,animation_normal_start;
    int idle_clip,walk_clip,run_clip;
    float hinge_x,hinge_z,bounds_radius;
    DlVec3 scaled_center,bounds_center;
    bool visible;
} ModelCache;
typedef struct { DlVec3 position,sine,cosine; bool door_open; } ModelPose;
typedef struct { DlVec3 center,normal; color_t front,back; } FaceCache;
typedef struct { color_t front[3],back[3]; } NightColors;
typedef struct { color_t front,back; } VertexColors;
typedef struct { DlVec3 position; DlVec2 uv; DlVec3 color; } ClipVertex;
typedef struct { DlVec3 eye,right,up,forward; } Camera;
static ModelCache models[MAX_MODELS];
static DlVec3 world_vertices[MAX_VERTICES];
#ifndef DL_RENDER_T3D
static DlVec3 camera_vertices[MAX_VERTICES];
static uint8_t clip_codes[MAX_VERTICES];
#endif
static FaceCache faces[MAX_TRIANGLES];
/* Allocate only the authored triangle count. Both door states are prepared
 * before play, so opening the gate never retraces every static light ray. */
static NightColors *night_colors[2];
/* Legacy scalar-light scenes need corner colors only for smooth meshes.
 * Night scenes reuse their existing per-corner, two-door-state cache. */
static VertexColors *smooth_colors;
static int smooth_vertex_count;
static int vertex_count,triangle_count,submitted_triangles;
static const DlLevel *cached_level;
static bool cached_door;
static surface_t depth_buffer;
static sprite_t *textures[64];
static const DlLevel *texture_level;
static int texture_uploads;
static int visible_models,transformed_vertices;
static DlRenderFrustum frustum;
static DlAnimMatrix animation_skin[DL_ANIMATION_MAX_BONES]; /* reused per model */
static DlVec3 *animation_normals;
static int animation_normal_count,animation_model_count;
static uint64_t animation_sample_ticks,animation_skin_ticks;
static uint32_t animation_frames,animation_poses,animation_sample_max,animation_skin_max;
static void hud_cache_prepare(const DlGame *g);
static void hud_cache_release(void);
#ifdef DL_CAPTURE
static const char *animation_preview_clip;
static float animation_preview_time,animation_preview_yaw,animation_preview_pitch;
void dl_render_set_animation_preview(const char *clip,float time,float yaw,float pitch){
    animation_preview_clip=clip;animation_preview_time=time;animation_preview_yaw=yaw;animation_preview_pitch=pitch;
}
#endif

static float clampf(float v,float lo,float hi){return v<lo?lo:v>hi?hi:v;}
static DlVec3 add(DlVec3 a,DlVec3 b){return (DlVec3){a.x+b.x,a.y+b.y,a.z+b.z};}
static DlVec3 sub(DlVec3 a,DlVec3 b){return (DlVec3){a.x-b.x,a.y-b.y,a.z-b.z};}
static DlVec3 mul(DlVec3 a,float s){return (DlVec3){a.x*s,a.y*s,a.z*s};}
static float dot(DlVec3 a,DlVec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
static DlVec3 cross(DlVec3 a,DlVec3 b){
    return (DlVec3){a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};
}
static uint32_t animation_elapsed_ticks(DlProfileMark start){
    DlProfileMark end=dl_profile_mark();
    return (end.ticks-start.ticks)-(end.audio_ticks-start.audio_ticks);
}
static const DlEnemy *model_enemy(const DlGame *g,const DlModelInstance *model){
    return model->role==DL_MODEL_GUARD&&model->enemy_index>=0&&model->enemy_index<g->enemy_count?
        &g->enemies[model->enemy_index]:NULL;
}
static float highest_awareness(const DlGame *g){
    float value=0;
    for(int i=0;i<g->enemy_count;++i)value=fmaxf(value,g->enemies[i].awareness);
    return value;
}
static bool enemy_chasing(const DlGame *g){
    for(int i=0;i<g->enemy_count;++i)if(g->enemies[i].state==DL_CHASE)return true;
    return false;
}
static color_t rgb(int r,int g,int b){return (color_t){(uint8_t)r,(uint8_t)g,(uint8_t)b,255};}
static void box(int x0,int y0,int x1,int y1,color_t color){
    if(x0<0)x0=0;
    if(y0<0)y0=0;
    if(x1>SCREEN_W)x1=SCREEN_W;
    if(y1>SCREEN_H)y1=SCREEN_H;
    if(x1<=x0||y1<=y0)return;
    rdpq_set_fill_color(color);
    rdpq_fill_rectangle(x0,y0,x1,y1);
}

static DlVec3 transform_scaled_point(DlVec3 p,const ModelCache *cache,const ModelPose *pose){
    if(pose->door_open){
        /* Hinge around the authored mesh's left edge in scaled model space. */
        float x=p.x-cache->hinge_x,z=p.z-cache->hinge_z;
        p.x=cache->hinge_x+z;p.z=cache->hinge_z-x;
    }
    p=(DlVec3){p.x,pose->cosine.x*p.y-pose->sine.x*p.z,pose->sine.x*p.y+pose->cosine.x*p.z};
    p=(DlVec3){pose->cosine.y*p.x+pose->sine.y*p.z,p.y,-pose->sine.y*p.x+pose->cosine.y*p.z};
    p=(DlVec3){pose->cosine.z*p.x-pose->sine.z*p.y,pose->sine.z*p.x+pose->cosine.z*p.y,p.z};
    return add(p,pose->position);
}
/* Bounds and vertices use exactly the same S, hinge, Rx, Ry, Rz, T transform.
 * Updating this one point lets an offscreen moving actor skip its entire mesh. */
static ModelPose update_model_pose(const DlGame *g,int index){
    const DlModelInstance *model=&g->level->models[index];
    DlVec3 position=model->position,rotation=model->rotation;
    const DlEnemy *enemy=model_enemy(g,model);
    if(enemy){
        const DlEnemyDef *def=&g->level->enemies[model->enemy_index];
        position=add(position,sub(enemy->position,def->spawn));
        rotation.y+=enemy->yaw-def->yaw;
    }
    if(model->role==DL_MODEL_OBJECTIVE)rotation.y+=g->elapsed*0.45f;
    ModelPose pose={position,{sinf(rotation.x),sinf(rotation.y),sinf(rotation.z)},
        {cosf(rotation.x),cosf(rotation.y),cosf(rotation.z)},model->role==DL_MODEL_DOOR&&g->door_open};
    models[index].bounds_center=transform_scaled_point(models[index].scaled_center,&models[index],&pose);
    return pose;
}
static void sample_model_animation(const DlGame *g,int index){
    const DlModelInstance *model=&g->level->models[index];
    const DlAnimationAsset *asset=g->level->meshes[model->mesh].animation;
    const ModelCache *cache=&models[index];
    const DlEnemy *enemy=model_enemy(g,model);
    int first=cache->idle_clip,second=cache->walk_clip;
    float first_time=g->elapsed,second_time=0,blend=0,head_yaw=0,head_pitch=0;
    if(enemy){
        if(second>=0){
            const DlAnimClip *walk=&asset->clips[second];
            float stride=walk->stride_length>0.01f?walk->stride_length:1.44f;
            second_time=enemy->animation_distance/stride*walk->duration;
            blend=clampf(enemy->animation_speed/0.4f,0,1);
            if(first<0)first=second;
        }
        if(enemy->sees_player||enemy->state==DL_INVESTIGATE||enemy->state==DL_CHASE){
            DlVec3 target=enemy->sees_player?dl_player_eye(g):enemy->investigate_target;
            if(!enemy->sees_player)target.y+=1.5f;
            DlVec3 eye=enemy->position;eye.y+=1.6f;
            DlVec3 delta=sub(target,eye);
            head_yaw=remainderf(atan2f(delta.x,delta.z)-enemy->yaw,6.283185307f);
            head_pitch=-atan2f(delta.y,sqrtf(delta.x*delta.x+delta.z*delta.z));
        }
    }
#ifdef DL_CAPTURE
    if(animation_preview_clip&&*animation_preview_clip){
        first=dl_animation_find_clip(asset,animation_preview_clip);
        assertf(first>=0,"Unknown animation preview clip %s",animation_preview_clip);
        first_time=animation_preview_time;blend=0;
        head_yaw=animation_preview_yaw;head_pitch=animation_preview_pitch;
    }
#endif
    bool valid=dl_animation_pose(asset,first,first_time,second,second_time,blend,head_yaw,head_pitch,animation_skin);
    assertf(valid,"Invalid animation skeleton");
}
static void transform_model_vertices(const DlGame *g,int index,const ModelPose *pose){
    const DlModelInstance *model=&g->level->models[index];
    const DlMesh *mesh=&g->level->meshes[model->mesh];
    const ModelCache *cache=&models[index];
    DlProfileMark animation_mark={0};
    if(mesh->animation){
        animation_mark=dl_profile_mark();
        sample_model_animation(g,index);
        uint32_t ticks=animation_elapsed_ticks(animation_mark);
        animation_sample_ticks+=ticks;if(ticks>animation_sample_max)animation_sample_max=ticks;
        ++animation_poses;animation_mark=dl_profile_mark();
    }
    for(int i=0;i<mesh->vertex_count;++i){
        DlVec3 p=mesh->vertices[i];
        if(mesh->animation){
            const DlAnimMatrix *skin=&animation_skin[mesh->animation->vertex_bones[i]];
            p=dl_animation_point(skin,p);
            if(mesh->normals){
                DlNormal n=mesh->normals[i];
                DlVec3 normal=dl_animation_normal(skin,(DlVec3){n.x,n.y,n.z});
                animation_normals[cache->animation_normal_start+i]=
                    dl_render_normal_vector(normal,model->scale,pose->sine,pose->cosine,pose->door_open);
            }
        }
        p.x*=model->scale.x;p.y*=model->scale.y;p.z*=model->scale.z;
        world_vertices[cache->vertex_start+i]=transform_scaled_point(p,cache,pose);
    }
    if(mesh->animation){
        uint32_t ticks=animation_elapsed_ticks(animation_mark);
        animation_skin_ticks+=ticks;if(ticks>animation_skin_max)animation_skin_max=ticks;
    }
}
static void transform_model(const DlGame *g,int index){
    ModelPose pose=update_model_pose(g,index);
    transform_model_vertices(g,index,&pose);
}

static color_t face_color(const DlGame *g,const DlModelInstance *model,DlVec3 normal,float light){
    float shape=0.72f+0.28f*fmaxf(0,dot(normal,(DlVec3){0.30f,0.81f,-0.50f}));
    float exposure=(0.24f+0.92f*light)*shape;
    float r=model->color[0],green=model->color[1],b=model->color[2];
    if(model->role==DL_MODEL_CONTROL&&g->door_open){r=88;green=185;b=117;}
    const DlEnemy *enemy=model_enemy(g,model);
    if(enemy&&enemy->state==DL_CHASE){r=180;green=72;b=56;}
    /* The relic is deliberately emissive-looking, with face shading for its
     * shape. It does not need scene illumination queries while rotating. */
    if(model->role==DL_MODEL_OBJECTIVE)exposure=0.62f+0.18f*shape;
    return rgb((int)clampf(r*exposure+light*12,0,255),
        (int)clampf(green*exposure+light*6,0,255),(int)clampf(b*exposure,0,255));
}
static color_t night_color(const DlGame *g,const DlModelInstance *model,DlVec3 light){
    float exposure=g->level->environment.exposure;
    return rgb((int)clampf(model->color[0]*sqrtf(fmaxf(0,light.x*exposure))+model->emissive[0],0,255),
        (int)clampf(model->color[1]*sqrtf(fmaxf(0,light.y*exposure))+model->emissive[1],0,255),
        (int)clampf(model->color[2]*sqrtf(fmaxf(0,light.z*exposure))+model->emissive[2],0,255));
}
static void light_model(const DlGame *g,int index,bool illuminate){
    const DlModelInstance *model=&g->level->models[index];
    const DlMesh *mesh=&g->level->meshes[model->mesh];
    const ModelCache *cache=&models[index];
    float model_light=-1;
    DlVec3 actor_light={0};
    const DlEnemy *enemy=model_enemy(g,model);
    DlVec3 actor_position=model->position;
    if(enemy)actor_position=add(actor_position,sub(enemy->position,g->level->enemies[model->enemy_index].spawn));
    if(illuminate&&g->level->environment.enabled&&model->role==DL_MODEL_GUARD){
        DlVec3 lower=actor_position,upper;
        lower.y+=0.65f;upper=lower;upper.y+=0.7f;
        actor_light=mul(add(dl_surface_light(g,lower,(DlVec3){0,1,0}),
            dl_surface_light(g,upper,(DlVec3){0,1,0})),0.5f);
    }
    if(illuminate&&g->level->environment.enabled&&model->role==DL_MODEL_OBJECTIVE)
        actor_light=dl_surface_light(g,model->position,(DlVec3){0,1,0});
    if(illuminate&&!g->level->environment.enabled&&model->role==DL_MODEL_GUARD){
        /* Two body-height probes approximate the actor's incident light.
         * Both still use shared world/door occlusion, but individual limbs do
         * not receive separate shadow boundaries. Normal shading remains per
         * face. This avoids hundreds of repeated OBB light queries per frame. */
        DlVec3 lower=actor_position;
        DlVec3 upper=lower;
        lower.y+=0.65f;upper.y+=1.35f;
        model_light=0.5f*(dl_visibility(g,lower)+dl_visibility(g,upper));
    }else if(model->role==DL_MODEL_OBJECTIVE){
        model_light=0;
    }
    bool actor=g->level->environment.enabled&&(model->role==DL_MODEL_GUARD||model->role==DL_MODEL_OBJECTIVE);
    color_t actor_color=actor?night_color(g,model,actor_light):rgb(0,0,0);
    ModelPose pose={0};
    if(illuminate&&mesh->normals)pose=update_model_pose(g,index);
    if(illuminate&&mesh->normals&&!g->level->environment.enabled){
        for(int v=0;v<mesh->vertex_count;++v){
            DlVec3 normal=mesh->animation?animation_normals[cache->animation_normal_start+v]:
                dl_render_normal(mesh->normals[v],model->scale,pose.sine,pose.cosine,pose.door_open);
            DlVec3 point=world_vertices[cache->vertex_start+v],back_normal=mul(normal,-1);
            float front_light=model_light,back_light=model_light;
            if(model_light<0){
                front_light=dl_visibility(g,add(point,mul(normal,0.035f)));
                back_light=model->double_sided?dl_visibility(g,add(point,mul(back_normal,0.035f))):front_light;
            }
            VertexColors *colors=&smooth_colors[cache->smooth_start+v];
            colors->front=face_color(g,model,normal,front_light);
            colors->back=model->double_sided?face_color(g,model,back_normal,back_light):colors->front;
        }
    }
    for(int t=0;t<mesh->index_count/3;++t){
        DlVec3 a=world_vertices[cache->vertex_start+mesh->indices[t*3]];
        DlVec3 b=world_vertices[cache->vertex_start+mesh->indices[t*3+1]];
        DlVec3 c=world_vertices[cache->vertex_start+mesh->indices[t*3+2]];
        FaceCache *face=&faces[cache->triangle_start+t];
        face->normal=cross(sub(b,a),sub(c,a));
        float length=sqrtf(dot(face->normal,face->normal));
        face->normal=length>0.000001f?mul(face->normal,1/length):(DlVec3){0,1,0};
        face->center=mul(add(add(a,b),c),1.0f/3.0f);
        if(!illuminate)continue;
        DlVec3 back_normal=mul(face->normal,-1);
        if(g->level->environment.enabled){
            NightColors *colors=&night_colors[g->door_open][cache->triangle_start+t];
            if(actor){
                /* Body probes already represent the whole actor. Reuse their
                 * display color instead of repeating identical square roots
                 * for every face corner and side. */
                for(int corner=0;corner<3;++corner){
                    color_t front=actor_color,back=actor_color;
                    if(mesh->animation){
                        DlVec3 normal=mesh->normals?animation_normals[cache->animation_normal_start+mesh->indices[t*3+corner]]:face->normal;
                        float shape=0.70f+0.30f*fmaxf(0,dot(normal,(DlVec3){0.30f,0.81f,-0.50f}));
                        float back_shape=0.70f+0.30f*fmaxf(0,-dot(normal,(DlVec3){0.30f,0.81f,-0.50f}));
                        front=rgb((int)(front.r*shape),(int)(front.g*shape),(int)(front.b*shape));
                        back=rgb((int)(back.r*back_shape),(int)(back.g*back_shape),(int)(back.b*back_shape));
                    }
                    colors->front[corner]=front;colors->back[corner]=back;
                }
                continue;
            }
            DlVec3 points[]={a,b,c};
            for(int corner=0;corner<3;++corner){
                DlVec3 front=actor_light,back=actor_light;
                int vertex=mesh->indices[t*3+corner];
                DlVec3 shading_normal=face->normal;
                if(mesh->normals)shading_normal=mesh->animation?
                    animation_normals[cache->animation_normal_start+vertex]:
                    dl_render_normal(mesh->normals[vertex],model->scale,pose.sine,pose.cosine,pose.door_open);
                if(model->role!=DL_MODEL_GUARD&&model->role!=DL_MODEL_OBJECTIVE){
                    front=dl_surface_light(g,add(points[corner],mul(shading_normal,0.035f)),shading_normal);
                    back=model->double_sided?dl_surface_light(g,add(points[corner],mul(shading_normal,-0.035f)),mul(shading_normal,-1)):front;
                }
                colors->front[corner]=night_color(g,model,front);
                colors->back[corner]=night_color(g,model,back);
            }
            continue;
        }
        if(mesh->normals)continue; /* scalar colors were cached per vertex */
        float front_light=model_light,back_light=model_light;
        if(model_light<0){
            /* Static faces retain separate samples just outside either side
             * of their physical surface, cached until the door state changes. */
            front_light=dl_visibility(g,add(face->center,mul(face->normal,0.035f)));
            back_light=dl_visibility(g,add(face->center,mul(back_normal,0.035f)));
        }
        face->front=face_color(g,model,face->normal,front_light);
        face->back=face_color(g,model,back_normal,back_light);
    }
}
static void load_textures(const DlGame *g){
    if(texture_level==g->level)return;
    rspq_wait();
    for(int i=0;i<64;++i){if(textures[i])sprite_free(textures[i]);textures[i]=NULL;}
    assertf(g->level->texture_count<=64,"Too many scene textures");
    for(int i=0;i<g->level->texture_count;++i){
        textures[i]=sprite_load(g->level->textures[i].path);
        assertf(textures[i],"Cannot load texture %s",g->level->textures[i].path);
    }
    texture_level=g->level;
}
static void build_cache(const DlGame *g){
    uint32_t start=TICKS_READ();
    load_textures(g);
#ifdef DL_RENDER_T3D
    gpu_release();
#endif
    vertex_count=triangle_count=smooth_vertex_count=0;
    animation_normal_count=animation_model_count=0;
    assertf(g->level->model_count<=MAX_MODELS,"Scene exceeds %d model instances",MAX_MODELS);
    for(int i=0;i<g->level->model_count;++i){
        const DlModelInstance *model=&g->level->models[i];
        assertf(model->mesh<g->level->mesh_count,"Bad model mesh index");
        const DlMesh *mesh=&g->level->meshes[model->mesh];
        models[i].animation_normal_start=-1;
        models[i].idle_clip=models[i].walk_clip=models[i].run_clip=-1;
        if(!mesh->animation)continue;
        assertf(mesh->vertex_count>0&&mesh->vertex_count<=MAX_MESH_VERTICES,"Invalid animated vertex count");
        assertf(dl_animation_validate(mesh->animation,mesh->vertex_count),"Invalid animated mesh");
        ++animation_model_count;
        models[i].idle_clip=dl_animation_find_clip(mesh->animation,"idle");
        models[i].walk_clip=dl_animation_find_clip(mesh->animation,"walk");
        models[i].run_clip=dl_animation_find_clip(mesh->animation,"run");
        if(mesh->normals){
            assertf(animation_normal_count+mesh->vertex_count<=MAX_VERTICES,"Animated normal cache exceeds scene vertex budget");
            models[i].animation_normal_start=animation_normal_count;animation_normal_count+=mesh->vertex_count;
        }
        bool first=true;
        for(int j=0;j<i;++j)if(g->level->meshes[g->level->models[j].mesh].animation==mesh->animation)first=false;
        if(first){
            const DlAnimationAsset *asset=mesh->animation;
            unsigned descriptors=sizeof(*asset)+sizeof(DlAnimBone)*asset->bone_count+
                sizeof(DlAnimClip)*asset->clip_count+sizeof(DlAnimSocket)*asset->socket_count;
            for(int clip=0;clip<asset->clip_count;++clip)descriptors+=sizeof(DlAnimTrack)*asset->clips[clip].track_count+
                sizeof(DlAnimEvent)*asset->clips[clip].event_count;
            debugf("DL64 animation_asset id=%s bones=%d clips=%d encoded_bytes=%lu table_bytes_without_strings=%u skeleton_bytes=%u vertex_binding_bytes=%d bone_limit=%d\n",
                asset->id,asset->bone_count,asset->clip_count,(unsigned long)asset->encoded_bytes,descriptors,
                (unsigned)(sizeof(DlAnimBone)*asset->bone_count),mesh->vertex_count,DL_ANIMATION_MAX_BONES);
        }
    }
    free(animation_normals);animation_normals=NULL;
    if(animation_normal_count){
        animation_normals=malloc(sizeof(DlVec3)*animation_normal_count);
        assertf(animation_normals,"Cannot allocate animated normal cache");
    }
    for(int i=0;i<g->level->model_count;++i){
        const DlModelInstance *model=&g->level->models[i];
        assertf(model->mesh<g->level->mesh_count,"Bad model mesh index");
        assertf(model->role!=DL_MODEL_GUARD||model_enemy(g,model),"Enemy model lacks a valid instance");
        const DlMesh *mesh=&g->level->meshes[model->mesh];
        assertf(mesh->vertex_count>0&&mesh->vertex_count<=MAX_MESH_VERTICES&&mesh->index_count%3==0,"Invalid mesh geometry");
        assertf(vertex_count+mesh->vertex_count<=MAX_VERTICES,"Scene exceeds %d instanced vertices",MAX_VERTICES);
        assertf(triangle_count+mesh->index_count/3<=MAX_TRIANGLES,"Scene exceeds %d instanced triangles",MAX_TRIANGLES);
        models[i].vertex_start=vertex_count;models[i].triangle_start=triangle_count;
        models[i].smooth_start=-1;
        if(mesh->normals){models[i].smooth_start=smooth_vertex_count;smooth_vertex_count+=mesh->vertex_count;}
        DlVec3 minimum=mesh->vertices[0],maximum=minimum;
        for(int v=1;v<mesh->vertex_count;++v){
            DlVec3 p=mesh->vertices[v];
            minimum=(DlVec3){fminf(minimum.x,p.x),fminf(minimum.y,p.y),fminf(minimum.z,p.z)};
            maximum=(DlVec3){fmaxf(maximum.x,p.x),fmaxf(maximum.y,p.y),fmaxf(maximum.z,p.z)};
        }
        for(int j=0;j<mesh->index_count;++j)assertf(mesh->indices[j]<mesh->vertex_count,"Mesh index outside vertices");
        models[i].hinge_x=minimum.x*model->scale.x;models[i].hinge_z=(minimum.z+maximum.z)*0.5f*model->scale.z;
        DlVec3 center=mul(add(minimum,maximum),0.5f),half=mul(sub(maximum,minimum),0.5f);
        models[i].scaled_center=(DlVec3){center.x*model->scale.x,center.y*model->scale.y,center.z*model->scale.z};
        half=(DlVec3){half.x*model->scale.x,half.y*model->scale.y,half.z*model->scale.z};
        models[i].bounds_radius=sqrtf(dot(half,half));
        if(mesh->animation){
            DlVec3 c=mesh->animation->bounds_center;
            models[i].scaled_center=(DlVec3){c.x*model->scale.x,c.y*model->scale.y,c.z*model->scale.z};
            models[i].bounds_radius=mesh->animation->bounds_radius*fmaxf(fabsf(model->scale.x),fmaxf(fabsf(model->scale.y),fabsf(model->scale.z)));
        }
        vertex_count+=mesh->vertex_count;triangle_count+=mesh->index_count/3;
        transform_model(g,i);
    }
    free(smooth_colors);smooth_colors=NULL;
    if(!g->level->environment.enabled&&smooth_vertex_count){
        smooth_colors=malloc(sizeof(VertexColors)*smooth_vertex_count);
        assertf(smooth_colors,"Cannot allocate smooth vertex lighting cache");
    }
    for(int state=0;state<2;++state){
        free(night_colors[state]);night_colors[state]=NULL;
        if(g->level->environment.enabled){
            night_colors[state]=malloc(sizeof(NightColors)*triangle_count);
            assertf(night_colors[state],"Cannot allocate night lighting cache");
        }
    }
    for(int i=0;i<g->level->model_count;++i)light_model(g,i,true);
    if(g->level->environment.enabled){
        DlGame alternate=*g;alternate.door_open=!g->door_open;
        for(int i=0;i<g->level->model_count;++i){
            if(g->level->models[i].role==DL_MODEL_DOOR)transform_model(&alternate,i);
            light_model(&alternate,i,true);
        }
        for(int i=0;i<g->level->model_count;++i)if(g->level->models[i].role==DL_MODEL_DOOR){
            transform_model(g,i);light_model(g,i,false);
        }
    }
    cached_level=g->level;cached_door=g->door_open;
    debugf("DL64 geometry_ready models=%d meshes=%d vertices=%d triangles=%d depth=hardware transform=xyz-euler dynamic_lighting=two-body-probes objective_lighting=emissive\n",
        g->level->model_count,g->level->mesh_count,vertex_count,triangle_count);
    debugf("DL64 scene_prepared ms=%.2f night_cache_bytes=%d door_states=%d textures=%d\n",
        (float)(uint32_t)(TICKS_READ()-start)*(1000.0f/TICKS_PER_SECOND),
        g->level->environment.enabled?(int)(2*sizeof(NightColors)*triangle_count):0,
        g->level->environment.enabled?2:1,g->level->texture_count);
    debugf("DL64 shading_ready smooth_vertices=%d scalar_smooth_cache_bytes=%d normal_format=snorm8\n",
        smooth_vertex_count,smooth_colors?(int)(sizeof(VertexColors)*smooth_vertex_count):0);
    debugf("DL64 animation_ready models=%d shared_matrix_bytes=%u pose_scratch_arrays_bytes=%lu normal_cache_bytes=%u per_enemy_motion_bytes=%u timeline=actual-distance head_yaw_max=45 head_pitch_max=25\n",
        animation_model_count,(unsigned)sizeof(animation_skin),(unsigned long)dl_animation_scratch_bytes(),
        (unsigned)(animation_normal_count*sizeof(DlVec3)),(unsigned)(2*sizeof(float)));
    animation_sample_ticks=animation_skin_ticks=0;
    animation_frames=animation_poses=animation_sample_max=animation_skin_max=0;
#ifdef DL_RENDER_T3D
    gpu_prepare(g);
#endif
    hud_cache_prepare(g);
}
void dl_render_prepare_scene(const DlGame *game){if(cached_level!=game->level)build_cache(game);}
void dl_render_release_scene(void){
    /* A preceding detach_show queued SYNC_FULL. Wait before freeing texture
     * memory still referenced by the RSP/RDP; never keep both levels' caches. */
    rspq_flush();
    rspq_wait();
#ifdef DL_RENDER_T3D
    gpu_release();
#endif
    hud_cache_release();
    for(int i=0;i<64;++i){if(textures[i])sprite_free(textures[i]);textures[i]=NULL;}
    for(int i=0;i<2;++i){free(night_colors[i]);night_colors[i]=NULL;}
    free(smooth_colors);smooth_colors=NULL;smooth_vertex_count=0;
    dl_render_report_animation();
    free(animation_normals);animation_normals=NULL;animation_normal_count=animation_model_count=0;
    cached_level=texture_level=NULL;
    cached_door=false;
    vertex_count=triangle_count=submitted_triangles=texture_uploads=0;
    visible_models=transformed_vertices=0;
}
void dl_render_report_animation(void){
    if(!animation_frames)return;
#ifdef DL_RENDER_T3D
    gpu_report();
#endif
    if(animation_model_count)debugf("DL64 animation_profile frames=%lu poses=%lu sample_ticks=%llu per_pose_sample_max_ticks=%lu skin_ticks=%llu per_pose_skin_max_ticks=%lu ticks_per_second=%lu nested_in=transforms skin_scope=deform_normals_instance\n",
        (unsigned long)animation_frames,(unsigned long)animation_poses,(unsigned long long)animation_sample_ticks,
        (unsigned long)animation_sample_max,(unsigned long long)animation_skin_ticks,(unsigned long)animation_skin_max,
        (unsigned long)TICKS_PER_SECOND);
    animation_frames=animation_poses=animation_sample_max=animation_skin_max=0;
    animation_sample_ticks=animation_skin_ticks=0;
}
static Camera make_camera(const DlGame *g){
    DlCameraBasis basis=dl_camera_basis(g->yaw,g->pitch);
    return (Camera){.eye=dl_player_eye(g),.right=basis.right,.up=basis.up,.forward=basis.forward};
}

#ifdef DL_RENDER_T3D
#include "render_t3d.inc"
#define geometry geometry_gpu
#else
/* Clip against all six frustum planes before perspective division. */
static float clip_distance(DlVec3 v,int plane){
    switch(plane){
    case 0:return v.z-near_plane;
    case 1:return far_plane-v.z;
    case 2:return v.x+v.z*(SCREEN_W*0.5f/focal);
    case 3:return v.z*(SCREEN_W*0.5f/focal)-v.x;
    case 4:return v.z*((horizon-VIEW_TOP)/focal)-v.y;
    default:return v.y+v.z*((VIEW_BOTTOM-horizon)/focal);
    }
}
static uint8_t clip_code(DlVec3 v){
    float horizontal=v.z*(SCREEN_W*0.5f/focal);
    float top=v.z*((horizon-VIEW_TOP)/focal),bottom=v.z*((VIEW_BOTTOM-horizon)/focal);
    return (uint8_t)((v.z-near_plane<0?1:0)|(far_plane-v.z<0?2:0)|
        (v.x+horizontal<0?4:0)|(horizontal-v.x<0?8:0)|
        (top-v.y<0?16:0)|(v.y+bottom<0?32:0));
}
static ClipVertex mix_vertex(ClipVertex a,ClipVertex b,float t){
    return (ClipVertex){add(a.position,mul(sub(b.position,a.position),t)),
        {a.uv.u+(b.uv.u-a.uv.u)*t,a.uv.v+(b.uv.v-a.uv.v)*t},
        add(a.color,mul(sub(b.color,a.color),t))};
}
static void project(ClipVertex v,const DlEnvironment *environment,int width,int height,float vertex[10]){
    DlVec3 p=v.position;
    float inverse=1/p.z;
    vertex[0]=SCREEN_W*0.5f+p.x*focal*inverse;vertex[1]=horizon-p.y*focal*inverse;
    vertex[2]=clampf(far_plane/(far_plane-near_plane)*(1-near_plane*inverse),0,0.999999f);
    float fog=clampf((p.z-8)/42,0,0.80f);
    if(environment->enabled){
        fog=clampf((p.z-environment->fog_near)/(environment->fog_far-environment->fog_near),0,1);
        vertex[3]=v.color.x;vertex[4]=v.color.y;vertex[5]=v.color.z;vertex[6]=1-fog;
    }else{
        vertex[3]=v.color.x*(1-fog)+12.0f/255*fog;
        vertex[4]=v.color.y*(1-fog)+15.0f/255*fog;
        vertex[5]=v.color.z*(1-fog)+22.0f/255*fog;vertex[6]=1;
    }
    vertex[7]=v.uv.u*width;vertex[8]=v.uv.v*height;vertex[9]=inverse;
}
static void triangle(ClipVertex a,ClipVertex b,ClipVertex c,const DlEnvironment *environment,const DlTexture *texture,unsigned clip_mask){
    ClipVertex storage[2][CLIP_CAPACITY];
    ClipVertex *in=storage[0],*out=storage[1];
    in[0]=a;in[1]=b;in[2]=c;
    int count=3;
    for(int plane=0;plane<6;++plane){
        if(!(clip_mask&(1u<<plane)))continue;
        int output=0;ClipVertex previous=in[count-1];float pd=clip_distance(previous.position,plane);
        for(int i=0;i<count;++i){
            ClipVertex current=in[i];float cd=clip_distance(current.position,plane);
            if((pd>=0)!=(cd>=0)){
                float t=pd/(pd-cd);
                assertf(output<CLIP_CAPACITY,"Triangle clipping capacity");
                out[output++]=mix_vertex(previous,current,t);
            }
            if(cd>=0){
                assertf(output<CLIP_CAPACITY,"Triangle clipping capacity");
                out[output++]=current;
            }
            previous=current;pd=cd;
        }
        if(output<3)return;
        count=output;ClipVertex *swap=in;in=out;out=swap;
    }
    int width=texture?texture->width:0,height=texture?texture->height:0;
    float v0[10],v1[10],v2[10];project(in[0],environment,width,height,v0);
    for(int i=1;i<count-1;++i){
        project(in[i],environment,width,height,v1);project(in[i+1],environment,width,height,v2);
        float area=(v1[0]-v0[0])*(v2[1]-v0[1])-(v1[1]-v0[1])*(v2[0]-v0[0]);
        if(fabsf(area)<0.025f)continue;
        rdpq_triangle(texture?&TRIFMT_ZBUF_SHADE_TEX:&TRIFMT_ZBUF_SHADE,v0,v1,v2);++submitted_triangles;
    }
}
static void geometry(const DlGame *g){
    DlProfileMark mark;
    if(cached_level!=g->level){
        mark=dl_profile_mark();build_cache(g);dl_profile_record(DL_PROFILE_CACHE,mark);
    }
    bool lighting_changed=cached_door!=g->door_open;
    ++animation_frames;
    unsigned animated=0,full=0,drawn=0,overlays=0;
    mark=dl_profile_mark();
    Camera camera=make_camera(g);
    visible_models=transformed_vertices=0;
    for(int i=0;i<g->level->model_count;++i){
        DlModelRole role=g->level->models[i].role;
        bool dynamic=role==DL_MODEL_GUARD||role==DL_MODEL_OBJECTIVE||g->level->meshes[g->level->models[i].mesh].animation;
        ModelPose pose;
        if(dynamic||(lighting_changed&&role==DL_MODEL_DOOR)){
            pose=update_model_pose(g,i);
        }
#ifdef DL_DISABLE_MODEL_CULL
        models[i].visible=!(role==DL_MODEL_OBJECTIVE&&g->complete);
#else
        DlVec3 rel=sub(models[i].bounds_center,camera.eye);
        DlVec3 center={dot(rel,camera.right),dot(rel,camera.up),dot(rel,camera.forward)};
        models[i].visible=!(role==DL_MODEL_OBJECTIVE&&g->complete)&&
            dl_render_sphere_visible(&frustum,center,models[i].bounds_radius);
#endif
        if(models[i].visible)++visible_models;
        if((dynamic&&models[i].visible)||(lighting_changed&&role==DL_MODEL_DOOR)){
            transform_model_vertices(g,i,&pose);
            if(models[i].visible&&g->level->meshes[g->level->models[i].mesh].animation){
                ++animated;
                const DlEnemy *enemy=model_enemy(g,&g->level->models[i]);
                if(enemy&&(enemy->sees_player||enemy->state==DL_INVESTIGATE||enemy->state==DL_CHASE))++overlays;
            }
        }
    }
    dl_profile_record(DL_PROFILE_TRANSFORMS,mark);
    mark=dl_profile_mark();
    for(int i=0;i<g->level->model_count;++i){
        DlModelRole role=g->level->models[i].role;
        bool dynamic=role==DL_MODEL_GUARD||role==DL_MODEL_OBJECTIVE||g->level->meshes[g->level->models[i].mesh].animation;
        if((dynamic&&models[i].visible)||(!dynamic&&lighting_changed)){
            bool illuminate=dynamic||!g->level->environment.enabled;
            if(illuminate||role==DL_MODEL_DOOR){
                light_model(g,i,illuminate);
            }
        }
    }
    dl_profile_record(DL_PROFILE_LIGHTING,mark);
    cached_door=g->door_open;
    mark=dl_profile_mark();
    for(int m=0;m<g->level->model_count;++m){
        if(!models[m].visible)continue;
        int count=g->level->meshes[g->level->models[m].mesh].vertex_count;
        transformed_vertices+=count;
        for(int v=0;v<count;++v){
            int i=models[m].vertex_start+v;
            DlVec3 rel=sub(world_vertices[i],camera.eye);
            camera_vertices[i]=(DlVec3){dot(rel,camera.right),dot(rel,camera.up),dot(rel,camera.forward)};
            clip_codes[i]=clip_code(camera_vertices[i]);
        }
        if(g->level->models[m].role==DL_MODEL_GUARD){
            bool inside=true;
            for(int v=0;v<count;++v)if(clip_codes[models[m].vertex_start+v])inside=false;
            if(inside)++full;
        }
    }
    dl_profile_record(DL_PROFILE_TRANSFORMS,mark);
    /* Submission elapsed time includes any internal graphics queue stalls;
     * these markers do not force synchronization or time RDP completion. */
    mark=dl_profile_mark();
    rdpq_set_scissor(0,VIEW_TOP,SCREEN_W,VIEW_BOTTOM);
    rdpq_set_mode_standard();rdpq_mode_combiner(RDPQ_COMBINER_SHADE);rdpq_mode_zbuf(true,true);
    rdpq_mode_persp(true);
    if(g->level->environment.enabled){
        DlVec3 fog=g->level->environment.fog_color;
        rdpq_set_fog_color(rgb((int)(fog.x*255),(int)(fog.y*255),(int)(fog.z*255)));
        rdpq_mode_fog(RDPQ_FOG_STANDARD);
    }
    submitted_triangles=0;
    texture_uploads=0;
    int bound_texture=-2;
    for(int i=0;i<g->level->model_count;++i){
        const DlModelInstance *model=&g->level->models[i];
        if(!models[i].visible)continue;
        const DlMesh *mesh=&g->level->meshes[model->mesh];const ModelCache *cache=&models[i];
        float highlight=0;
        if(model->loot_highlight){
            DlVec3 delta=sub(cache->bounds_center,camera.eye);
            highlight=dl_loot_highlight_lift(g->elapsed,sqrtf(dot(delta,delta)),(float)i*0.73f);
        }
        float shade_scale=(1-highlight)/255.0f;
        int texture=mesh->uvs&&model->texture>=0&&model->texture<g->level->texture_count?model->texture:-1;
        if(texture!=bound_texture){
            rdpq_mode_combiner(texture>=0?RDPQ_COMBINER_TEX_SHADE:RDPQ_COMBINER_SHADE);
            if(texture>=0){
                rdpq_sprite_upload(TILE0,textures[texture],&(rdpq_texparms_t){.s.repeats=REPEAT_INFINITE,.t.repeats=REPEAT_INFINITE});
                rdpq_mode_filter(FILTER_BILINEAR);++texture_uploads;
            }
            bound_texture=texture;
        }
        int previous_triangles=submitted_triangles;
        for(int t=0;t<mesh->index_count/3;++t){
            const FaceCache *face=&faces[cache->triangle_start+t];
            const NightColors *colors=g->level->environment.enabled?&night_colors[g->door_open][cache->triangle_start+t]:NULL;
            bool front=dot(face->normal,sub(camera.eye,face->center))>=0;
            if(!model->double_sided&&!front)continue;
            unsigned code[3];
            for(int v=0;v<3;++v)code[v]=clip_codes[cache->vertex_start+mesh->indices[t*3+v]];
            if(code[0]&code[1]&code[2])continue;
            ClipVertex corners[3];
            for(int v=0;v<3;++v){
                int index=mesh->indices[t*3+v];
                color_t color;
                if(colors)color=front?colors->front[v]:colors->back[v];
                else if(mesh->normals){
                    const VertexColors *smooth=&smooth_colors[cache->smooth_start+index];
                    color=front?smooth->front:smooth->back;
                }else color=front?face->front:face->back;
                corners[v]=(ClipVertex){camera_vertices[cache->vertex_start+index],texture>=0?mesh->uvs[index]:(DlVec2){0},
                    {color.r*shade_scale+highlight,color.g*shade_scale+highlight,color.b*shade_scale+highlight}};
            }
            triangle(corners[0],corners[1],corners[2],&g->level->environment,texture>=0?&g->level->textures[texture]:NULL,code[0]|code[1]|code[2]);
        }
        if(model->role==DL_MODEL_GUARD&&submitted_triangles>previous_triangles)++drawn;
    }
    rdpq_set_scissor(0,0,SCREEN_W,SCREEN_H);rdpq_set_mode_fill(rgb(0,0,0));
    dl_profile_record(DL_PROFILE_TRIANGLES,mark);
    dl_profile_workload(animated,full,drawn,submitted_triangles,overlays);
}
#endif

/* Top-down projection of actual oriented collision proxies, with no tiles. */
static void map_line(int x0,int y0,int x1,int y1,color_t color){
    int dx=abs(x1-x0),dy=-abs(y1-y0),sx=x0<x1?1:-1,sy=y0<y1?1:-1,error=dx+dy;
    for(int steps=0;steps<160;++steps){
        if(x0>=208&&x0<312&&y0>=34&&y0<138)box(x0,y0,x0+1,y0+1,color);
        if(x0==x1&&y0==y1)break;
        int twice=error*2;
        if(twice>=dy){error+=dy;x0+=sx;}
        if(twice<=dx){error+=dx;y0+=sy;}
    }
}
static void minimap(const DlGame *g){
    float scale=4;box(206,32,314,140,rgb(8,11,16));
    for(int i=0;i<g->level->collider_count;++i){
        const DlCollider *c=&g->level->colliders[i];
        if((c->door&&g->door_open)||c->half_size.y<0.3f)continue;
        float s=sinf(c->yaw),co=cosf(c->yaw);int x[4],z[4];
        for(int j=0;j<4;++j){
            float lx=(j==0||j==3?-1:1)*c->half_size.x,lz=(j<2?-1:1)*c->half_size.z;
            x[j]=260+(int)((c->center.x+co*lx+s*lz-g->player.x)*scale);
            z[j]=86+(int)((c->center.z-s*lx+co*lz-g->player.z)*scale);
        }
        for(int j=0;j<4;++j)map_line(x[j],z[j],x[(j+1)%4],z[(j+1)%4],c->door?rgb(170,119,55):rgb(80,87,90));
    }
    DlVec3 points[]={g->level->control,g->level->objective,g->player};
    color_t colors[]={rgb(97,185,130),rgb(242,203,106),rgb(230,236,220)};
    for(int i=0;i<3+g->enemy_count;++i){
        DlVec3 point=i<3?points[i]:g->enemies[i-3].position;
        color_t color=i<3?colors[i]:rgb(223,83,70);
        int x=260+(int)((point.x-g->player.x)*scale),z=86+(int)((point.z-g->player.z)*scale);
        if(x>208&&x<311&&z>34&&z<137)box(x-1,z-1,x+2,z+2,color);
    }
    map_line(260,86,260+(int)(sinf(g->yaw)*7),86+(int)(cosf(g->yaw)*7),rgb(241,228,177));
}
static void profile_hud(const DlGame *g,const DlRenderStats *stats){
    static const DlProfileSlot rows[]={
        DL_PROFILE_INPUT,DL_PROFILE_GAMEPLAY,DL_PROFILE_AUDIO_EVENTS,
        DL_PROFILE_AUDIO_MIX,DL_PROFILE_CACHE,DL_PROFILE_TRANSFORMS,
        DL_PROFILE_LIGHTING,DL_PROFILE_TRIANGLES,DL_PROFILE_HUD,
        DL_PROFILE_DEBUG_HUD,DL_PROFILE_RENDER_SETUP,DL_PROFILE_DISPLAY_WAIT,
        DL_PROFILE_OTHER,
    };
    static const char labels[]="^02Phase\n^00Input\nGameplay\nAudio events\nAudio mix\n"
        "Scene cache\nTransforms\nLighting\nTriangles\nHUD\nDebug HUD\nRender setup\nDisplay wait\nOther";
    /* The built-in font has ascent 11, descent -2, line gap 1: 14 pixels.
     * Compact it to the same 11-pixel baseline spacing as the former rows. */
    static const rdpq_textparms_t columns={.line_spacing=-3};
    static char averages[256],budgets[256],frame_summary[64];
    static uint32_t cached_window;
    const DlProfileSnapshot *profile=dl_profile_latest();
    rdpq_set_mode_fill(rgb(0,0,0));
    box(7,30,203,190,rgb(9,12,17));minimap(g);
    box(206,140,314,190,rgb(9,12,17));
    if(profile->frames){
        if(cached_window!=profile->window||!averages[0]){
            /* Float formatting is paid once per profiling window, and all
             * three table columns are laid out/submitted in three calls. */
            size_t avg_used=(size_t)snprintf(averages,sizeof(averages),"^02Avg ms\n^00");
            size_t budget_used=(size_t)snprintf(budgets,sizeof(budgets),"^02%%%dfps\n^00",DL_PROFILE_TARGET_FPS);
            for(unsigned i=0;i<sizeof(rows)/sizeof(rows[0]);++i){
                float elapsed=profile->avg_ms[rows[i]],share=elapsed*(DL_PROFILE_TARGET_FPS/10.0f);
                const char *newline=i+1<sizeof(rows)/sizeof(rows[0])?"\n":"";
                int n=elapsed<999.95f?
                    snprintf(averages+avg_used,sizeof(averages)-avg_used,"%5.1f%s",elapsed,newline):
                    snprintf(averages+avg_used,sizeof(averages)-avg_used,">999%s",newline);
                assertf(n>=0&&(size_t)n<sizeof(averages)-avg_used,"Profile average text capacity");
                avg_used+=(size_t)n;
                n=share<9999.5f?
                    snprintf(budgets+budget_used,sizeof(budgets)-budget_used,"%4.0f%s",share,newline):
                    snprintf(budgets+budget_used,sizeof(budgets)-budget_used,">9999%s",newline);
                assertf(n>=0&&(size_t)n<sizeof(budgets)-budget_used,"Profile budget text capacity");
                budget_used+=(size_t)n;
            }
            snprintf(frame_summary,sizeof(frame_summary),"Avg %.1f ms\nPeak %.1f ms",
                profile->frame_ms,profile->max_frame_ms);
            cached_window=profile->window;
        }
        rdpq_text_print(&columns,1,12,40,labels);
        rdpq_text_print(&columns,1,110,40,averages);
        rdpq_text_print(&columns,1,161,40,budgets);
        rdpq_text_print(&columns,1,210,151,frame_summary);
    }else{
        rdpq_text_print(NULL,1,12,46,"Profiling...");
        rdpq_text_print(NULL,1,12,59,"^02Collecting one second of frames");
    }
    int seeing=0,hearing=0;
    for(int i=0;i<g->enemy_count;++i){seeing+=g->enemies[i].sees_player;hearing+=g->enemies[i].heard_sound;}
    rdpq_text_printf(&columns,1,210,173,"E%d S%d H%d\n%dM heap %dK",g->enemy_count,
        seeing,hearing,stats->memory_bytes/(1024*1024),stats->heap_used/1024);
}
#include "render_hud_cache.inc"
static void hud(const DlGame *g,const DlRenderStats *stats){
    DlProfileMark mark=dl_profile_mark();
    box(0,0,SCREEN_W,VIEW_TOP,rgb(9,12,17));box(0,24,SCREEN_W,26,rgb(131,97,45));
    box(0,VIEW_BOTTOM,SCREEN_W,SCREEN_H,rgb(9,12,17));box(0,VIEW_BOTTOM,SCREEN_W,VIEW_BOTTOM+1,rgb(131,97,45));
    box(60,198,127,204,rgb(31,39,43));
    box(60,198,60+(int)(67*clampf(g->visibility,0,1)),204,rgb(212,172,83));
    box(192,198,253,204,rgb(31,39,43));
    float awareness=highest_awareness(g);
    box(192,198,192+(int)(61*clampf(awareness,0,1)),204,awareness>0.65f?rgb(219,78,56):rgb(207,141,71));
    box(158,(int)horizon,162,(int)horizon+1,rgb(212,195,155));
    box(159,(int)horizon-1,160,(int)horizon+3,rgb(212,195,155));
    HudPrompt prompt=g->door_open?HUD_PROMPT_OPEN:HUD_PROMPT_CLOSED;
    DlVec3 eye=dl_player_eye(g);
    if(dot(sub(eye,g->level->control),sub(eye,g->level->control))<2.25f&&dl_line_of_sight(g,eye,g->level->control))prompt=HUD_PROMPT_SWITCH;
    if(dot(sub(eye,g->level->objective),sub(eye,g->level->objective))<2.25f&&dl_line_of_sight(g,eye,g->level->objective))prompt=HUD_PROMPT_RELIC;
    if(enemy_chasing(g))prompt=HUD_PROMPT_CHASE;
    hud_cache_draw(g,prompt);
    dl_profile_record(DL_PROFILE_HUD,mark);
    if(stats->debug){
        mark=dl_profile_mark();
        profile_hud(g,stats);
        dl_profile_record(DL_PROFILE_DEBUG_HUD,mark);
    }
    if(g->complete||g->caught){
        mark=dl_profile_mark();
        rdpq_set_mode_fill(rgb(0,0,0));
        box(38,80,282,139,rgb(10,13,18));box(38,80,282,82,g->caught?rgb(183,60,48):rgb(218,181,87));
        hud_cache_result(g->caught);
        dl_profile_record(DL_PROFILE_HUD,mark);
    }
}
void dl_render_init(void){
#ifdef DL_RENDER_T3D
    t3d_init((T3DInitParams){0});
    dl_profile_set_geometry_evidence(true);
    for(int i=0;i<GPU_FRAMES;++i){
        gpu_viewports[i]=t3d_viewport_create();
        t3d_viewport_set_area(&gpu_viewports[i],0,VIEW_TOP,SCREEN_W,VIEW_BOTTOM-VIEW_TOP);
        t3d_viewport_set_projection(&gpu_viewports[i],2*atanf((VIEW_BOTTOM-VIEW_TOP)*0.5f/focal),near_plane*gpu_units,far_plane*gpu_units);
        t3d_mat4_identity(&gpu_viewports[i].matCamera);
    }
#endif
    frustum=dl_render_frustum(near_plane,far_plane,SCREEN_W*0.5f/focal,(horizon-VIEW_TOP)/focal);
    depth_buffer=surface_alloc(FMT_RGBA16,SCREEN_W,SCREEN_H);
    rdpq_font_t *font=rdpq_font_load_builtin(FONT_BUILTIN_DEBUG_VAR);
    rdpq_font_style(font,0,&(rdpq_fontstyle_t){.color={213,219,213,255}});
    rdpq_font_style(font,1,&(rdpq_fontstyle_t){.color={236,192,107,255}});
    rdpq_font_style(font,2,&(rdpq_fontstyle_t){.color={149,160,169,255}});
    rdpq_text_register_font(1,font);
}
int dl_render_triangle_count(void){return submitted_triangles;}
int dl_render_texture_upload_count(void){return texture_uploads;}
int dl_render_model_count(void){return cached_level?cached_level->model_count:0;}
int dl_render_visible_model_count(void){return visible_models;}
int dl_render_transformed_vertex_count(void){return transformed_vertices;}
static void background(const DlGame *game){
    if(!game->level->environment.enabled)return;
    const DlEnvironment *env=&game->level->environment;
    rdpq_set_mode_fill(rgb(0,0,0));
    for(int y=VIEW_TOP;y<VIEW_BOTTOM;y+=4){
        float t=clampf((float)(y-VIEW_TOP)/(VIEW_BOTTOM-VIEW_TOP),0,1);
        DlVec3 color=add(mul(env->sky_top,1-t),mul(env->sky_bottom,t));
        box(0,y,SCREEN_W,y+4<VIEW_BOTTOM?y+4:VIEW_BOTTOM,rgb((int)(color.x*255),(int)(color.y*255),(int)(color.z*255)));
    }
    Camera camera=make_camera(game);
    DlVec3 moon=env->moon_direction;
    float z=dot(moon,camera.forward);
    if(z>0.01f){
        int x=(int)(SCREEN_W*0.5f+dot(moon,camera.right)*focal/z);
        int y=(int)(horizon-dot(moon,camera.up)*focal/z);
        for(int dy=-4;dy<=4;++dy){
            int width=(int)sqrtf(16.0f-dy*dy);
            if(y+dy>=VIEW_TOP&&y+dy<VIEW_BOTTOM)box(x-width,y+dy,x+width+1,y+dy+1,rgb(195,216,242));
        }
    }
}
#if defined(DL_CAPTURE) || defined(DL_MENU_TEST)
/* Diagnostic builds only: export the actual completed RGBA5551 framebuffer.
 * The explicit GPU wait and dump are outside normal performance captures. */
static int capture_id;
void dl_render_request_capture(int id){capture_id=id;}
static void capture_frame(surface_t *display){
    if(!capture_id)return;
    rspq_wait();
    debugf("DL64 capture_begin id=%d width=%d height=%d format=rgba5551\n",capture_id,SCREEN_W,SCREEN_H);
    const char hex[]="0123456789abcdef";
    char row[SCREEN_W*4+1];
    for(int y=0;y<SCREEN_H;++y){
        const uint16_t *pixels=(const uint16_t*)((const uint8_t*)display->buffer+y*display->stride);
        for(int x=0;x<SCREEN_W;++x){uint16_t p=pixels[x];for(int n=0;n<4;++n)row[x*4+n]=hex[(p>>(12-n*4))&15];}
        row[SCREEN_W*4]='\0';debugf("DL64 capture_row id=%d y=%d data=%s\n",capture_id,y,row);
    }
    debugf("DL64 capture_end id=%d\n",capture_id);capture_id=0;
}
#endif
float dl_render_frame(const DlGame *game,const DlRenderStats *stats){
    DlProfileMark mark=dl_profile_mark();
    /* Keep the queued completion callback runnable before waiting for its
     * display buffer. The installed SDK/Ares combination can leave the RSP
     * asleep with pending commands after the preceding detach flush. This
     * wakes the queue without waiting for graphics or draining the pipeline. */
    rspq_flush();
    surface_t *display=display_get();
    dl_profile_record(DL_PROFILE_DISPLAY_WAIT,mark);
    uint32_t start=TICKS_READ();
    mark=dl_profile_mark();
    rdpq_attach(display,&depth_buffer);rdpq_clear(rgb(12,15,22));rdpq_clear_z(0xffff);
    background(game);
    dl_profile_record(DL_PROFILE_RENDER_SETUP,mark);
    geometry(game);hud(game,stats);
    mark=dl_profile_mark();
#ifdef DL_CAPTURE
    if(capture_id){rdpq_detach();capture_frame(display);display_show(display);}
    else rdpq_detach_show();
#else
    rdpq_detach_show();
#endif
    dl_profile_record(DL_PROFILE_RENDER_SETUP,mark);
    return (float)(uint32_t)(TICKS_READ()-start)*(1000.0f/TICKS_PER_SECOND);
}
/* User labels are data, never the built-in text renderer's style escapes.
 * The ROM's debug font is ASCII; cap label length to the available menu row. */
static void menu_label(char *out,const char *source,int limit){
    int count=0;
    for(int i=0;source&&source[i]&&count<limit;++i){
        unsigned char ch=(unsigned char)source[i];
        if(ch>=128){if((ch&0xc0)!=0x80)out[count++]='?';continue;}
        out[count++]=(ch<' '||ch=='^'||ch=='$')?' ':ch;
    }
    out[count]='\0';
}
float dl_render_menu(const DlBundle *bundle,const DlLauncher *launcher,const DlRenderStats *stats){
    (void)stats;
    DlProfileMark mark=dl_profile_mark();
    rspq_flush();
    surface_t *display=display_get();
    dl_profile_record(DL_PROFILE_DISPLAY_WAIT,mark);
    uint32_t start=TICKS_READ();
    mark=dl_profile_mark();
    rdpq_attach(display,NULL);
    rdpq_clear(rgb(9,12,18));
    rdpq_set_mode_fill(rgb(0,0,0));
    dl_profile_record(DL_PROFILE_RENDER_SETUP,mark);
    mark=dl_profile_mark();
    box(10,34,310,36,rgb(131,97,45));
    char title[49];menu_label(title,bundle->title,42);
    rdpq_text_print(NULL,1,12,17,title);
    rdpq_text_printf(NULL,1,12,30,"^01LEVEL SELECT^00                         %d levels",bundle->level_count);
    for(int i=0;i<bundle->level_count;++i){
        int y=51+i*16;
        if(i==launcher->selected_level)box(10,y-10,310,y+4,rgb(44,39,28));
        char label[49];menu_label(label,bundle->levels[i].title,42);
        rdpq_text_print(NULL,1,28,y,label);
        if(i==launcher->selected_level)rdpq_text_print(NULL,1,14,y,"^01>");
    }
    box(10,180,310,181,rgb(75,65,43));
    const DlLevel *level=bundle->levels[launcher->selected_level].load();
    const DlStartPreset *preset=launcher->selected_start>=0?&level->test_starts[launcher->selected_start]:NULL;
    char label[49];menu_label(label,preset?preset->label:"Default spawn",36);
    rdpq_text_printf(NULL,1,12,194,"^01Start:^00 %s",label);
    if(preset)rdpq_text_printf(NULL,1,12,207,"^02%s / gate %s",preset->crouched?"Crouched":"Standing",preset->door_open?"open":"closed");
    else rdpq_text_print(NULL,1,12,207,"^02Fresh level at its player start");
    rdpq_text_print(NULL,1,12,221,"Up/Down: level   Left/Right: start");
    rdpq_text_print(NULL,1,12,235,launcher->has_active?"A/START: launch   B: resume":"A/START: launch");
    dl_profile_record(DL_PROFILE_HUD,mark);
    mark=dl_profile_mark();
#ifdef DL_MENU_TEST
    if(capture_id){rdpq_detach();capture_frame(display);display_show(display);}
    else rdpq_detach_show();
#else
    rdpq_detach_show();
#endif
    dl_profile_record(DL_PROFILE_RENDER_SETUP,mark);
    return (float)(uint32_t)(TICKS_READ()-start)*(1000.0f/TICKS_PER_SECOND);
}
