#include <libdragon.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "game.h"
#include "render.h"
#include "bundle.h"
#include "launch.h"
#include "dl_profile.h"
#include "animation.h"
#ifdef DL_SCALE_BENCH
#include "scale_bench.h"
#endif
#ifdef DL_CAPTURE
#include "capture_views.h"
#endif

enum { SAMPLE_RATE = 22050, VOICE_COUNT = 4, REQUIRED_MEMORY = 8 * 1024 * 1024 };

typedef struct {
    uint32_t phase, increment;
    int remaining, length, volume, noise, pan;
} Voice;

static Voice voices[VOICE_COUNT];
static uint32_t random_state = 0x64d4a7u;
static int sample_rate;
static const DlAnimClip *guard_step_clips[DL_MAX_ENEMIES];

/* The callback runs under an AI interrupt: bounded integer synthesis only,
 * with no allocation, logging, game access or floating-point instructions. */
static void audio_callback(short *buffer, size_t samples) {
    uint32_t start = TICKS_READ();
    for (size_t i = 0; i < samples; ++i) {
        int left = 0, right = 0;
        random_state ^= random_state << 13;
        random_state ^= random_state >> 17;
        random_state ^= random_state << 5;
        int noise = (int)(random_state & 0xffff) - 32768;
        for (int j = 0; j < VOICE_COUNT; ++j) {
            Voice *v = &voices[j];
            if (!v->remaining) continue;
            v->phase += v->increment;
            int wave = (int)(v->phase >> 16);
            wave = wave < 32768 ? wave * 2 - 32768 : 98303 - wave * 2;
            wave = (wave * (256 - v->noise) + noise * v->noise) >> 8;
            /* Scale before the envelope multiply to stay within int32. */
            int level = v->volume * v->remaining / v->length;
            int value = (wave * level) >> 15;
            left += value * (128 - v->pan) / 128;
            right += value * (128 + v->pan) / 128;
            --v->remaining;
        }
        if (left < -32767) left = -32767;
        if (left > 32767) left = 32767;
        if (right < -32767) right = -32767;
        if (right > 32767) right = 32767;
        buffer[i * 2] = (short)left;
        buffer[i * 2 + 1] = (short)right;
    }
    dl_profile_audio_record((uint32_t)(TICKS_READ() - start));
}

static void sound(float hz, float seconds, int volume, int noise, int pan) {
    Voice voice = { .increment = (uint32_t)(hz * (4294967296.0 / sample_rate)),
        .length = (int)(seconds * sample_rate), .volume = volume, .noise = noise, .pan = pan };
    if (voice.length < 1) voice.length = 1;
    voice.remaining = voice.length;
    disable_interrupts();
    int slot = 0;
    for (int i = 0; i < VOICE_COUNT; ++i) {
        if (voices[i].remaining < voices[slot].remaining) slot = i;
    }
    voices[slot] = voice;
    enable_interrupts();
}

static void clear_sounds(void) {
    disable_interrupts();
    memset(voices, 0, sizeof(voices));
    enable_interrupts();
}

/* Placeholder spatial cues use the same XYZ world as gameplay. This is
 * distance attenuation, horizontal panning and obstruction, not room DSP. */
static void world_sound(const DlGame *game, DlVec3 source, float hz,
                        float seconds, int volume, int noise) {
    DlVec3 ear = dl_player_eye(game);
    float dx = source.x - ear.x, dy = source.y - ear.y, dz = source.z - ear.z;
    float distance = sqrtf(dx * dx + dy * dy + dz * dz);
    if (distance >= 9.0f) return;
    float lateral = dx * cosf(game->yaw) - dz * sinf(game->yaw);
    int pan = (int)(lateral / (distance + 0.1f) * 65);
    volume = (int)(volume / (1.0f + distance * distance * 0.2f));
    if (!dl_line_of_sight(game, ear, source)) volume /= 3;
    sound(hz, seconds, volume, noise, pan);
}

static void event_sound(const DlGame *game) {
    switch (game->event) {
    case DL_EVENT_STEP: sound(75, 0.10f, game->crouched ? 700 : 2300, 185, 0); break;
    case DL_EVENT_NOISE: sound(130, 0.32f, 6500, 220, 0); break;
    case DL_EVENT_DOOR:
        world_sound(game, game->last_sound, 185, 0.60f, 4200, 75);
        world_sound(game, game->last_sound, 65, 0.30f, 4500, 205);
        break;
    case DL_EVENT_DETECTED:
        world_sound(game, game->last_sound, 330, 0.55f, 6200, 5);
        world_sound(game, game->last_sound, 248, 0.55f, 4800, 5);
        break;
    case DL_EVENT_OBJECTIVE:
        sound(523, 1.0f, 3300, 0, -25);
        sound(659, 1.2f, 2400, 0, 25);
        sound(784, 1.4f, 1800, 0, 0);
        break;
    default: break;
    }
}

static float stick_axis(int value) {
    if (value > -9 && value < 9) return 0;
    float axis = (value > 0 ? value - 8 : value + 8) / 72.0f;
    return axis < -1 ? -1 : axis > 1 ? 1 : axis;
}

static DlInput read_input(bool *debug, const DlLauncher *launcher, DlLaunchInput *menu) {
    joypad_poll();
    joypad_inputs_t pad = joypad_get_inputs(JOYPAD_PORT_1);
    joypad_buttons_t pressed = joypad_get_buttons_pressed(JOYPAD_PORT_1);
    *menu = (DlLaunchInput){0};
    if (launcher->menu_open) {
        static int held_vertical, held_horizontal;
        int vertical = pad.stick_y > 35 ? -1 : pad.stick_y < -35 ? 1 : 0;
        int horizontal = pad.stick_x > 35 ? 1 : pad.stick_x < -35 ? -1 : 0;
        menu->level_delta = (pressed.d_down || pressed.c_down) - (pressed.d_up || pressed.c_up);
        menu->start_delta = (pressed.d_right || pressed.c_right) - (pressed.d_left || pressed.c_left);
        if (vertical && vertical != held_vertical) menu->level_delta = vertical;
        if (horizontal && horizontal != held_horizontal) menu->start_delta = horizontal;
        held_vertical = vertical; held_horizontal = horizontal;
        menu->accept = pressed.a || pressed.start;
        menu->back = pressed.b;
        return (DlInput){0};
    }
    if (pressed.start && pad.btn.z) {
        menu->open_menu = true;
        return (DlInput){0};
    }
    if (pressed.start) *debug = !*debug;
    DlInput input = {
        .forward = (float)pad.btn.c_up - (float)pad.btn.c_down,
        .turn = stick_axis(pad.stick_x),
        .strafe = (float)pad.btn.c_right - (float)pad.btn.c_left,
        .look = stick_axis(pad.stick_y),
        .crouch = pad.btn.z, .use = pressed.a, .noise = pressed.b, .restart = pressed.r,
        .jump = pressed.l,
    };
    /* Digital look aliases also support the project's Ares arrow keys. */
    if (pad.btn.d_up) input.look = 1;
    if (pad.btn.d_down) input.look = -1;
    if (pad.btn.d_left) input.turn = -1;
    if (pad.btn.d_right) input.turn = 1;
    return input;
}

#ifdef DL_AUTOPLAY
/* Input-only acceptance replay for the checked-in Lantern Store blockout.
 * This deliberately depends on that room's authored route; it is a QA build
 * option, not runtime pathfinding or a replacement for the manual demo. */
static DlInput replay_input(const DlGame *game, float dt) {
    static int stage;
    DlInput input = { .crouch = true };
    if (game->elapsed < 0.01f) stage = 0;
    if (game->caught || game->complete) return input;
    DlVec3 route[] = {
        { -2.0f, 0, 2.0f },
        { game->level->control.x, 0, game->level->control.z },
        { 1.2f, 0, 0 }, { 2.5f, 0, 2.0f }, { 5.0f, 0, 1.7f },
        { 5.0f, 0.8f, 4.2f },
        { game->level->objective.x, 0.8f, game->level->objective.z },
    };
    if (stage >= (int)(sizeof(route) / sizeof(route[0]))) return input;
    float dx = route[stage].x - game->player.x, dz = route[stage].z - game->player.z;
    if (dx * dx + dz * dz < 0.08f * 0.08f && fabsf(route[stage].y - game->player.y) < 0.12f) {
        if (stage == 1 || stage == 6) input.use = true;
        ++stage;
        debugf("DL64 replay stage=%d t=%.2f player=%.2f,%.2f,%.2f use=%d\n",
            stage, game->elapsed, game->player.x, game->player.y, game->player.z, input.use);
        return input;
    }
    float error = atan2f(dx, dz) - game->yaw;
    while (error > 3.14159265f) error -= 6.28318531f;
    while (error < -3.14159265f) error += 6.28318531f;
    float turn = -error / (2.3f * dt);
    input.turn = turn < -1 ? -1 : turn > 1 ? 1 : turn;
    input.forward = fabsf(error) < 0.10f ? 1 : 0;
    return input;
}
#endif

static void require_expansion_pak(int memory) {
    if (memory >= REQUIRED_MEMORY) return;
    /* This small console error path is valid with 4 MiB. It runs before
     * renderer, audio, scene initialization, or other substantial allocation. */
    console_init();
    console_set_render_mode(RENDER_AUTOMATIC);
    printf("\n  DARKLANTERN64\n\n  Expansion Pak required.\n\n  Detected: %d MiB\n  Required: 8 MiB\n\n  Enable 8 MiB RDRAM in your emulator,\n  or fit an Expansion Pak to your N64.\n", memory / (1024 * 1024));
    debugf("DL64 boot_failed reason=expansion_pak memory=%d required=%d\n", memory, REQUIRED_MEMORY);
    while (1) { wait_ms(100); }
}

static void start_level(DlGame *game, const DlLauncher *launcher, DlRenderStats *stats,
                        float *guard_step, DlVec3 *previous_guard) {
    clear_sounds();
    dl_render_release_scene();
    const DlLevel *level = dl_launcher_active_level(launcher);
    assertf(dl_game_start(game, level, launcher->active_start), "Invalid level start preset");
    dl_render_prepare_scene(game);
    memset(guard_step, 0, sizeof(float) * DL_MAX_ENEMIES);
    memset(guard_step_clips, 0, sizeof(guard_step_clips));
    for(int model=0;model<level->model_count;++model){
        const DlModelInstance *instance=&level->models[model];
        if(instance->role!=DL_MODEL_GUARD||instance->enemy_index<0||instance->enemy_index>=game->enemy_count)continue;
        const DlAnimationAsset *asset=level->meshes[instance->mesh].animation;
        int clip=dl_animation_find_clip(asset,"walk");
        if(clip>=0&&asset->clips[clip].stride_length>0&&asset->clips[clip].event_count)
            guard_step_clips[instance->enemy_index]=&asset->clips[clip];
    }
    for (int i = 0; i < game->enemy_count; ++i) previous_guard[i] = game->enemies[i].position;
    stats->frames = 0; stats->frame_ms = 16.67f; stats->cpu_ms = 0;
    heap_stats_t heap;
    sys_get_heap_stats(&heap);
    stats->heap_used = heap.used; stats->heap_total = heap.total;
    const char *preset = launcher->active_start < 0 ? "default" :
        level->test_starts[launcher->active_start].id;
    debugf("DL64 level_start id=%s preset=%s heap=%d/%d player=%.2f,%.2f,%.2f yaw=%.3f pitch=%.3f door=%d crouched=%d enemies=%d elapsed=%.2f complete=%d caught=%d\n",
        launcher->bundle->levels[launcher->active_level].id,preset,heap.used,heap.total,
        game->player.x,game->player.y,game->player.z,game->yaw,game->pitch,
        game->door_open,game->crouched,game->enemy_count,game->elapsed,game->complete,game->caught);
    debugf("DL64 boot_ok memory=%d scene=\"%s\" meshes=%d models=%d colliders=%d version=%lu world=3d renderer=xyz-mesh-rdpq depth=hardware\n",
        stats->memory_bytes,level->title,level->mesh_count,level->model_count,
        level->collider_count,(unsigned long)level->version);
    debugf("DL64 enemies count=%d capacity=%d state_bytes=%u\n",game->enemy_count,DL_MAX_ENEMIES,(unsigned)sizeof(game->enemies));
    for (int i=0;i<game->enemy_count;++i) {
        const DlEnemyDef *def=&level->enemies[i];
        debugf("DL64 enemy_spawn id=%s index=%d type=%d behavior=%s patrol_points=%d position=%.2f,%.2f,%.2f\n",
            def->id,i,(int)def->type,def->behavior==DL_BEHAVIOR_SENTRY?"sentry":"patrol",def->patrol_count,
            def->spawn.x,def->spawn.y,def->spawn.z);
    }
}

#ifdef DL_MENU_TEST
/* Script the same controller used by physical inputs. Each cycle visits every
 * default spawn and named preset, restarts it, then returns through the menu. */
static DlLaunchInput menu_test_input(const DlLauncher *launcher, DlInput *input) {
    static int initial_frames, variant, phase, frames, transitions;
    int variants = 0;
    for (int i=0;i<launcher->bundle->level_count;++i)
        variants += launcher->bundle->levels[i].load()->test_start_count + 1;
    *input = (DlInput){0};
    if (initial_frames++ < 30) return (DlLaunchInput){0};
    if (variant >= variants * 3) {
        if (phase != 9) {
            debugf("DL64 menu_test_complete transitions=%d variants=%d cycles=3\n",transitions,variants);
            phase = 9;
        }
        return (DlLaunchInput){0};
    }
    if (phase == 0) {
        int level=0, start=variant%variants;
        while (start > launcher->bundle->levels[level].load()->test_start_count) {
            start -= launcher->bundle->levels[level].load()->test_start_count + 1;
            ++level;
        }
        --start;
        if (launcher->selected_level != level)
            return (DlLaunchInput){.level_delta=level-launcher->selected_level};
        if (launcher->selected_start != start)
            return (DlLaunchInput){.start_delta=start-launcher->selected_start};
        phase=1;frames=0;++transitions;
        return (DlLaunchInput){.accept=true};
    }
    if (phase == 1) {
        if (++frames >= 8) { phase=2; return (DlLaunchInput){.open_menu=true}; }
        return (DlLaunchInput){0};
    }
    if (phase == 2) { phase=3;return (DlLaunchInput){.back=true}; }
    if (phase == 3) { phase=4;input->restart=true;++transitions;return (DlLaunchInput){0}; }
    ++variant;phase=0;
    return (DlLaunchInput){.open_menu=true};
}
#endif

int main(void) {
    debug_init_emulog();
    int memory = get_memory_size();
    require_expansion_pak(memory);
    display_init(RESOLUTION_320x240, DEPTH_16_BPP, 2, GAMMA_NONE, FILTERS_RESAMPLE);
    rdpq_init();
    joypad_init();
    audio_init(SAMPLE_RATE, 4);
    sample_rate = audio_get_frequency();
    audio_set_buffer_callback(audio_callback);
    /* This SDK installs the callback without queuing the first AI transfer.
     * Prime playback once; subsequent buffers are supplied by AI interrupts. */
    audio_write_silence();
#ifdef DL_SCALE_BENCH
    debugf("DL64 scale_boot memory=%d\n",memory);
    dl_scale_benchmark(dl_bundle.levels[dl_bundle.initial_level].load());
    while(1)wait_ms(100);
#endif
    if(dl_bundle.has_textures)assertf(dfs_init(DFS_DEFAULT_LOCATION)==DFS_ESUCCESS,"ROM texture filesystem unavailable");
    dl_render_init();

    DlLauncher launcher;
    assertf(dl_launcher_init(&launcher,&dl_bundle),"Invalid level bundle");
    DlGame game = {0};
    float guard_step[DL_MAX_ENEMIES] = {0};
    DlVec3 previous_guard[DL_MAX_ENEMIES] = {0};
    DlRenderStats stats = { .memory_bytes = memory, .frame_ms = 16.67f };
#ifdef DL_AUTOPLAY
    /* Start hidden profiling runs with the ordinary gameplay HUD. */
    stats.debug = false;
    bool profile_terminal = false;
#endif
#ifdef DL_DEBUG_OVERLAY
    stats.debug = true;
#endif
    if (launcher.has_active) start_level(&game,&launcher,&stats,guard_step,previous_guard);
    debugf("DL64 controls stick=look dpad=look c_up_down=move c_left_right=strafe l=jump z=crouch a=use b=noise r=restart start=debug z_start=level_menu\n");
    debugf("DL64 menu_ready levels=%d initial=%s mode=%s\n",dl_bundle.level_count,
        dl_bundle.levels[launcher.selected_level].id,launcher.menu_open?"menu":"direct");
#ifdef DL_AUTOPLAY
    debugf("DL64 replay_start mode=input-only scene_version=%lu fixed_dt=0.016666667\n",
        (unsigned long)game.level->version);
#endif
    uint32_t previous = TICKS_READ(), next_report = previous;
    while (1) {
        dl_profile_frame_begin(stats.debug);
        uint32_t frame_start = TICKS_READ();
        float elapsed = (float)(uint32_t)(frame_start - previous) / TICKS_PER_SECOND;
        previous = frame_start;
        if (elapsed < 0.001f) elapsed = 1.0f / 60.0f;
        stats.frame_ms += (elapsed * 1000.0f - stats.frame_ms) * 0.06f;
        /* The core subdivides collision movement; this cap prevents focus
         * loss or emulator debugger pauses from jumping the simulation. */
        float dt = elapsed > 0.05f ? 0.05f : elapsed;
        DlProfileMark mark = dl_profile_mark();
        bool next_debug = stats.debug;
        DlLaunchInput menu;
        DlInput input = read_input(&next_debug,&launcher,&menu);
#ifdef DL_MENU_TEST
        menu = menu_test_input(&launcher,&input);
#endif
#ifdef DL_AUTOPLAY
        dt = 1.0f / 60.0f;
        if (!launcher.menu_open && !menu.open_menu && !input.restart) input = replay_input(&game, dt);
#endif
        dl_profile_record(DL_PROFILE_INPUT, mark);
        DlLaunchAction action = dl_launcher_update(&launcher,menu);
        if (action == DL_LAUNCH_LEVEL || (!launcher.menu_open && input.restart)) {
            bool restart = input.restart;
            dl_profile_frame_end();
            start_level(&game,&launcher,&stats,guard_step,previous_guard);
            if (restart) debugf("DL64 restart t=%.2f player=%.2f,%.2f,%.2f\n",
                game.elapsed,game.player.x,game.player.y,game.player.z);
            dl_profile_reset();
            previous = next_report = TICKS_READ();
#ifdef DL_AUTOPLAY
            profile_terminal = false;
#endif
            continue;
        }
        if (action == DL_LAUNCH_MENU_OPENED || action == DL_LAUNCH_RESUME) {
            if (action == DL_LAUNCH_MENU_OPENED) clear_sounds();
            debugf("DL64 menu action=%s active=%s t=%.2f\n",
                action==DL_LAUNCH_MENU_OPENED?"open":"resume",
                launcher.has_active?dl_bundle.levels[launcher.active_level].id:"none",game.elapsed);
            debugf("DL64 menu_%s id=%s preset=%s elapsed=%.6f player=%.3f,%.3f,%.3f door=%d complete=%d caught=%d\n",
                action==DL_LAUNCH_MENU_OPENED?"open":"resume",
                dl_bundle.levels[launcher.active_level].id,
                launcher.active_start<0?"default":game.level->test_starts[launcher.active_start].id,
                game.elapsed,game.player.x,game.player.y,game.player.z,game.door_open,game.complete,game.caught);
            dl_profile_frame_end();
            dl_profile_reset();
            previous = next_report = TICKS_READ();
            continue;
        }
        if (launcher.menu_open) {
#ifdef DL_MENU_TEST
            static bool menu_captured;
            if (!menu_captured) { dl_render_request_capture(1); menu_captured=true; }
#endif
            stats.cpu_ms=dl_render_menu(&dl_bundle,&launcher,&stats);
            ++stats.frames;
            dl_profile_frame_end();
            continue;
        }
#ifdef DL_CAPTURE
        unsigned view=stats.frames/30;
        if(view>=DL_CAPTURE_VIEW_COUNT)view=DL_CAPTURE_VIEW_COUNT-1;
        game.player=dl_capture_views[view].position;
        game.yaw=dl_capture_views[view].yaw;game.pitch=dl_capture_views[view].pitch;
        game.door_open=dl_capture_views[view].door_open;
        /* Reproducible visual phase, not simulation/saved-game advancement. */
        game.elapsed=dl_capture_views[view].animation_time;
        dl_render_set_animation_preview(dl_capture_views[view].animation_clip,game.elapsed,
            dl_capture_views[view].head_yaw,dl_capture_views[view].head_pitch);
        game.visibility=dl_visibility(&game,dl_player_eye(&game));
        input=(DlInput){0};dt=0;
        if(stats.frames<DL_CAPTURE_VIEW_COUNT*30&&stats.frames%30==29)
            dl_render_request_capture((int)view+1);
#endif
        DlGuardState old_state[DL_MAX_ENEMIES];
        for(int i=0;i<game.enemy_count;++i)old_state[i]=game.enemies[i].state;
        uint32_t old_sound = game.sound_serial;
        float old_elapsed = game.elapsed;
        mark = dl_profile_mark();
        dl_game_update(&game, &input, dt);
        dl_profile_record(DL_PROFILE_GAMEPLAY, mark);
        /* Falling out of bounds resets the same selected test start as R. */
        if (game.elapsed < old_elapsed) {
            dl_profile_frame_end();
            start_level(&game,&launcher,&stats,guard_step,previous_guard);
            debugf("DL64 restart t=%.2f player=%.2f,%.2f,%.2f\n",
                game.elapsed,game.player.x,game.player.y,game.player.z);
            dl_profile_reset();
            previous=next_report=TICKS_READ();
#ifdef DL_AUTOPLAY
            profile_terminal=false;
#endif
            continue;
        }
        mark = dl_profile_mark();
        bool new_event = game.sound_serial != old_sound;
        if (new_event) event_sound(&game);
        for(int i=0;i<game.enemy_count;++i){
            const DlEnemy *enemy=&game.enemies[i];
            float dx=enemy->position.x-previous_guard[i].x,dy=enemy->position.y-previous_guard[i].y;
            float dz=enemy->position.z-previous_guard[i].z;
            float accumulated_step=guard_step[i]+sqrtf(dx*dx+dy*dy+dz*dz);
            previous_guard[i]=enemy->position;
            bool foot_contact=accumulated_step>0.72f,animated_contacts=false;
            const DlAnimClip *walk=guard_step_clips[i];
            if(walk){
                float to=enemy->animation_distance/walk->stride_length*walk->duration;
                /* Markers follow real travel even while the guard is culled.
                 * This existing sound path remains presentation-only: friendly
                 * guard sounds do not trigger today's player-noise AI hook. */
                foot_contact=dl_animation_event_count(walk,guard_step[i],to,DL_ANIM_FOOT_LEFT)>0||
                    dl_animation_event_count(walk,guard_step[i],to,DL_ANIM_FOOT_RIGHT)>0;
                guard_step[i]=to;
                animated_contacts=true;
            }
            if(!animated_contacts)guard_step[i]=accumulated_step;
            if(foot_contact){
                if(!animated_contacts)guard_step[i]=0;
                DlVec3 source=enemy->position;source.y+=0.10f;
                world_sound(&game,source,62,0.12f,2300,165);
            }
            if(old_state[i]!=enemy->state){
                debugf("DL64 guard_state from=%s to=%s t=%.2f sees=%d hears=%d alert=%.3f id=%s index=%d\n",
                    dl_guard_state_name(old_state[i]),dl_guard_state_name(enemy->state),game.elapsed,
                    enemy->sees_player,enemy->heard_sound,enemy->awareness,game.level->enemies[i].id,i);
                if(enemy->state==DL_INVESTIGATE){
                    DlVec3 source=enemy->position;source.y+=1.50f;
                    world_sound(&game,source,172,0.23f,2900,15);
                }
            }
        }
        if (new_event && game.event != DL_EVENT_NONE && game.event != DL_EVENT_STEP)
            debugf("DL64 event kind=%d t=%.2f door=%d complete=%d caught=%d sound=%lu\n",
                game.event, game.elapsed, game.door_open, game.complete, game.caught, (unsigned long)game.sound_serial);
        dl_profile_record(DL_PROFILE_AUDIO_EVENTS, mark);
        if ((int32_t)(frame_start - next_report) >= 0) {
            heap_stats_t heap;
            sys_get_heap_stats(&heap);
            stats.heap_used = heap.used;
            stats.heap_total = heap.total;
            /* Keep the original first-enemy fields for existing profile tools;
             * separate ID-tagged rows describe every independent actor. */
            const DlEnemy empty={0};
            const DlEnemy *first=game.enemy_count?&game.enemies[0]:&empty;
            debugf("DL64 frame n=%lu t=%.2f ms=%.2f cpu_ms=%.2f heap=%d/%d triangles=%d player=%.2f,%.2f,%.2f yaw=%.3f pitch=%.3f grounded=%d guard=%.2f,%.2f,%.2f light=%.3f alert=%.3f state=%s door=%d complete=%d caught=%d\n",
                (unsigned long)stats.frames, game.elapsed, stats.frame_ms, stats.cpu_ms, heap.used, heap.total,
                dl_render_triangle_count(), game.player.x, game.player.y, game.player.z, game.yaw, game.pitch,
                game.grounded, first->position.x,first->position.y,first->position.z,game.visibility,first->awareness,
                game.enemy_count?dl_guard_state_name(first->state):"none",game.door_open,game.complete,game.caught);
            for(int i=0;i<game.enemy_count;++i){
                const DlEnemy *enemy=&game.enemies[i];
                debugf("DL64 enemy id=%s index=%d position=%.2f,%.2f,%.2f state=%s patrol_index=%d sees=%d hears=%d alert=%.3f\n",
                    game.level->enemies[i].id,i,enemy->position.x,enemy->position.y,enemy->position.z,
                    dl_guard_state_name(enemy->state),enemy->patrol_index,enemy->sees_player,enemy->heard_sound,enemy->awareness);
            }
            debugf("DL64 materials textures=%d uploads=%d\n",game.level->texture_count,dl_render_texture_upload_count());
            debugf("DL64 scene_work models=%d visible=%d camera_vertices=%d\n",
                dl_render_model_count(),dl_render_visible_model_count(),dl_render_transformed_vertex_count());
            dl_render_report_animation();
            next_report = frame_start + TICKS_PER_SECOND;
        }
        float update_ms = (float)(uint32_t)(TICKS_READ() - frame_start) * (1000.0f / TICKS_PER_SECOND);
        stats.cpu_ms = dl_render_frame(&game, &stats) + update_ms;
        ++stats.frames;
        dl_profile_frame_end();
        stats.debug = next_debug;
#ifdef DL_CAPTURE
        if(stats.frames==DL_CAPTURE_VIEW_COUNT*30)debugf("DL64 capture_complete views=%d\n",DL_CAPTURE_VIEW_COUNT);
#endif
#ifdef DL_AUTOPLAY
        if (!profile_terminal && (game.complete || game.caught)) {
            dl_profile_report();
            debugf("DL64 profile_replay_done complete=%d caught=%d\n", game.complete, game.caught);
            profile_terminal = true;
        }
#endif
    }
}
