#ifndef DARKLANTERN_RENDER_LIGHTING_H
#define DARKLANTERN_RENDER_LIGHTING_H

#include "animation.h"

/* Lighting uses the transformed normal and an actor's shared body probes.
 * UV seams and disconnected vertices with the same packed normal on the same
 * joint therefore have identical colors. Build once per immutable mesh;
 * instances may share both tables. Each output has vertex_count capacity.
 * Returns the number of groups, or -1 for invalid input before writing.
 * Flat meshes and per-vertex world-light probes must keep their existing path. */
int dl_lighting_build_groups(const DlMesh *mesh, uint16_t *vertex_groups,
    uint16_t *representatives, int capacity);

typedef struct { uint8_t r,g,b,a; } DlLightingColor;
typedef struct { DlLightingColor front,back; } DlLightingPair;
typedef struct {
    float red,green,blue;
    float exposure,red_add,green_add;
    bool objective;
} DlScalarLighting;

/* Scalar light is the unchanged average of the two world/occlusion probes.
 * Preparation hoists model state and light invariants out of the normal loop.
 * These helpers never perform, cache, or suppress a world-light query. */
void dl_lighting_scalar_prepare(const DlGame *game,const DlModelInstance *model,
    float light,DlScalarLighting *out);
DlLightingPair dl_lighting_scalar_pair(const DlScalarLighting *lighting,
    DlVec3 normal,bool double_sided);

/* The caller computes its existing night_color once from the two body probes.
 * This is only the current animated-actor directional presentation term. */
DlLightingPair dl_lighting_night_pair(DlLightingColor body_color,
    DlVec3 normal,bool double_sided);

typedef struct { DlVec3 unit_normal;uint8_t bone; } DlLightingNormalGroup;
/* Shared immutable group data. Representative indices come from
 * dl_lighting_build_groups. Rejects zero packed normals and invalid indices
 * before writing. This is the only normalization needed for uniform actors. */
bool dl_lighting_prepare_normals(const DlMesh *mesh,const uint16_t *representatives,
    int count,DlLightingNormalGroup *out);

/* A rigid bone and a positive uniform instance scale preserve normal length.
 * Transform the presentation-light direction into each bone's local space
 * once, then shade each group with one dot product. Anisotropic or reflected
 * scales use the full normalized matrix-normal fallback. The normal palette
 * is from dl_render_normal_matrix; it already includes inverse instance scale.
 * Floating reassociation can differ from per-normal renormalization by a
 * final color unit at byte boundaries; the formulas/body probes are unchanged.
 * groups/palette/counts must be valid, bone_count <= DL_ANIMATION_MAX_BONES. */
void dl_lighting_scalar_groups(const DlScalarLighting *lighting,
    const DlLightingNormalGroup *groups,int count,const DlAnimMatrix *normal_palette,
    int bone_count,DlVec3 instance_scale,bool double_sided,DlLightingPair *out);
void dl_lighting_night_groups(DlLightingColor body_color,
    const DlLightingNormalGroup *groups,int count,const DlAnimMatrix *normal_palette,
    int bone_count,DlVec3 instance_scale,bool double_sided,DlLightingPair *out);

#endif
