#include "dl_profile.h"
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

static uint32_t clock_ticks;
uint32_t dl_profile_test_ticks(void) { return clock_ticks; }
static void near(float actual, float expected) { assert(fabsf(actual - expected) < 0.001f); }

int main(void) {
    /* Both the timer and audio accumulator wrap during this measured frame. */
    clock_ticks = UINT32_MAX - 50;
    dl_profile_audio_record(UINT32_MAX - 5);
    dl_profile_frame_begin(false);
    DlProfileMark start = dl_profile_mark();
    clock_ticks += 30;
    dl_profile_audio_record(10);
    dl_profile_record(DL_PROFILE_INPUT, start);
    start = dl_profile_mark();
    clock_ticks += 40;
    dl_profile_audio_record(8);
    dl_profile_record(DL_PROFILE_DISPLAY_WAIT, start);
    clock_ticks += 30;
    dl_profile_frame_end();
    dl_profile_report();
    const DlProfileSnapshot *s = dl_profile_latest();
    assert(s->frames == 1);
    near(s->frame_ms, 100);
    near(s->avg_ms[DL_PROFILE_INPUT], 20);
    near(s->avg_ms[DL_PROFILE_DISPLAY_WAIT], 32);
    near(s->avg_ms[DL_PROFILE_AUDIO_MIX], 18);
    near(s->avg_ms[DL_PROFILE_OTHER], 30);

    /* Time spent between frames (including reporting) is accounted as OTHER.
     * Repeated scopes accumulate before per-frame maxima are calculated. */
    clock_ticks += 5;
    dl_profile_frame_begin(false);
    start = dl_profile_mark();
    clock_ticks += 10;
    dl_profile_record(DL_PROFILE_GAMEPLAY, start);
    start = dl_profile_mark();
    clock_ticks += 15;
    dl_profile_record(DL_PROFILE_GAMEPLAY, start);
    clock_ticks += 20;
    dl_profile_frame_end();
    dl_profile_frame_begin(false);
    start = dl_profile_mark();
    clock_ticks += 5;
    dl_profile_record(DL_PROFILE_GAMEPLAY, start);
    clock_ticks += 15;
    dl_profile_frame_end();
    /* Debug-state changes flush the old window without mixing its samples. */
    dl_profile_frame_begin(true);
    s = dl_profile_latest();
    assert(s->frames == 2);
    near(s->frame_ms, 35);
    near(s->max_frame_ms, 50);
    near(s->avg_ms[DL_PROFILE_GAMEPLAY], 15);
    near(s->max_ms[DL_PROFILE_GAMEPLAY], 25);
    near(s->avg_ms[DL_PROFILE_OTHER], 20);
    clock_ticks += 1000;
    dl_profile_frame_end();
    s = dl_profile_latest();
    assert(s->frames == 1);
    near(s->frame_ms, 1000);
    near(s->avg_ms[DL_PROFILE_OTHER], 1000);
    /* Loading/paused time and its audio callbacks must not contaminate the
     * first gameplay sample; report IDs remain unique across resets. */
    uint32_t old_window = s->window;
    clock_ticks += 9000;
    dl_profile_audio_record(250);
    dl_profile_reset();
    assert(dl_profile_latest()->frames == 0);
    dl_profile_frame_begin(false);
    start = dl_profile_mark();
    clock_ticks += 12;
    dl_profile_audio_record(2);
    dl_profile_record(DL_PROFILE_GAMEPLAY, start);
    dl_profile_frame_end();
    dl_profile_report();
    s = dl_profile_latest();
    assert(s->window > old_window && s->frames == 1);
    near(s->frame_ms, 12);
    near(s->avg_ms[DL_PROFILE_GAMEPLAY], 10);
    near(s->avg_ms[DL_PROFILE_AUDIO_MIX], 2);
    puts("Profiler: wrap, interrupt subtraction, disjoint accounting, window weighting, maxima and level resets passed.");
    return 0;
}
