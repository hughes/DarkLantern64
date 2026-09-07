#ifndef DARKLANTERN_LIGHTING_BAKE_H
#define DARKLANTERN_LIGHTING_BAKE_H

#include "game.h"
#include <stddef.h>

typedef size_t (*DlLightingBakeRead)(void *context,void *destination,size_t bytes);
typedef enum {
    DL_LIGHTING_BAKE_OK,DL_LIGHTING_BAKE_ARGUMENT,DL_LIGHTING_BAKE_PATH,
    DL_LIGHTING_BAKE_HEADER,DL_LIGHTING_BAKE_IDENTITY,DL_LIGHTING_BAKE_LAYOUT,
    DL_LIGHTING_BAKE_TRUNCATED,DL_LIGHTING_BAKE_CHECKSUM
} DlLightingBakeStatus;
typedef struct {
    DlLightingBakeStatus status;
    uint32_t models,triangles,payload_bytes,crc32;
} DlLightingBakeResult;

/* DLC1 v1 streaming decode into two existing full-scene 24-byte RGBA triangle
 * arrays (front[3][4], back[3][4]). Dynamic-model slots remain untouched.
 * A failed decode may have written part of the destination; caller MUST fully
 * recompute lighting before using it. Read may return short chunks or zero at
 * EOF. The declared file size must be the actual size. No allocation/seeking. */
DlLightingBakeResult dl_lighting_bake_decode(const DlLevel *level,
    DlLightingBakeRead read,void *context,size_t file_bytes,
    void *closed_colors,void *open_colors,size_t triangle_capacity);
const char *dl_lighting_bake_status_name(DlLightingBakeStatus status);

#endif
