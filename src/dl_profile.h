#ifndef DARKLANTERN_PROFILE_H
#define DARKLANTERN_PROFILE_H

#include <stdbool.h>
#include <stdint.h>

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

#endif
