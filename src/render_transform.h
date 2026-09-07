#ifndef DARKLANTERN_RENDER_TRANSFORM_H
#define DARKLANTERN_RENDER_TRANSFORM_H

#include "animation.h"
#include "render_camera.h"

/* Reuses animation's row-major 3x4 affine format. No trigonometry in these
 * helpers: compute sine/cosine once per instance. Scale must be nonzero and
 * all inputs finite. The hinge is in scaled mesh space, exactly as render.c:
 * S -> optional H about (hinge_x,hinge_z) -> Rx -> Ry -> Rz -> translation. */
void dl_render_instance_matrix(DlAnimMatrix *out,DlVec3 position,DlVec3 scale,
    DlVec3 sine,DlVec3 cosine,bool door_open,float hinge_x,float hinge_z);

/* out = left * right; either input may alias out. For animated positions use
 * instance * skin, where skin is animation's bind-to-current bone transform. */
void dl_render_affine_compose(DlAnimMatrix *out,const DlAnimMatrix *left,
    const DlAnimMatrix *right);
DlVec3 dl_render_matrix_point(const DlAnimMatrix *matrix,DlVec3 point);

/* Inverse-transpose instance scale, then hinge/Euler, applied after the
 * optional rigid bone rotation: Rz*Ry*Rx*H*S^-1*boneRotation. Translation is
 * zero. Build once per bone/instance and normalize each distinct normal. */
void dl_render_normal_matrix(DlAnimMatrix *out,DlVec3 scale,DlVec3 sine,
    DlVec3 cosine,bool door_open,const DlAnimMatrix *bone);
DlVec3 dl_render_matrix_normal(const DlAnimMatrix *matrix,DlVec3 normal);

/* Build a camera-relative modelview for integer packed mesh positions.
 * world maps original metre coordinates into world metres. Output maps
 * packed positions to GPU units using camera right/up/-forward (RH -Z).
 * Typical units: gpu_units_per_metre=64, packed_units_per_metre=1024.
 * Both scales must be positive finite values. Projection is separate.
 * Subtract the eye before camera rotation to retain local precision. */
void dl_render_modelview(DlAnimMatrix *out,const DlAnimMatrix *world,
    DlVec3 eye,DlCameraBasis camera,float gpu_units_per_metre,
    float packed_units_per_metre);

/* T3DMat4.m uses column-major [column][row]. The last row is {0,0,0,1}.
 * Passing a T3DMat4's .m requires no casts or dependency on Tiny3D here. */
void dl_render_matrix_column_major(float out[4][4],const DlAnimMatrix *matrix);

#endif
