#ifndef DARKLANTERN_RENDER_VISIBILITY_H
#define DARKLANTERN_RENDER_VISIBILITY_H

#include "game.h"
#include <math.h>

/* Camera space looks along +Z. Slopes describe the symmetric horizontal and
 * vertical clip planes; their lengths are cached once, not per model. */
typedef struct {
    float near_z,far_z,horizontal,vertical,horizontal_length,vertical_length;
} DlRenderFrustum;

static inline DlRenderFrustum dl_render_frustum(float near_z,float far_z,float horizontal,float vertical){
    return (DlRenderFrustum){near_z,far_z,horizontal,vertical,
        sqrtf(1+horizontal*horizontal),sqrtf(1+vertical*vertical)};
}

/* Reject only a sphere wholly outside a plane. A sphere intersecting a plane,
 * enclosing the camera, or spanning the near plane must reach triangle clipping.
 * The small outward margin accommodates rounding in bounds and camera rotation. */
static inline bool dl_render_sphere_visible(const DlRenderFrustum *frustum,DlVec3 center,float radius){
    float reach=radius+0.001f;
    float horizontal=center.z*frustum->horizontal;
    float vertical=center.z*frustum->vertical;
    float horizontal_reach=reach*frustum->horizontal_length;
    float vertical_reach=reach*frustum->vertical_length;
    return !(center.z-frustum->near_z < -reach || frustum->far_z-center.z < -reach ||
        center.x+horizontal < -horizontal_reach || horizontal-center.x < -horizontal_reach ||
        vertical-center.y < -vertical_reach || center.y+vertical < -vertical_reach);
}

#endif
