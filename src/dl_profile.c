#include "dl_profile.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

#ifdef DL_PROFILE_TEST
extern uint32_t dl_profile_test_ticks(void);
#define TICKS_READ() dl_profile_test_ticks()
#define TICKS_PER_SECOND 1000u
#define disable_interrupts() ((void)0)
#define enable_interrupts() ((void)0)
#define debugf(...) printf(__VA_ARGS__)
#else
#include <libdragon.h>
#endif

static const char *const names[DL_PROFILE_COUNT] = {
    "input", "gameplay", "audio_events", "audio_mix", "cache", "transforms",
    "lighting", "triangles", "hud", "debug_hud", "render_setup", "display_wait", "other"
};
static volatile uint32_t audio_ticks, audio_calls;
static uint32_t frame_ticks[DL_PROFILE_COUNT], maxima[DL_PROFILE_COUNT];
static uint64_t totals[DL_PROFILE_COUNT], elapsed_ticks, calls;
static uint32_t frames, max_frame_ticks, window;
static bool active, initialized, window_debug;
static DlProfileMark boundary;
static DlProfileSnapshot latest;

DlProfileMark dl_profile_mark(void) {
    /* Keep the hardware count and interrupt accumulator from straddling an AI
     * callback. Libdragon's interrupt disable/enable calls are nestable. */
    disable_interrupts();
    DlProfileMark mark = { TICKS_READ(), audio_ticks, audio_calls };
    enable_interrupts();
    return mark;
}

void dl_profile_audio_record(uint32_t ticks) {
    audio_ticks += ticks;
    ++audio_calls;
}

void dl_profile_record(DlProfileSlot slot, DlProfileMark start) {
    if (!active) return;
    DlProfileMark end = dl_profile_mark();
    uint32_t elapsed = end.ticks - start.ticks;
    uint32_t audio = end.audio_ticks - start.audio_ticks;
    assert(slot >= 0 && slot < DL_PROFILE_COUNT);
    assert(slot != DL_PROFILE_AUDIO_MIX && slot != DL_PROFILE_OTHER);
    assert(audio <= elapsed);
    frame_ticks[slot] += elapsed - audio;
}

void dl_profile_frame_begin(bool debug) {
    assert(!active);
    if (frames && debug != window_debug) dl_profile_report();
    window_debug = debug;
    if (!initialized) {
        boundary = dl_profile_mark();
        initialized = true;
        debugf("DL64 profile_enabled version=1 budget_fps=30 clock=cp0_count audio=exclusive waits=elapsed\n");
    }
    memset(frame_ticks, 0, sizeof(frame_ticks));
    active = true;
}

void dl_profile_frame_end(void) {
    assert(active);
    DlProfileMark end = dl_profile_mark();
    uint32_t elapsed = end.ticks - boundary.ticks;
    frame_ticks[DL_PROFILE_AUDIO_MIX] = end.audio_ticks - boundary.audio_ticks;
    uint64_t accounted = 0;
    for (int i = 0; i < DL_PROFILE_COUNT; ++i) accounted += frame_ticks[i];
    /* A failure here means overlapping markers, not a slow frame. */
    assert(accounted <= elapsed);
    frame_ticks[DL_PROFILE_OTHER] = elapsed - accounted;
    for (int i = 0; i < DL_PROFILE_COUNT; ++i) {
        totals[i] += frame_ticks[i];
        if (frame_ticks[i] > maxima[i]) maxima[i] = frame_ticks[i];
    }
    elapsed_ticks += elapsed;
    calls += (uint32_t)(end.audio_calls - boundary.audio_calls);
    if (elapsed > max_frame_ticks) max_frame_ticks = elapsed;
    ++frames;
    boundary = end;
    active = false;
    /* End-to-end boundaries include previous reporting/bookkeeping in OTHER
     * on the next frame. No forced RSP/RDP sync is introduced by profiling. */
    if (elapsed_ticks >= TICKS_PER_SECOND) dl_profile_report();
}

void dl_profile_report(void) {
    assert(!active);
    if (!frames) return;
    const float ms = 1000.0f / TICKS_PER_SECOND;
    latest.frames = frames;
    latest.frame_ms = (float)elapsed_ticks * ms / frames;
    latest.max_frame_ms = max_frame_ticks * ms;
    ++window;
    latest.window = window;
    debugf("DL64 profile window=%lu frames=%lu ticks_per_second=%lu frame_ticks=%llu frame_max_ticks=%lu audio_calls=%llu debug=%d\n",
        (unsigned long)window, (unsigned long)frames, (unsigned long)TICKS_PER_SECOND,
        (unsigned long long)elapsed_ticks, (unsigned long)max_frame_ticks,
        (unsigned long long)calls, window_debug);
    for (int i = 0; i < DL_PROFILE_COUNT; ++i) {
        latest.avg_ms[i] = (float)totals[i] * ms / frames;
        latest.max_ms[i] = maxima[i] * ms;
        debugf("DL64 profile_slot window=%lu name=%s ticks=%llu max_ticks=%lu\n",
            (unsigned long)window, names[i], (unsigned long long)totals[i], (unsigned long)maxima[i]);
    }
    debugf("DL64 profile_end window=%lu\n", (unsigned long)window);
    memset(totals, 0, sizeof(totals));
    memset(maxima, 0, sizeof(maxima));
    elapsed_ticks = calls = 0;
    frames = max_frame_ticks = 0;
}

const DlProfileSnapshot *dl_profile_latest(void) { return &latest; }

void dl_profile_reset(void) {
    assert(!active);
    dl_profile_report();
    memset(&latest, 0, sizeof(latest));
    /* Retain monotonic window IDs and interrupt totals for log consumers. */
    boundary = dl_profile_mark();
}
