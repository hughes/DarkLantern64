#include "dl_profile.h"
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t clock_ticks;
uint32_t dl_profile_test_ticks(void) { return clock_ticks; }
extern const char *dl_profile_test_decimal(char out[21], uint64_t value);
static void near(float actual, float expected) { assert(fabsf(actual - expected) < 0.001f); }

int main(void) {
    /* The formatting fast path must not truncate at the uint32 threshold or
     * overwrite either buffer canary at the full uint64 decimal limit. */
    const uint64_t values[] = {0,1,9,10,99,100,UINT32_MAX,(uint64_t)UINT32_MAX+1,
        UINT64_C(10000000000000000000),UINT64_MAX};
    for (unsigned i=0;i<sizeof(values)/sizeof(values[0]);++i) {
        char buffer[23], expected[21];memset(buffer,'!',sizeof(buffer));
        snprintf(expected,sizeof(expected),"%llu",(unsigned long long)values[i]);
        assert(strcmp(dl_profile_test_decimal(buffer+1,values[i]),expected)==0);
        assert(buffer[0]=='!' && buffer[22]=='!');
    }
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
    /* Count actual origin changes, including two repeated scans and wrapped
     * CP0 ticks. Three requested/rendered frames are deliberately not a proxy
     * for the four observed scans or the two newly presented buffers. */
    dl_profile_init_display();
    dl_profile_reset();
    clock_ticks = UINT32_MAX - 20;
    dl_profile_reset();
    dl_profile_vi_record(0x1000);
    dl_profile_frame_begin(false);
    clock_ticks += 16; dl_profile_vi_record(0x2000);
    clock_ticks += 16; dl_profile_vi_record(0x2000);
    dl_profile_workload(2,2,2,900,2);
    dl_profile_frame_end();
    dl_profile_frame_begin(false);
    clock_ticks += 16; dl_profile_vi_record(0x2000);
    dl_profile_workload(2,1,1,500,2);
    dl_profile_frame_end();
    dl_profile_frame_begin(false);
    clock_ticks += 16; dl_profile_vi_record(0x1000);
    dl_profile_workload(2,2,2,901,2);
    dl_profile_frame_end();
    dl_profile_report();
    s = dl_profile_latest();
    assert(s->frames == 3 && s->vi_scans == 4 && s->presented_frames == 2);
    assert(s->repeated_scans == 2 && s->max_present_gap_vis == 3);
    dl_profile_reset();
    clock_ticks += 10000;
    dl_profile_vi_record(0x1000);
    dl_profile_frame_begin(false);
    clock_ticks += 16; dl_profile_vi_record(0x2000);
    dl_profile_frame_end();
    dl_profile_report();
    assert(s->vi_scans == 1 && s->presented_frames == 1 && s->repeated_scans == 0);
    /* Both endpoints of the uint32 sample formatter must survive the emitted
     * log/parser roundtrip, including a zero frame and all eight hex digits. */
    dl_profile_frame_begin(false);
    dl_profile_frame_end();
    dl_profile_report();
    near(s->frame_ms, 0);
    dl_profile_frame_begin(false);
    clock_ticks += UINT32_MAX;
    dl_profile_frame_end();
    assert(s->frames == 1);
    puts("Profiler: wrap, interrupt subtraction, accounting, windows, maxima, resets, VI cadence and uint32 sample endpoints passed.");
    return 0;
}
