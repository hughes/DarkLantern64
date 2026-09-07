#ifndef DARKLANTERN_ANIMATION_H
#define DARKLANTERN_ANIMATION_H

#include "game.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DL_ANIMATION_MAX_BONES 32
typedef struct { float x,y,z,w; } DlQuat;
typedef struct { DlVec3 translation; DlQuat rotation; } DlAnimTransform;
/* Row-major affine matrix: rows [rotation XYZ, translation]. */
typedef struct { float m[12]; } DlAnimMatrix;
typedef struct {
    const char *name;
    int8_t parent; /* -1 root, otherwise an earlier bone */
    DlAnimTransform rest, inverse_bind;
} DlAnimBone;
typedef enum { DL_ANIM_ROTATION, DL_ANIM_TRANSLATION } DlAnimChannel;
typedef struct {
    uint8_t bone, channel;
    uint16_t sample_count; /* 1 constant, otherwise clip.sample_count */
    const int16_t *values; /* XYZW /32767 or XYZ * clip.translation_scale */
} DlAnimTrack;
typedef enum { DL_ANIM_FOOT_LEFT, DL_ANIM_FOOT_RIGHT } DlAnimEventKind;
typedef struct { uint16_t phase; uint8_t kind; } DlAnimEvent;
typedef struct {
    const char *id;
    float duration, stride_length, translation_scale;
    uint16_t sample_count, track_count;
    bool loop;
    const DlAnimTrack *tracks;
    const DlAnimEvent *events;
    uint16_t event_count;
} DlAnimClip;
typedef struct { const char *id; uint8_t bone; DlAnimTransform local; } DlAnimSocket;
typedef struct DlAnimationAsset {
    const char *id;
    const DlAnimBone *bones;
    uint16_t bone_count;
    const uint8_t *vertex_bones; /* one influence; vertex count equals DlMesh */
    const DlAnimClip *clips;
    uint16_t clip_count;
    int16_t head_bone; /* -1 disables bounded local head adjustment */
    DlVec3 bounds_center;
    float bounds_radius; /* conservative for every clip AND attention overlay */
    uint32_t encoded_bytes; /* key payload; excludes pointers/mesh/skeleton */
    const DlAnimSocket *sockets;
    uint16_t socket_count;
} DlAnimationAsset;

/* No heap allocations; shared immutable assets, caller-owned 32-matrix output.
 * Uniform clip keys include both endpoints. Negative clip index means rest.
 * Blend is normalized linear quaternion interpolation, shortest hemisphere.
 * Head overlay is model-relative yaw (+Y) then pitch (+X), bounded internally.
 * Validate external assets once before using this allocation-free hot path. */
bool dl_animation_validate(const DlAnimationAsset *asset, int vertex_count);
bool dl_animation_pose(const DlAnimationAsset *asset, int clip_a, float time_a,
    int clip_b, float time_b, float blend, float head_yaw, float head_pitch,
    DlAnimMatrix *skin);
DlVec3 dl_animation_point(const DlAnimMatrix *skin, DlVec3 point);
DlVec3 dl_animation_normal(const DlAnimMatrix *skin, DlVec3 normal);
int dl_animation_find_clip(const DlAnimationAsset *asset,const char *id);
/* Count marker crossings in (from,to], including loops and long frame gaps.
 * Animation event evaluation is gameplay work, never visibility-dependent. */
unsigned dl_animation_event_count(const DlAnimClip *clip,float from,float to,uint8_t kind);
uint32_t dl_animation_scratch_bytes(void);

#ifdef __cplusplus
}
#endif
#endif
