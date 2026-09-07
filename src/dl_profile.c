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
#include <unistd.h>
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
static uint32_t sample_elapsed[DL_PROFILE_SAMPLE_CAPACITY], sample_work[DL_PROFILE_SAMPLE_CAPACITY];
static uint32_t workload_frames, workload_min[5], workload_max[5];
static bool workload_recorded, display_tracked, vi_initialized;
static bool geometry_bounds_only;
typedef struct {
    uint32_t vis, presents, repeats, max_gap_vis, max_interval_ticks;
    uint64_t ticks;
} VideoWindow;
static volatile VideoWindow video;
static uint32_t vi_previous_ticks, vi_previous_origin, vi_gap;

/* A report has up to 256 tick values. General-purpose snprintf conversion
 * made its first following frame measurably slower on VR4300. Keep the exact
 * same lowercase hexadecimal protocol with at most eight writes per value. */
static unsigned sample_hex(char *out, uint32_t value) {
    static const char digits[] = "0123456789abcdef";
    unsigned shift = 28, count = 0;
    while (shift && !(value >> shift)) shift -= 4;
    for (;;) {
        out[count++] = digits[(value >> shift) & 15u];
        if (!shift) return count;
        shift -= 4;
    }
}

/* Normal one-second window totals fit uint32. Constant division by ten then
 * compiles to the VR4300's cheap multiply/shift path instead of printf's
 * generic 64-bit conversion. Retain an exact full-width path for long stalls. */
static const char *decimal_u64(char out[21], uint64_t value) {
    char *at = out + 20;
    *at = '\0';
    if (value <= UINT32_MAX) {
        uint32_t small = (uint32_t)value;
        do { *--at = (char)('0' + small % 10u); small /= 10u; } while (small);
    } else {
        do { *--at = (char)('0' + value % 10u); value /= 10u; } while (value);
    }
    return at;
}

#ifdef DL_PROFILE_TEST
/* Exercise both formatter domains independently of the one-second flush,
 * which ordinarily prevents a real window total reaching the uint64 limit. */
const char *dl_profile_test_decimal(char out[21], uint64_t value) { return decimal_u64(out, value); }
#endif

/* One bounded chunk matches libdragon's ISViewer transfer buffer. Writing the
 * completed bytes directly avoids repeated printf parsing and line-buffer
 * flushing. No samples are dropped and this cost still belongs to OTHER. */
typedef struct { char bytes[512]; unsigned used; } ReportWriter;

static void report_flush(ReportWriter *writer) {
    if (!writer->used) return;
#ifdef DL_PROFILE_TEST
    assert(fwrite(writer->bytes, 1, writer->used, stdout) == writer->used);
#elif !defined(NDEBUG)
    /* ISViewer reads whole words for a partial tail; pad inside our chunk. */
    for (unsigned i = writer->used; i < ((writer->used+3u)&~3u); ++i) writer->bytes[i] = 0;
    ssize_t written = write(STDERR_FILENO, writer->bytes, writer->used);
    (void)written; /* debugf also does not turn unavailable logging into a game failure. */
#endif
    writer->used = 0;
}

static void report_text(ReportWriter *writer, const char *text) {
    while (*text) {
        if (writer->used == sizeof(writer->bytes)) report_flush(writer);
        writer->bytes[writer->used++] = *text++;
    }
}

static void report_number(ReportWriter *writer, uint64_t value) {
    char decimal[21];
    report_text(writer, decimal_u64(decimal, value));
}

static void report_field(ReportWriter *writer, const char *key, uint64_t value) {
    report_text(writer, " "); report_text(writer, key); report_text(writer, "=");
    report_number(writer, value);
}

static void report_begin(ReportWriter *writer, const char *kind) {
    report_text(writer, "DL64 "); report_text(writer, kind);
    report_field(writer, "window", window);
}

void dl_profile_vi_record(uint32_t origin) {
    uint32_t now = TICKS_READ();
    if (vi_initialized) {
        uint32_t interval = now - vi_previous_ticks;
        ++video.vis;
        video.ticks += interval;
        if (interval > video.max_interval_ticks) video.max_interval_ticks = interval;
        ++vi_gap;
        if (origin != vi_previous_origin) {
            ++video.presents;
            if (vi_gap > video.max_gap_vis) video.max_gap_vis = vi_gap;
            vi_gap = 0;
        } else ++video.repeats;
    }
    vi_initialized = true;
    vi_previous_ticks = now;
    vi_previous_origin = origin;
}

#ifndef DL_PROFILE_TEST
static void profile_vi_callback(void) {
    /* VI_ORIGIN contains the physical DRAM start of the scanned framebuffer.
     * Libdragon prepends handlers: registering after display_init observes the
     * previous field before display changes origin. This adds one field of
     * latency, never mistakes queue submission for actual presentation. */
    dl_profile_vi_record(*(volatile uint32_t *)0xA4400004 & 0x00ffffffu);
}
#endif

void dl_profile_init_display(void) {
    if (display_tracked) return;
#ifndef DL_PROFILE_TEST
    assert(display_get_num_buffers() >= 2);
    assert(!(*(volatile uint32_t *)0xA4400000 & 0x40u)); /* interlaced origin offsets */
    register_VI_handler(profile_vi_callback);
#endif
    display_tracked = true;
}

void dl_profile_workload(uint32_t animated, uint32_t full, uint32_t drawn,
                         uint32_t triangles, uint32_t head_overlays) {
    if (!active) return;
    assert(!workload_recorded);
    uint32_t values[5] = {animated, full, drawn, triangles, head_overlays};
    for (int i = 0; i < 5; ++i) {
        if (!workload_frames || values[i] < workload_min[i]) workload_min[i] = values[i];
        if (values[i] > workload_max[i]) workload_max[i] = values[i];
    }
    ++workload_frames;
    workload_recorded = true;
}

void dl_profile_set_geometry_evidence(bool bounds_only) {
    assert(!active);
    geometry_bounds_only = bounds_only;
}

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
        debugf("DL64 profile_enabled version=2 budget_fps=%d clock=cp0_count audio=exclusive waits=elapsed sample_capacity=%d video=vi_origin\n",
            DL_PROFILE_TARGET_FPS, DL_PROFILE_SAMPLE_CAPACITY);
    }
    memset(frame_ticks, 0, sizeof(frame_ticks));
    workload_recorded = false;
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
    if (frames < DL_PROFILE_SAMPLE_CAPACITY) {
        sample_elapsed[frames] = elapsed;
        sample_work[frames] = elapsed - frame_ticks[DL_PROFILE_DISPLAY_WAIT];
    }
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
    disable_interrupts();
    VideoWindow observed = video;
    video = (VideoWindow){0};
    enable_interrupts();
    latest.vi_scans = observed.vis;
    latest.presented_frames = observed.presents;
    latest.repeated_scans = observed.repeats;
    latest.max_present_gap_vis = observed.max_gap_vis;
#if !defined(DL_PROFILE_TEST) && !defined(NDEBUG)
    fflush(stderr); /* Preserve ordering with any earlier partial debug line. */
#endif
    ReportWriter writer = {.used = 0};
    report_begin(&writer, "profile");
    report_field(&writer, "frames", frames);
    report_field(&writer, "ticks_per_second", TICKS_PER_SECOND);
    report_field(&writer, "frame_ticks", elapsed_ticks);
    report_field(&writer, "frame_max_ticks", max_frame_ticks);
    report_field(&writer, "audio_calls", calls);
    report_field(&writer, "debug", window_debug);
    report_text(&writer, "\n");
    for (int i = 0; i < DL_PROFILE_COUNT; ++i) {
        latest.avg_ms[i] = (float)totals[i] * ms / frames;
        latest.max_ms[i] = maxima[i] * ms;
        report_begin(&writer, "profile_slot");
        report_text(&writer, " name=");
        report_text(&writer, names[i]);
        report_field(&writer, "ticks", totals[i]);
        report_field(&writer, "max_ticks", maxima[i]);
        report_text(&writer, "\n");
    }
    unsigned samples = frames < DL_PROFILE_SAMPLE_CAPACITY ? frames : DL_PROFILE_SAMPLE_CAPACITY;
    for (unsigned first = 0; first < samples; first += 16) {
        unsigned count = samples - first < 16 ? samples - first : 16;
        char elapsed_text[16*9], work_text[16*9];
        unsigned elapsed_used = 0, work_used = 0;
        for (unsigned i = 0; i < count; ++i) {
            if (i) { elapsed_text[elapsed_used++] = ','; work_text[work_used++] = ','; }
            elapsed_used += sample_hex(elapsed_text+elapsed_used, sample_elapsed[first+i]);
            work_used += sample_hex(work_text+work_used, sample_work[first+i]);
        }
        /* 16 uint32 values * 8 digits + 15 commas + terminator = 144. */
        assert(elapsed_used < sizeof(elapsed_text) && work_used < sizeof(work_text));
        elapsed_text[elapsed_used] = work_text[work_used] = '\0';
        report_begin(&writer, "profile_samples");
        report_field(&writer, "first", first);
        report_field(&writer, "count", count);
        report_text(&writer, " elapsed=");
        report_text(&writer, elapsed_text);
        report_text(&writer, " work=");
        report_text(&writer, work_text);
        report_text(&writer, "\n");
    }
    report_begin(&writer, "profile_video");
    report_field(&writer, "tracked", display_tracked);
    report_field(&writer, "vis", observed.vis);
    report_field(&writer, "presents", observed.presents);
    report_field(&writer, "repeats", observed.repeats);
    report_field(&writer, "ticks", observed.ticks);
    report_field(&writer, "max_gap_vis", observed.max_gap_vis);
    report_field(&writer, "max_interval_ticks", observed.max_interval_ticks);
    report_text(&writer, "\n");
    report_begin(&writer, "profile_workload");
    report_field(&writer, "frames", workload_frames);
    report_field(&writer, "geometry_mode", geometry_bounds_only);
    report_field(&writer, "animated_min", workload_min[0]);
    report_field(&writer, "animated_max", workload_max[0]);
    report_field(&writer, "full_min", workload_min[1]);
    report_field(&writer, "full_max", workload_max[1]);
    report_field(&writer, "drawn_min", workload_min[2]);
    report_field(&writer, "drawn_max", workload_max[2]);
    report_field(&writer, "triangles_min", workload_min[3]);
    report_field(&writer, "triangles_max", workload_max[3]);
    report_field(&writer, "heads_min", workload_min[4]);
    report_field(&writer, "heads_max", workload_max[4]);
    report_text(&writer, "\n");
    report_begin(&writer, "profile_end");
    report_text(&writer, "\n");
    report_flush(&writer);
    memset(totals, 0, sizeof(totals));
    memset(maxima, 0, sizeof(maxima));
    elapsed_ticks = calls = 0;
    frames = max_frame_ticks = 0;
    workload_frames = 0;
    memset(workload_min, 0, sizeof(workload_min));
    memset(workload_max, 0, sizeof(workload_max));
}

const DlProfileSnapshot *dl_profile_latest(void) { return &latest; }

void dl_profile_reset(void) {
    assert(!active);
    dl_profile_report();
    memset(&latest, 0, sizeof(latest));
    /* Retain monotonic window IDs and interrupt totals for log consumers. */
    boundary = dl_profile_mark();
    disable_interrupts();
    video = (VideoWindow){0};
    vi_initialized = false;
    vi_gap = 0;
    enable_interrupts();
}
