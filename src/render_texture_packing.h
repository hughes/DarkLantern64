#ifndef DARKLANTERN_RENDER_TEXTURE_PACKING_H
#define DARKLANTERN_RENDER_TEXTURE_PACKING_H

#include "render_batches.h"

typedef enum {
    DL_TEXTURE_PACK_OK,
    DL_TEXTURE_PACK_INVALID_INPUT,
    DL_TEXTURE_PACK_NONFINITE_UV,
    DL_TEXTURE_PACK_SPAN_TOO_WIDE,
    DL_TEXTURE_PACK_NO_REPEAT_BASE
} DlTexturePackStatus;
typedef struct {
    DlTexturePackStatus status;
    uint32_t batch,source_vertex; /* UINT32_MAX when no specific item applies */
    unsigned axis; /* 0=U, 1=V */
    double span_texels;
} DlTexturePackError;

/* Prepare signed 10.5 texture coordinates for packed batch vertices. Caller
 * provides 2*batches->vertex_count int16_t entries; no heap allocation occurs.
 * One integer UV-repeat offset per axis/batch preserves the texture's phase
 * and interpolation, even for large authored offsets. Whole-batch spans over
 * 2047 texels are rejected. A narrower span can also fail if no integer-period
 * shift places it inside s16. Padding copies retain identical coordinates.
 * Validation finishes before output is written. On failure output is unchanged.
 * Width/height are positive pixel dimensions; caller validates hardware TMEM
 * and power-of-two requirements separately. Input assets must remain immutable. */
bool dl_render_texture_pack(const DlMesh *mesh,const DlRenderBatches *batches,
    int width,int height,int16_t *out_uv_pairs);
bool dl_render_texture_pack_ex(const DlMesh *mesh,const DlRenderBatches *batches,
    int width,int height,int16_t *out_uv_pairs,DlTexturePackError *error);
const char *dl_render_texture_pack_status_name(DlTexturePackStatus status);

#endif
