#include "animation.h"
#include "render_shading.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>

static void near(float a,float b){if(fabsf(a-b)>.00015f){fprintf(stderr,"got %.7f expected %.7f\n",a,b);assert(0);}}
static void vector(DlVec3 a,DlVec3 b){near(a.x,b.x);near(a.y,b.y);near(a.z,b.z);}
static float length(DlVec3 a){return sqrtf(a.x*a.x+a.y*a.y+a.z*a.z);}
static const uint8_t joints[]={0,1,1};
static const DlAnimBone bones[]={
    {.name="root",.parent=-1,.rest={{0,0,0},{0,0,0,1}},.inverse_bind={{0,0,0},{0,0,0,1}}},
    {.name="head",.parent=0,.rest={{0,1,0},{0,0,0,1}},.inverse_bind={{0,-1,0},{0,0,0,1}}},
};
static const int16_t turn[]={0,0,0,32767, 0,23170,0,23170};
static const int16_t move[]={0,0,0, 4096,0,0};
static const int16_t antipodal[]={0,0,0,32767, 0,0,0,-32767};
static const DlAnimTrack tracks[]={
    {0,DL_ANIM_ROTATION,2,turn},{0,DL_ANIM_TRANSLATION,2,move},
};
static const DlAnimTrack constant_tracks[]={{0,DL_ANIM_ROTATION,1,turn}};
static const DlAnimTrack anti_tracks[]={{0,DL_ANIM_ROTATION,2,antipodal}};
static const DlAnimEvent events[]={{0,DL_ANIM_FOOT_LEFT},{32768,DL_ANIM_FOOT_RIGHT}};
static const DlAnimClip clips[]={
    {.id="turn",.duration=1,.stride_length=2,.translation_scale=1/4096.0f,.sample_count=2,.track_count=2,.loop=false,.tracks=tracks},
    {.id="loop",.duration=1,.translation_scale=1/4096.0f,.sample_count=2,.track_count=1,.loop=true,.tracks=anti_tracks,.events=events,.event_count=2},
    {.id="constant",.duration=1,.translation_scale=1/4096.0f,.sample_count=2,.track_count=1,.loop=true,.tracks=constant_tracks},
};
static const DlAnimationAsset asset={.id="test",.bones=bones,.bone_count=2,.vertex_bones=joints,.clips=clips,.clip_count=3,.head_bone=1,.bounds_radius=3};
int main(void){
    assert(dl_animation_validate(&asset,3));
    DlAnimMatrix skin[DL_ANIMATION_MAX_BONES],other[DL_ANIMATION_MAX_BONES];
    /* Inverse bind cancels the entire rest hierarchy, including translated joints. */
    assert(dl_animation_pose(&asset,-1,0,-1,0,0,0,0,skin));
    vector(dl_animation_point(&skin[1],(DlVec3){.5f,1.2f,.3f}),(DlVec3){.5f,1.2f,.3f});
    /* Absolute local translation and rotation interpolation compose parent-first. */
    assert(dl_animation_pose(&asset,0,.5f,-1,0,0,0,0,skin));
    float root=.70710678f;
    vector(dl_animation_point(&skin[1],(DlVec3){0,1,1}),(DlVec3){.5f+root,1,root});
    vector(dl_animation_normal(&skin[1],(DlVec3){0,0,1}),(DlVec3){root,0,root});
    /* Clip endpoints clamp; antipodal quaternion keys and negative loop times do not spin. */
    assert(dl_animation_pose(&asset,0,9,-1,0,0,0,0,skin));
    vector(dl_animation_point(&skin[1],(DlVec3){0,1,1}),(DlVec3){2,1,0});
    assert(dl_animation_pose(&asset,1,-.5f,-1,0,0,0,0,skin));
    vector(dl_animation_point(&skin[1],(DlVec3){0,1,1}),(DlVec3){0,1,1});
    assert(dl_animation_pose(&asset,2,.8f,-1,0,0,0,0,skin));
    vector(dl_animation_point(&skin[1],(DlVec3){0,1,1}),(DlVec3){0,1,1});
    /* Crossfade shares the same shortest-arc interpolation as within a clip. */
    assert(dl_animation_pose(&asset,-1,0,0,1,.5f,0,0,skin));
    vector(dl_animation_point(&skin[1],(DlVec3){0,1,1}),(DlVec3){.5f+root,1,root});
    /* Procedural attention clamps at the posed pivot, not the model origin. */
    assert(dl_animation_pose(&asset,-1,0,-1,0,0,10,10,skin));
    assert(dl_animation_pose(&asset,-1,0,-1,0,0,.7853982f,.4363323f,other));
    for(int i=0;i<12;++i)near(skin[1].m[i],other[1].m[i]);
    vector(dl_animation_point(&skin[1],(DlVec3){0,1,0}),(DlVec3){0,1,0});
    vector(dl_animation_point(&skin[0],(DlVec3){0,0,1}),(DlVec3){0,0,1});
    DlVec3 normal=dl_animation_normal(&skin[1],(DlVec3){0,0,1});
    near(length(normal),1);
    DlVec3 world_normal=dl_render_normal_vector(normal,(DlVec3){2,.5f,3},(DlVec3){0,0,0},(DlVec3){1,1,1},false);
    near(length(world_normal),1);
    DlVec3 tangent=dl_animation_normal(&skin[1],(DlVec3){1,0,0});
    near(world_normal.x*tangent.x*2+world_normal.y*tangent.y*.5f+world_normal.z*tangent.z*3,0);
    /* Logical marker interval excludes its starting boundary and counts skipped loops. */
    assert(dl_animation_event_count(&clips[1],0,1,DL_ANIM_FOOT_LEFT)==1);
    assert(dl_animation_event_count(&clips[1],0,.5f,DL_ANIM_FOOT_RIGHT)==0);
    assert(dl_animation_event_count(&clips[1],0,.5001f,DL_ANIM_FOOT_RIGHT)==1);
    assert(dl_animation_event_count(&clips[1],.2f,3.6f,DL_ANIM_FOOT_RIGHT)==4);
    assert(dl_animation_event_count(&clips[1],1,1,DL_ANIM_FOOT_LEFT)==0);
    assert(dl_animation_event_count(&clips[1],NAN,2,DL_ANIM_FOOT_LEFT)==0);
    assert(dl_animation_find_clip(&asset,"constant")==2&&dl_animation_find_clip(&asset,"missing")==-1);
    DlAnimationAsset bad=asset;bad.bone_count=33;assert(!dl_animation_validate(&bad,3));
    bad=asset;bad.head_bone=2;assert(!dl_animation_validate(&bad,3));
    uint8_t bad_joints[]={2};bad=asset;bad.vertex_bones=bad_joints;assert(!dl_animation_validate(&bad,1));
    DlAnimBone bad_bones[2]={bones[0],bones[1]};bad_bones[1].parent=1;bad=asset;bad.bones=bad_bones;assert(!dl_animation_validate(&bad,3));
    bad=asset;bad.bounds_radius=NAN;assert(!dl_animation_validate(&bad,3));
    printf("Animation: hierarchy, inverse binds, quantized tracks, interpolation, crossfade, attention, normals, event intervals and invalid data passed; scratch=%u matrix=%u\n",
        (unsigned)dl_animation_scratch_bytes(),(unsigned)sizeof(skin));
    return 0;
}
