#ifndef DARKLANTERN_RENDER_CAMERA_H
#define DARKLANTERN_RENDER_CAMERA_H

#include "game.h"
#include <math.h>

typedef struct { DlVec3 right,up,forward; } DlCameraBasis;

/* Authored yaw 0 faces world +Z, yaw pi faces -Z; positive pitch looks up.
 * The world shares LightEngine/glTF's right-handed XYZ axes. Camera depth is
 * positive in front, so right x up = -forward, as in a conventional RH view
 * with its negative camera-Z converted to positive depth for the N64 clipper. */
static inline DlCameraBasis dl_camera_basis(float yaw,float pitch){
    float sy=sinf(yaw),cy=cosf(yaw),sp=sinf(pitch),cp=cosf(pitch);
    return (DlCameraBasis){.right={-cy,0,sy},
        .up={-sy*sp,cp,-cy*sp},.forward={sy*cp,sp,cy*cp}};
}

#endif
