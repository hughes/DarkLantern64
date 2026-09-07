#ifndef DARKLANTERN_RENDER_SHADING_H
#define DARKLANTERN_RENDER_SHADING_H

#include "game.h"
#include <math.h>

/* Normals use inverse-transpose scale, followed by the same hinge and XYZ
 * rotations as positions. Normalizing also removes signed-byte quantization's
 * small length error; the common 1/127 factor cancels out. */
static inline DlVec3 dl_render_normal_vector(DlVec3 normal,DlVec3 scale,DlVec3 sine,DlVec3 cosine,bool door_open){
    DlVec3 n={normal.x/scale.x,normal.y/scale.y,normal.z/scale.z};
    if(door_open)n=(DlVec3){n.z,n.y,-n.x};
    n=(DlVec3){n.x,cosine.x*n.y-sine.x*n.z,sine.x*n.y+cosine.x*n.z};
    n=(DlVec3){cosine.y*n.x+sine.y*n.z,n.y,-sine.y*n.x+cosine.y*n.z};
    n=(DlVec3){cosine.z*n.x-sine.z*n.y,sine.z*n.x+cosine.z*n.y,n.z};
    float length=sqrtf(n.x*n.x+n.y*n.y+n.z*n.z);
    return length>0.000001f?(DlVec3){n.x/length,n.y/length,n.z/length}:(DlVec3){0,1,0};
}
static inline DlVec3 dl_render_normal(DlNormal packed,DlVec3 scale,DlVec3 sine,DlVec3 cosine,bool door_open){
    return dl_render_normal_vector((DlVec3){packed.x,packed.y,packed.z},scale,sine,cosine,door_open);
}

/* An opt-in presentation cue, not a world light. Uses no new geometry,
 * texture, particle state or scene illumination queries. Full effect within
 * 4m; fades completely by 8m. Each placed model receives a stable phase. */
static inline float dl_loot_highlight_lift(float seconds,float distance,float phase){
    if(distance>=8)return 0;
    float fade=distance<=4?1:(8-distance)*0.25f;
    return (0.14f+0.64f*(0.5f-0.5f*cosf(seconds*(6.283185307f/3.6f)+phase)))*fade;
}

#endif
