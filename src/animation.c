#include "animation.h"

#include <math.h>
#include <string.h>

static float clampf(float v,float a,float b){return v<a?a:v>b?b:v;}
static DlVec3 add(DlVec3 a,DlVec3 b){return (DlVec3){a.x+b.x,a.y+b.y,a.z+b.z};}
static DlVec3 scale(DlVec3 a,float b){return (DlVec3){a.x*b,a.y*b,a.z*b};}
static DlVec3 cross(DlVec3 a,DlVec3 b){return (DlVec3){a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
static DlQuat normalized(DlQuat q){
    float l=q.x*q.x+q.y*q.y+q.z*q.z+q.w*q.w;
    if(!(l>0.000001f)||!isfinite(l))return (DlQuat){0,0,0,1};
    float inv=1/sqrtf(l);return (DlQuat){q.x*inv,q.y*inv,q.z*inv,q.w*inv};
}
static DlQuat product(DlQuat a,DlQuat b){return (DlQuat){
    a.w*b.x+a.x*b.w+a.y*b.z-a.z*b.y,
    a.w*b.y-a.x*b.z+a.y*b.w+a.z*b.x,
    a.w*b.z+a.x*b.y-a.y*b.x+a.z*b.w,
    a.w*b.w-a.x*b.x-a.y*b.y-a.z*b.z};}
static DlVec3 rotate(DlQuat q,DlVec3 p){
    DlVec3 v={q.x,q.y,q.z},t=scale(cross(v,p),2);
    return add(p,add(scale(t,q.w),cross(v,t)));
}
static DlQuat mixq(DlQuat a,DlQuat b,float t){
    float sign=a.x*b.x+a.y*b.y+a.z*b.z+a.w*b.w<0?-1:1;
    float s=1-t;t*=sign;
    return normalized((DlQuat){a.x*s+b.x*t,a.y*s+b.y*t,a.z*s+b.z*t,a.w*s+b.w*t});
}
static DlAnimTransform compose(DlAnimTransform a,DlAnimTransform b){
    return (DlAnimTransform){add(a.translation,rotate(a.rotation,b.translation)),product(a.rotation,b.rotation)};
}
static DlAnimMatrix matrix(DlAnimTransform t){
    DlQuat q=normalized(t.rotation);
    return (DlAnimMatrix){{1-2*(q.y*q.y+q.z*q.z),2*(q.x*q.y-q.z*q.w),2*(q.x*q.z+q.y*q.w),t.translation.x,
        2*(q.x*q.y+q.z*q.w),1-2*(q.x*q.x+q.z*q.z),2*(q.y*q.z-q.x*q.w),t.translation.y,
        2*(q.x*q.z-q.y*q.w),2*(q.y*q.z+q.x*q.w),1-2*(q.x*q.x+q.y*q.y),t.translation.z}};
}
static bool finite_transform(DlAnimTransform t){
    float length=t.rotation.x*t.rotation.x+t.rotation.y*t.rotation.y+t.rotation.z*t.rotation.z+t.rotation.w*t.rotation.w;
    return isfinite(t.translation.x)&&isfinite(t.translation.y)&&isfinite(t.translation.z)&&
        isfinite(length)&&fabsf(length-1)<0.01f;
}
bool dl_animation_validate(const DlAnimationAsset *a,int vertex_count){
    if(!a||!a->bones||!a->bone_count||a->bone_count>DL_ANIMATION_MAX_BONES||
       !a->vertex_bones||vertex_count<0||!isfinite(a->bounds_radius)||a->bounds_radius<=0||
       !isfinite(a->bounds_center.x)||!isfinite(a->bounds_center.y)||!isfinite(a->bounds_center.z)||
       a->head_bone < -1||a->head_bone>=a->bone_count||a->clip_count>32||(a->clip_count&&!a->clips))return false;
    for(int b=0;b<a->bone_count;++b)if(a->bones[b].parent < -1||a->bones[b].parent>=b||
        !finite_transform(a->bones[b].rest)||!finite_transform(a->bones[b].inverse_bind))return false;
    for(int v=0;v<vertex_count;++v)if(a->vertex_bones[v]>=a->bone_count)return false;
    for(int c=0;c<a->clip_count;++c){
        const DlAnimClip *clip=&a->clips[c];
        if(!clip->id||!isfinite(clip->duration)||clip->duration<=0||clip->sample_count<2||clip->sample_count>4096||
           clip->track_count>2*a->bone_count||clip->event_count>256||
           !isfinite(clip->translation_scale)||clip->translation_scale<=0||
           !isfinite(clip->stride_length)||clip->stride_length<0||
           (clip->track_count&&!clip->tracks)||(clip->event_count&&!clip->events))return false;
        uint8_t seen[DL_ANIMATION_MAX_BONES]={0};
        for(int t=0;t<clip->track_count;++t){
            const DlAnimTrack *track=&clip->tracks[t];
            if(track->bone>=a->bone_count||track->channel>DL_ANIM_TRANSLATION||!track->values||
               (track->sample_count!=1&&track->sample_count!=clip->sample_count)||
               (seen[track->bone]&(1u<<track->channel)))return false;
            seen[track->bone]|=(uint8_t)(1u<<track->channel);
        }
    }
    if(a->socket_count>32||(a->socket_count&&!a->sockets))return false;
    for(int s=0;s<a->socket_count;++s)if(a->sockets[s].bone>=a->bone_count||!finite_transform(a->sockets[s].local))return false;
    return true;
}
static void sample(const DlAnimationAsset *a,int ci,float time,DlAnimTransform *out){
    for(int b=0;b<a->bone_count;++b)out[b]=a->bones[b].rest;
    if(ci<0||ci>=a->clip_count)return;
    const DlAnimClip *c=&a->clips[ci];
    if(!isfinite(time))time=0;
    if(c->loop){time=fmodf(time,c->duration);if(time<0)time+=c->duration;}
    else time=clampf(time,0,c->duration);
    float frame=time/c->duration*(c->sample_count-1);
    int left=(int)frame,right=left+1<c->sample_count?left+1:left;
    float blend=frame-left;
    for(int t=0;t<c->track_count;++t){
        const DlAnimTrack *track=&c->tracks[t];
        int l=track->sample_count==1?0:left,r=track->sample_count==1?0:right;
        if(track->channel==DL_ANIM_ROTATION){
            const int16_t *p=track->values+l*4,*q=track->values+r*4;
            DlQuat qa={p[0]/32767.0f,p[1]/32767.0f,p[2]/32767.0f,p[3]/32767.0f};
            DlQuat qb={q[0]/32767.0f,q[1]/32767.0f,q[2]/32767.0f,q[3]/32767.0f};
            out[track->bone].rotation=mixq(qa,qb,blend);
        }else{
            const int16_t *p=track->values+l*3,*q=track->values+r*3;
            out[track->bone].translation=(DlVec3){(p[0]+(q[0]-p[0])*blend)*c->translation_scale,
                (p[1]+(q[1]-p[1])*blend)*c->translation_scale,(p[2]+(q[2]-p[2])*blend)*c->translation_scale};
        }
    }
}
bool dl_animation_pose(const DlAnimationAsset *a,int ca,float ta,int cb,float tb,float blend,
                       float yaw,float pitch,DlAnimMatrix *skin){
    /* Full validation belongs to asset load. This hot path only rejects shape
     * errors; callers must not pass unvalidated external track buffers. */
    if(!a||!a->bones||!a->bone_count||a->bone_count>DL_ANIMATION_MAX_BONES||!skin)return false;
    DlAnimTransform pose[DL_ANIMATION_MAX_BONES],other[DL_ANIMATION_MAX_BONES];
    blend=isfinite(blend)?clampf(blend,0,1):0;
    sample(a,blend>=1?cb:ca,blend>=1?tb:ta,pose);
    if(blend>0&&blend<1){
        sample(a,cb,tb,other);
        for(int b=0;b<a->bone_count;++b){
            pose[b].translation=add(scale(pose[b].translation,1-blend),scale(other[b].translation,blend));
            pose[b].rotation=mixq(pose[b].rotation,other[b].rotation,blend);
        }
    }
    yaw=isfinite(yaw)?clampf(yaw,-0.7853982f,0.7853982f):0;
    pitch=isfinite(pitch)?clampf(pitch,-0.4363323f,0.4363323f):0;
    DlQuat attention=product((DlQuat){0,sinf(yaw*.5f),0,cosf(yaw*.5f)},
        (DlQuat){sinf(pitch*.5f),0,0,cosf(pitch*.5f)});
    for(int b=0;b<a->bone_count;++b){
        int parent=a->bones[b].parent;
        if(parent < -1||parent>=b)return false;
        if(parent>=0)pose[b]=compose(pose[parent],pose[b]);
        /* Rotate head around its posed pivot in model axes. This works with
         * arbitrarily oriented Blender edit bones and does not move AI eyes. */
        if(b==a->head_bone)pose[b].rotation=product(attention,pose[b].rotation);
        skin[b]=matrix(compose(pose[b],a->bones[b].inverse_bind));
    }
    return true;
}
DlVec3 dl_animation_point(const DlAnimMatrix *s,DlVec3 p){const float *m=s->m;
    return (DlVec3){m[0]*p.x+m[1]*p.y+m[2]*p.z+m[3],m[4]*p.x+m[5]*p.y+m[6]*p.z+m[7],m[8]*p.x+m[9]*p.y+m[10]*p.z+m[11]};}
DlVec3 dl_animation_normal(const DlAnimMatrix *s,DlVec3 n){const float *m=s->m;
    return (DlVec3){m[0]*n.x+m[1]*n.y+m[2]*n.z,m[4]*n.x+m[5]*n.y+m[6]*n.z,m[8]*n.x+m[9]*n.y+m[10]*n.z};}
int dl_animation_find_clip(const DlAnimationAsset *a,const char *id){
    if(a&&id)for(int c=0;c<a->clip_count;++c)if(!strcmp(a->clips[c].id,id))return c;
    return -1;
}
unsigned dl_animation_event_count(const DlAnimClip *c,float from,float to,uint8_t kind){
    if(!c||!isfinite(from)||!isfinite(to)||to<=from||!(c->duration>0))return 0;
    if(!c->loop){from=clampf(from,0,c->duration);to=clampf(to,0,c->duration);}
    unsigned count=0;
    for(int e=0;e<c->event_count;++e){
        if(c->events[e].kind!=kind)continue;
        double event=(double)c->events[e].phase/65535.0*c->duration;
        double crossings=c->loop?floor(((double)to-event)/c->duration)-floor(((double)from-event)/c->duration):
            (event>from&&event<=to?1:0);
        if(crossings>1000000)return 1000000; /* malformed caller cannot overflow */
        if(crossings>0)count+=(unsigned)crossings;
    }
    return count;
}
uint32_t dl_animation_scratch_bytes(void){return 2*sizeof(DlAnimTransform)*DL_ANIMATION_MAX_BONES;}
