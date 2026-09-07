#ifndef DARKLANTERN_STATIC_LIGHTING_H
#define DARKLANTERN_STATIC_LIGHTING_H

#include "game.h"
#include <stddef.h>

/* Exactly the renderer's NightColors object representation, without a
 * libdragon dependency. Channels are RGBA; alpha is always 255. */
typedef struct {
    uint8_t front[3][4], back[3][4];
} DlStaticLightingColor;

enum { DL_STATIC_LIGHTING_VERSION = 1, DL_STATIC_LIGHTING_HEADER_BYTES = 64,
       DL_STATIC_LIGHTING_RECORD_BYTES = 12 };

/* Includes static props, lamps, control and the door in its requested state.
 * Actors, the rotating objective and every animated mesh remain live. */
bool dl_static_lighting_eligible(const DlLevel *level, int model_index);
/* Writes one mesh's triangles, in authored order. No allocation or global
 * scratch. output can point at the matching range of the renderer cache. */
bool dl_static_lighting_model(const DlLevel *level, int model_index, bool door_open,
                              DlStaticLightingColor *output, size_t triangle_capacity);

#endif
