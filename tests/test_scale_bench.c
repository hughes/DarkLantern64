#include "game.h"
#include "dl_profile.h"
#include "scale_bench.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Predictable fake measurements exercise arithmetic and reporting without
 * making host-speed assertions. Every measured sample contains 25 audio ticks
 * within 100 elapsed ticks, including a CP0-style unsigned timer wrap. */
DlProfileMark dl_profile_mark(void) {
    static uint32_t tick = UINT32_MAX - 150, audio = UINT32_MAX - 35;
    tick += 100;
    audio += 25;
    return (DlProfileMark){tick, audio, 0};
}

static void near(float actual, float expected) {
    assert(fabsf(actual - expected) < .00001f);
}

int main(void) {
    DlCollider floor = {.center = {0, -.1f, 0}, .half_size = {20, .1f, 20}};
    DlVec3 patrol[] = {{0, 0, 0}, {4, 0, 0}, {4, 0, 4}};
    DlLight light = {.position = {0, 2, 0}, .radius = 4, .intensity = .4f};
    DlEnemyDef enemy = {.id = "bench-watchman", .spawn = {0, 0, 0},
        .patrol = patrol, .patrol_count = 3, .speed = 1, .sight_range = 9, .hearing_range = 10};
    DlLevel level = {.title = "Scale benchmark test", .colliders = &floor,
        .collider_count = 1, .spawn = {-10, 0, -10}, .enemies = &enemy, .enemy_count = 1,
        .lights = &light, .light_count = 1};

    /* The diagnostic guard entry point must reproduce the actual game's
     * four guard physics/AI substeps in an ordinary stationary 30 Hz frame. */
    DlGame ordinary, isolated;
    dl_game_init(&ordinary, &level);
    isolated = ordinary;
    DlInput input = {0};
    for (unsigned frame = 0; frame < 20; ++frame) {
        dl_game_update(&ordinary, &input, 1.0f / 30.0f);
        for (unsigned substep = 0; substep < 4; ++substep)
            dl_game_benchmark_guard_step(&isolated, 1.0f / 120.0f);
        near(isolated.enemies[0].position.x, ordinary.enemies[0].position.x);
        near(isolated.enemies[0].position.y, ordinary.enemies[0].position.y);
        near(isolated.enemies[0].position.z, ordinary.enemies[0].position.z);
        near(isolated.enemies[0].yaw, ordinary.enemies[0].yaw);
        near(isolated.enemies[0].vertical_velocity, ordinary.enemies[0].vertical_velocity);
        near(isolated.enemies[0].awareness, ordinary.enemies[0].awareness);
        assert(isolated.enemies[0].state == ordinary.enemies[0].state);
        assert(isolated.enemies[0].patrol_index == ordinary.enemies[0].patrol_index);
        assert(isolated.enemies[0].sees_player == ordinary.enemies[0].sees_player);
    }
    DlGame unchanged = isolated;
    dl_game_benchmark_guard_step(NULL, 1.0f / 120.0f);
    dl_game_benchmark_guard_step(&isolated, NAN);
    dl_game_benchmark_guard_step(&isolated, 0);
    assert(memcmp(&unchanged, &isolated, sizeof(isolated)) == 0);

    /* Multiple-enemy source levels still benchmark exactly the first actor. */
    DlEnemyDef pair[] = {enemy, enemy};
    pair[1].spawn.x = 6;
    DlLevel paired_level = level;
    paired_level.enemies = pair;
    paired_level.enemy_count = 2;
    DlGame paired;
    dl_game_init(&paired, &paired_level);
    DlEnemy second = paired.enemies[1];
    dl_game_benchmark_guard_step(&paired, 1.0f / 120.0f);
    assert(paired.enemies[0].position.x > pair[0].spawn.x);
    assert(memcmp(&second, &paired.enemies[1], sizeof(second)) == 0);
    paired_level.enemy_count = 0;
    dl_game_init(&paired, &paired_level);
    DlGame empty = paired;
    dl_game_benchmark_guard_step(&paired, 1.0f / 120.0f);
    assert(memcmp(&empty, &paired, sizeof(empty)) == 0);

    /* Benchmark scenarios borrow immutable level inputs. Local light/collider
     * sweeps must not alter the playable source or caller's simulation. */
    DlLevel original_level = level;
    DlLight original_light = light;
    DlCollider original_floor = floor;
    DlVec3 original_patrol[3];
    memcpy(original_patrol, patrol, sizeof(patrol));
    dl_scale_benchmark(&level);
    assert(memcmp(&original_level, &level, sizeof(level)) == 0);
    assert(memcmp(&original_light, &light, sizeof(light)) == 0);
    assert(memcmp(&original_floor, &floor, sizeof(floor)) == 0);
    assert(memcmp(original_patrol, patrol, sizeof(patrol)) == 0);
    assert(memcmp(&unchanged, &isolated, sizeof(isolated)) == 0);
    puts("PASS scale benchmark: isolated guard equivalence, input validation and immutable source");
    return 0;
}
