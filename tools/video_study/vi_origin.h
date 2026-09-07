#ifndef DL_VIDEO_STUDY_VI_ORIGIN_H
#define DL_VIDEO_STUDY_VI_ORIGIN_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Registered physical framebuffer bases, never virtual pointers. For the
 * staged libdragon interlaced mode, VI_ORIGIN alternates between the base and
 * base + one scanline stride. Pass stride=0 for progressive output. This only
 * identifies the source framebuffer; it does not prove a coherent field pair.
 * Unknown or ambiguous addresses fail closed and leave *base_out unchanged. */
static inline bool dl_video_base_origin(uint32_t raw, const uint32_t *bases,
                                        unsigned count, uint32_t stride,
                                        uint32_t *base_out) {
    if (!bases || !base_out || !count || count > 3) return false;
    bool found = false;
    uint32_t candidate = 0;
    for (unsigned i = 0; i < count; ++i) {
        uint32_t base = bases[i];
        bool match = raw == base ||
            (stride <= UINT32_MAX - base && raw == base + stride);
        if (!match) continue;
        if (found && candidate != base) return false;
        candidate = base;
        found = true;
    }
    if (!found) return false;
    *base_out = candidate;
    return true;
}

#endif
