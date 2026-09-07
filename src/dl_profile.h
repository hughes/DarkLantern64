#ifndef DARKLANTERN_PROFILE_H
#define DARKLANTERN_PROFILE_H

#include <stdbool.h>
#include <stdint.h>

/* Work budget, independent of the display's native refresh rate. */
#ifndef DL_PROFILE_TARGET_FPS
#define DL_PROFILE_TARGET_FPS 60
#endif
#if DL_PROFILE_TARGET_FPS < 1 || DL_PROFILE_TARGET_FPS > 240
#error DL_PROFILE_TARGET_FPS must be between 1 and 240
#endif
#define DL_PROFILE_SAMPLE_CAPACITY 128

typedef enum {
    DL_PROFILE_INPUT, DL_PROFILE_GAMEPLAY, DL_PROFILE_AUDIO_EVENTS,
    DL_PROFILE_AUDIO_MIX, DL_PROFILE_CACHE, DL_PROFILE_TRANSFORMS,
    DL_PROFILE_LIGHTING, DL_PROFILE_TRIANGLES, DL_PROFILE_HUD,
    DL_PROFILE_DEBUG_HUD, DL_PROFILE_RENDER_SETUP, DL_PROFILE_DISPLAY_WAIT,
    DL_PROFILE_OTHER, DL_PROFILE_COUNT
} DlProfileSlot;

typedef struct { uint32_t ticks, audio_ticks, audio_calls; } DlProfileMark;
typedef struct {
    uint32_t frames, window;
    float frame_ms, max_frame_ms;
    float avg_ms[DL_PROFILE_COUNT], max_ms[DL_PROFILE_COUNT];
    uint32_t vi_scans, presented_frames, repeated_scans, max_present_gap_vis;
} DlProfileSnapshot;

/* Flat, disjoint sections only. Audio callbacks are subtracted automatically.
 * Other interrupts and queue stalls remain inside the interrupted section. */
DlProfileMark dl_profile_mark(void);
void dl_profile_record(DlProfileSlot slot, DlProfileMark start);
void dl_profile_frame_begin(bool debug);
void dl_profile_frame_end(void);
void dl_profile_report(void);
/* Between frames: finish the previous window and begin fresh measurements.
 * Call after a level load/resume so loading or paused time is not gameplay. */
void dl_profile_reset(void);
const DlProfileSnapshot *dl_profile_latest(void);
/* Called only by the AI interrupt. Integer accounting; no locks or logging. */
void dl_profile_audio_record(uint32_t ticks);

/* Install after display_init. Read-only VI origin observation; non-interlaced
 * output with at least two buffers. Does not drain graphics or present frames. */
void dl_profile_init_display(void);
/* Integer-only interrupt seam, also exercised by host tests. */
void dl_profile_vi_record(uint32_t origin);
/* Renderer initialization only. RSP culling cannot cheaply report post-clip
 * triangles/full-body coverage; mark its model candidates explicitly. */
void dl_profile_set_geometry_evidence(bool bounds_only);
/* Once per gameplay frame after submission. Full: all posed vertices inside
 * viewport. Drawn: nondegenerate guard triangles submitted. Geometry evidence
 * does not by itself prove those triangles survive depth-buffer occlusion. */
void dl_profile_workload(uint32_t animated, uint32_t full, uint32_t drawn,
                         uint32_t triangles, uint32_t head_overlays);

#endif
