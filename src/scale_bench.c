#include "scale_bench.h"
#include "dl_profile.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#ifdef DL_SCALE_BENCH_TEST
#define TICKS_PER_SECOND 1000u
#define debugf(...) printf(__VA_ARGS__)
#else
#include <libdragon.h>
#endif

#ifndef DL_SCALE_BENCH
#error "scale_bench.c belongs only in a DL_SCALE_BENCH diagnostic build"
#endif

enum { MAX_GUARDS = 16, MAX_LIGHTS = 16, MAX_COLLIDERS = 256,
       GUARD_SAMPLES = 60, GUARD_SUBSTEPS = 4, QUERY_BATCH = 32 };

typedef struct {
    uint64_t ticks, audio_ticks;
    uint32_t max_ticks, samples;
} Measurement;

/* Bounded diagnostic storage. Shipping games share one DlGame with a bounded
 * enemy array. These larger copies deliberately retain the historic workload:
 * one first-authored enemy per clone, even when the source now has many. */
static DlGame guards[MAX_GUARDS];
static DlLight lights[MAX_LIGHTS];
static DlCollider distant_colliders[MAX_COLLIDERS];
static volatile float result_sink;
static unsigned case_count;

static void sample_end(Measurement *measurement, DlProfileMark start) {
    DlProfileMark end = dl_profile_mark();
    uint32_t elapsed = end.ticks - start.ticks;
    uint32_t audio = end.audio_ticks - start.audio_ticks;
    assert(audio <= elapsed);
    uint32_t cpu = elapsed - audio;
    measurement->ticks += cpu;
    measurement->audio_ticks += audio;
    if (cpu > measurement->max_ticks) measurement->max_ticks = cpu;
    ++measurement->samples;
}

static void report(const char *name, unsigned count, unsigned units,
                   unsigned colliders, unsigned light_count, unsigned substeps,
                   const Measurement *measurement, float checksum) {
    assert(measurement->samples && isfinite(checksum));
    result_sink += checksum;
    ++case_count;
    debugf("DL64 scale_case name=%s count=%u samples=%lu units_per_sample=%u "
           "ticks=%llu max_ticks=%lu audio_ticks=%llu ticks_per_second=%lu "
           "colliders=%u lights=%u substeps=%u checksum=%.6f\n",
           name, count, (unsigned long)measurement->samples, units,
           (unsigned long long)measurement->ticks,
           (unsigned long)measurement->max_ticks,
           (unsigned long long)measurement->audio_ticks,
           (unsigned long)TICKS_PER_SECOND, colliders, light_count, substeps,
           (double)checksum);
}

static void guard_batch(unsigned count) {
    /* dl_game_update subdivides a 30 Hz frame into four 120 Hz steps today.
     * Keep that real cost here; measuring one 30 Hz AI call would understate it. */
    for (unsigned substep = 0; substep < GUARD_SUBSTEPS; ++substep)
        for (unsigned i = 0; i < count; ++i)
            dl_game_benchmark_guard_step(&guards[i], 1.0f / 120.0f);
}

static void benchmark_guards(const DlLevel *level, unsigned count, bool chase) {
    for (unsigned i = 0; i < count; ++i) {
        dl_game_init(&guards[i], level);
        guards[i].enemy_count = 1;
        if (!chase) guards[i].visibility = 0;
        if (chase) {
            guards[i].enemies[0].state = DL_CHASE;
            guards[i].enemies[0].investigate_target = guards[i].player;
            guards[i].enemies[0].search_timer = 60;
            guards[i].visibility = 1;
        }
    }
    /* State persists through warm-up and all timed frames. Visibility is a
     * shared, precomputed player input to AI; that query is measured separately. */
    for (unsigned i = 0; i < 4; ++i) guard_batch(count);
    Measurement measurement = {0};
    for (unsigned sample = 0; sample < GUARD_SAMPLES; ++sample) {
        DlProfileMark start = dl_profile_mark();
        guard_batch(count);
        sample_end(&measurement, start);
    }
    float checksum = 0;
    for (unsigned i = 0; i < count; ++i) {
        assert(isfinite(guards[i].enemies[0].position.x) && isfinite(guards[i].enemies[0].position.y) &&
               isfinite(guards[i].enemies[0].position.z) && isfinite(guards[i].enemies[0].awareness));
        checksum += guards[i].enemies[0].position.x + guards[i].enemies[0].position.y + guards[i].enemies[0].position.z +
                    guards[i].enemies[0].awareness + (float)guards[i].enemies[0].state;
    }
    report(chase ? "guard_chase" : "guard_patrol", count, count,
           (unsigned)level->collider_count, (unsigned)level->light_count,
           GUARD_SUBSTEPS, &measurement, checksum);
}

static DlVec3 probe_position(DlVec3 center, unsigned index) {
    /* A small planar sample patch near the authored player spawn. */
    return (DlVec3){center.x + ((int)(index % 32) - 16) * .015625f,
                    center.y, center.z + ((int)(index / 32) - 8) * .03125f};
}

static void local_lights(DlVec3 center) {
    for (unsigned i = 0; i < MAX_LIGHTS; ++i)
        lights[i] = (DlLight){
            .position = {center.x + ((int)(i % 4) - 1) * .375f,
                         center.y + 2, center.z + ((int)(i / 4) - 1) * .375f},
            .radius = 8, .intensity = .5f, .color = {1, .55f, .2f}};
}

static float probe_batch(const DlGame *game, DlVec3 center, unsigned count,
                         bool visibility) {
    float checksum = 0;
    for (unsigned i = 0; i < count; ++i) {
        DlVec3 position = probe_position(center, i);
        if (visibility) checksum += dl_visibility(game, position);
        else {
            DlVec3 rgb = dl_surface_light(game, position, (DlVec3){0, 1, 0});
            checksum += rgb.x + rgb.y + rgb.z;
        }
    }
    return checksum;
}

static void benchmark_probes(const char *name, DlLevel *level, DlVec3 center,
                             unsigned count, unsigned probes, unsigned samples,
                             bool visibility, bool moving) {
    DlGame game;
    dl_game_init(&game, level);
    result_sink += probe_batch(&game, center, probes, visibility);
    Measurement measurement = {0};
    float checksum = 0;
    for (unsigned sample = 0; sample < samples; ++sample) {
        /* Source movement is outside the timer: isolate the required lighting
         * recomputation. No transforms, upload, shading conversion or draw work. */
        if (moving) lights[0].position.x = center.x + (float)sample * .125f;
        DlProfileMark start = dl_profile_mark();
        checksum += probe_batch(&game, center, probes, visibility);
        sample_end(&measurement, start);
    }
    report(name, count, probes, (unsigned)level->collider_count,
           (unsigned)level->light_count, 0, &measurement, checksum);
}

void dl_scale_benchmark(const DlLevel *source) {
    assert(source && source->collider_count >= 0 && source->light_count >= 0);
    /* A guard workload needs an actual authored definition and route. */
    assert(source->enemies && source->enemy_count > 0);
    case_count = 0;
    result_sink = 0;
    debugf("DL64 scale_begin version=2 scene=\"%s\" clock=cp0_count "
           "audio=exclusive frame_hz=30 guard_substeps=4 guard_warmup_frames=4 "
           "game_state_bytes=%u model_bytes=%u light_bytes=%u collider_bytes=%u vec3_bytes=%u "
           "source_models=%d source_meshes=%d source_colliders=%d source_lights=%d "
           "diagnostic_storage_bytes=%u rendering=excluded "
           "guards=independent_clones visibility=shared_input\n",
           source->title, (unsigned)sizeof(DlGame), (unsigned)sizeof(DlModelInstance),
           (unsigned)sizeof(DlLight), (unsigned)sizeof(DlCollider), (unsigned)sizeof(DlVec3),
           source->model_count, source->mesh_count, source->collider_count, source->light_count,
           (unsigned)(sizeof(guards) + sizeof(lights) + sizeof(distant_colliders)));
    const unsigned guard_counts[] = {1, 2, 4, 8, 16};
    for (unsigned i = 0; i < sizeof(guard_counts) / sizeof(guard_counts[0]); ++i) {
        benchmark_guards(source, guard_counts[i], false);
        benchmark_guards(source, guard_counts[i], true);
    }

    DlLevel level = *source;
    DlVec3 center = source->spawn;
    center.y += 1;
    local_lights(center);
    level.lights = lights;
    /* Isolate point-light scaling: the constant moon cost is deliberately off. */
    level.environment.enabled = true;
    level.environment.ambient = (DlVec3){.01f, .01f, .01f};
    level.environment.moon_intensity = 0;
    const unsigned light_counts[] = {1, 4, 8, 16};
    for (unsigned i = 0; i < sizeof(light_counts) / sizeof(light_counts[0]); ++i) {
        level.light_count = (int)light_counts[i];
        benchmark_probes("surface_lights", &level, center, light_counts[i], QUERY_BATCH, 8, false, false);
        benchmark_probes("visibility_lights", &level, center, light_counts[i], QUERY_BATCH, 8, true, false);
    }
    level.light_count = 1;
    benchmark_probes("moving_light_vertices", &level, center, 128, 128, 4, false, true);
    benchmark_probes("moving_light_vertices", &level, center, 512, 512, 4, false, true);

    /* Worst traversal count for the current linear collider scan: each ray
     * misses every proxy, so there is no lucky early occluder. These synthetic
     * axis-aligned boxes are distant; they do not represent additional rooms. */
    for (unsigned i = 0; i < MAX_COLLIDERS; ++i)
        distant_colliders[i] = (DlCollider){
            .center = {center.x + 100 + (float)i * 2, center.y, center.z},
            .half_size = {.5f, 1, .5f}};
    level.colliders = distant_colliders;
    level.light_count = 4;
    local_lights(center);
    const unsigned collider_counts[] = {0, 16, 64, 256};
    for (unsigned i = 0; i < sizeof(collider_counts) / sizeof(collider_counts[0]); ++i) {
        level.collider_count = (int)collider_counts[i];
        benchmark_probes("light_collider_scan", &level, center, collider_counts[i], 8, 8, false, false);
    }

    /* Paired exposed probes retain the same authored colliders and positions.
     * In the courtyard fixture these sources are at y=1.8, below the shelter
     * roof at y=2.74: every local ray is clear. The original blocked-source
     * cases above stay unchanged and precede these six additions. */
    level = *source;
    level.lights = lights;
    level.environment.enabled = true;
    level.environment.ambient = (DlVec3){.01f, .01f, .01f};
    level.environment.moon_intensity = 0;
    local_lights(center);
    for (unsigned i = 0; i < MAX_LIGHTS; ++i) lights[i].position.y = center.y + .8f;
    for (unsigned i = 0; i < sizeof(light_counts) / sizeof(light_counts[0]); ++i) {
        level.light_count = (int)light_counts[i];
        benchmark_probes("surface_lights_clear", &level, center, light_counts[i], QUERY_BATCH, 8, false, false);
    }
    level.light_count = 1;
    benchmark_probes("moving_light_vertices_clear", &level, center, 128, 128, 4, false, true);
    benchmark_probes("moving_light_vertices_clear", &level, center, 512, 512, 4, false, true);
    debugf("DL64 scale_complete cases=%u checksum=%.6f\n", case_count, (double)result_sink);
}
