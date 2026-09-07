#ifndef DARKLANTERN_RENDER_BATCHES_H
#define DARKLANTERN_RENDER_BATCHES_H

#include "game.h"
#include <stddef.h>

#define DL_RENDER_BATCH_VERTICES 70
typedef struct {
    uint16_t source_vertex;
    uint16_t triangle_index; /* exact source face for flat color/normal lookup */
} DlRenderVertexRef;
typedef struct {
    uint8_t bone;
    uint8_t vertex_start; /* local cache index, always even */
    uint8_t vertex_count; /* includes duplicate padding; always even */
} DlRenderBatchGroup;
typedef struct {
    uint32_t vertex_offset,group_offset,index_offset;
    uint16_t index_count;
    uint8_t vertex_count,group_count;
} DlRenderBatch;
typedef struct {
    DlRenderBatch *batches;
    DlRenderVertexRef *vertices;
    DlRenderBatchGroup *groups;
    uint8_t *indices; /* cache-local indices; source triangle order preserved */
    uint32_t batch_count,vertex_count,group_count,index_count;
    size_t allocated_bytes;
} DlRenderBatches;

/* Caller zero-initializes out. Build once per immutable mesh; all instances
 * share it. No mesh data is copied or changed. Bone groups use pair-aligned
 * starts/counts for T3D vertex loads; their padding copies an existing vertex.
 * Smooth meshes share source vertices. Flat meshes use (source,triangle) keys
 * so adjacent faces can have independent normals and colors. Mixed-bone
 * triangles retain the exact original connectivity. Empty meshes are valid.
 * On failure, out is unchanged. Free before rebuilding an existing output. */
bool dl_render_batches_build(const DlMesh *mesh,DlRenderBatches *out);
void dl_render_batches_free(DlRenderBatches *batches);

#endif
