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
#include "tick.h"
#include "dl_music.h"
#include "stream_rom.h"
#ifdef DL_INPUT_REPLAY
#include "input_replay.h"
static int replay_start_preset=-1;
#endif
#ifdef DL_SCALE_BENCH
#include "scale_bench.h"
#endif
#ifdef DL_CAPTURE
#include "capture_views.h"
#endif

enum { SAMPLE_RATE = 32000, VOICE_COUNT = 4, REQUIRED_MEMORY = 8 * 1024 * 1024 };

typedef struct {
    uint32_t phase, increment;
    int remaining, length, volume, noise, pan;
} Voice;

static Voice voices[VOICE_COUNT];
static uint32_t random_state = 0x64d4a7u;
static int sample_rate;
static volatile uint32_t audio_call_total;
static bool stream_recovery_pending;

/* The callback runs under an AI interrupt: bounded integer synthesis only,
 * with no allocation, logging, game access or floating-point instructions. */
static void audio_callback(short *buffer, size_t samples) {
    ++audio_call_total;
    uint32_t start = TICKS_READ();
    const int16_t *music = dl_music_consume(samples);
    for (size_t i = 0; i < samples; ++i) {
        int left = music ? music[i * 2] : 0, right = music ? music[i * 2 + 1] : 0;
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

static void event_sound(const DlGame *game,const DlWorldEvent *event) {
    switch (event->kind) {
    case DL_EVENT_STEP: sound(75, 0.10f, game->crouched ? 700 : 2300, 185, 0); break;
    case DL_EVENT_NOISE: sound(130, 0.32f, 6500, 220, 0); break;
    case DL_EVENT_DOOR:
        world_sound(game, event->position, 185, 0.60f, 4200, 75);
        world_sound(game, event->position, 65, 0.30f, 4500, 205);
        break;
    case DL_EVENT_DETECTED:
        world_sound(game, event->position, 330, 0.55f, 6200, 5);
        world_sound(game, event->position, 248, 0.55f, 4800, 5);
        break;
    case DL_EVENT_OBJECTIVE:
        sound(523, 1.0f, 3300, 0, -25);
        sound(659, 1.2f, 2400, 0, 25);
        sound(784, 1.4f, 1800, 0, 0);
        break;
    case DL_EVENT_GUARD_CONTACT: {
        DlVec3 source=event->position;source.y+=.1f;
        world_sound(game,source,62,.12f,2300,165);break;
    }
    case DL_EVENT_GUARD_STATE:
        if(event->detail==DL_INVESTIGATE) {
            DlVec3 source=event->position;source.y+=1.5f;
            world_sound(game,source,172,.23f,2900,15);
        }
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

static void start_level(DlGame *game, const DlLauncher *launcher, DlRenderStats *stats) {
    stream_recovery_pending=false;
    uint32_t load_start = TICKS_READ();
    if(!dl_stream_rom_music_ready())dma_wait();
    dl_music_stop();
    clear_sounds();
    dl_render_release_scene();
    dl_stream_rom_close();
    const DlLevel *level = dl_launcher_active_level(launcher);
    if(level->stream)dl_stream_rom_start(game,level,launcher->active_start);
    else assertf(dl_game_start(game, level, launcher->active_start), "Invalid level start preset");
    level=game->level;
    dl_render_prepare_scene(game);
    stats->streaming=level->stream!=NULL;
    dl_music_start(&dl_level_music[launcher->active_level]);
    stats->frames = 0; stats->frame_ms = 16.67f; stats->cpu_ms = 0;
    heap_stats_t heap;
    sys_get_heap_stats(&heap);
    stats->heap_used = heap.used; stats->heap_total = heap.total;
    const char *preset = launcher->active_start < 0 ? "default" :
        level->test_starts[launcher->active_start].id;
    debugf("DL64 level_load id=%s preset=%s total_ticks=%lu ticks_per_second=%lu scope=reset_to_ready_including_interrupts\n",
        launcher->bundle->levels[launcher->active_level].id,preset,
        (unsigned long)(uint32_t)(TICKS_READ()-load_start),(unsigned long)TICKS_PER_SECOND);
    debugf("DL64 level_start id=%s preset=%s heap=%d/%d player=%.2f,%.2f,%.2f yaw=%.3f pitch=%.3f door=%d crouched=%d enemies=%d elapsed=%.2f complete=%d caught=%d\n",
        launcher->bundle->levels[launcher->active_level].id,preset,heap.used,heap.total,
        game->player.x,game->player.y,game->player.z,game->yaw,game->pitch,
        game->door_open,game->crouched,game->enemy_count,game->elapsed,game->complete,game->caught);
    debugf("DL64 boot_ok memory=%d scene=\"%s\" meshes=%d models=%d colliders=%d version=%lu world=3d renderer=" DL_RENDER_BACKEND " depth=hardware\n",
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

#ifdef DL_STREAM_TEST
static uint64_t stream_test_tick;
static void stream_checkpoint(const char *name,const DlGame *game){
    const DlWorldStream *world=dl_stream_rom_world();
    assertf(world,"Stream diagnostic has no active mission");
    debugf("DL64 stream_probe {\"checkpoint\":\"%s\",\"tick\":%llu,\"player\":[%.4f,%.4f,%.4f],\"door_open\":%d,\"loot_value\":%lu,\"collected_objects\":%llu,\"guards\":[",
        name,(unsigned long long)stream_test_tick,game->player.x,game->player.y,game->player.z,game->door_open,
        (unsigned long)game->loot_value,(unsigned long long)game->collected_objects);
    for(int i=0;i<game->enemy_count;++i){const DlEnemy *enemy=&game->enemies[i];
        debugf("%s{\"position\":[%.4f,%.4f,%.4f],\"awareness\":%.4f,\"state\":\"%s\",\"patrol_index\":%d}",
            i?",":"",enemy->position.x,enemy->position.y,enemy->position.z,enemy->awareness,
            dl_guard_state_name(enemy->state),enemy->patrol_index);}
    debugf("],\"stream\":{\"current\":%lu,\"resident\":[",(unsigned long)dl_stream_rom_current(game));
    bool first=true;for(size_t i=0;i<world->slot_count;++i)if(world->slots[i].state==DL_STREAM_RESIDENT){
        debugf("%s%lu",first?"":",",(unsigned long)world->slots[i].region_id);first=false;}
    debugf("],\"reserved\":%lu,\"allocated\":%lu,\"completed_loads\":%llu,\"retired\":%lu,\"readbytes\":%llu,\"stalls\":%llu}}\n",
        (unsigned long)world->stats.reserved_bytes,(unsigned long)world->stats.allocated_slot_bytes,
        (unsigned long long)world->stats.completed_loads,(unsigned long)dl_stream_rom_retired(),
        (unsigned long long)world->stats.bytes_read,(unsigned long long)world->stats.read_stalls);
}
static bool stream_test_move(const DlGame *game,DlVec3 target,DlInput *input){
    float x=target.x-game->player.x,z=target.z-game->player.z;
    if(x*x+z*z<.015f)return true;
    float error=atan2f(x,z)-game->yaw;
    while(error>3.14159265f)error-=6.28318531f;
    while(error< -3.14159265f)error+=6.28318531f;
    float turn=-error/(2.3f*DL_TICK_SECONDS);
    input->turn=fmaxf(-1,fminf(1,turn));input->forward=fabsf(error)<.1f?1:0;return false;
}
static DlInput stream_test_input(const DlGame *game,const DlLauncher *launcher,DlLaunchInput *menu){
    static unsigned stage;static uint64_t collected;static DlEnemy remembered;
    DlInput input={0};*menu=(DlLaunchInput){0};++stream_test_tick;
    if(launcher->menu_open){
        int probe=-1;for(int i=0;i<launcher->bundle->level_count;++i)
            if(strcmp(launcher->bundle->levels[i].id,"streaming-probe")==0)probe=i;
        assertf(probe>=0,"Streaming Rooms is missing from the diagnostic menu");
        menu->level_delta=probe-launcher->selected_level;menu->accept=true;return input;
    }
    assertf(dl_stream_rom_active(),"Diagnostic did not select the streamed menu entry");
    assertf(!game->caught&&!game->complete,"Streaming diagnostic reached an unexpected terminal state");
    assertf(stream_test_tick<7200,"Streaming diagnostic route timed out stage=%u",stage);
    const DlWorldStream *world=dl_stream_rom_world();unsigned resident=0;
    for(size_t i=0;i<world->slot_count;++i)resident+=world->slots[i].state==DL_STREAM_RESIDENT;
    switch(stage){
    case 0:
        stream_checkpoint("start",game);
#ifdef DL_STREAM_TEST_CAPTURE
        dl_render_request_capture(1);
#endif
        ++stage;break;
    case 1:if(stream_test_move(game,(DlVec3){-1.1f,0,0},&input)){input.use=true;++stage;}break;
    case 2:if(game->door_open){stream_checkpoint("east_gate_open",game);++stage;}break;
    case 3:if(stream_test_move(game,(DlVec3){1.1f,0,0},&input)){input.use=true;++stage;}break;
    case 4:if(!game->door_open){stream_checkpoint("east_closed",game);++stage;}break;
    case 5:if(stream_test_move(game,(DlVec3){5,0,2},&input)){input.use=true;++stage;}break;
    case 6:if(game->loot_value==25){collected=game->collected_objects;stream_checkpoint("east_closed_loot",game);
        ++stage;}break;
    case 7:if(resident==1){
#ifdef DL_STREAM_TEST_CAPTURE
        /* Turn through ordinary controls to show the room/gate instead of
         * exporting a close view of the empty loot location. */
        float error=atan2f(-game->player.x,-game->player.z)-game->yaw;
        while(error>3.14159265f)error-=6.28318531f;
        while(error< -3.14159265f)error+=6.28318531f;
        if(fabsf(error)>.05f){input.turn=fmaxf(-1,fminf(1,-error/(2.3f*DL_TICK_SECONDS)));break;}
        dl_render_request_capture(2);
#endif
        stream_checkpoint("west_retired",game);++stage;}break;
    case 8:if(stream_test_move(game,(DlVec3){1.1f,0,0},&input)){input.use=true;++stage;}break;
    case 9:if(game->door_open){stream_checkpoint("west_gate_open",game);++stage;}break;
    case 10:if(stream_test_move(game,(DlVec3){-1.1f,0,0},&input)){input.use=true;++stage;}break;
    case 11:if(!game->door_open){stream_checkpoint("west_closed",game);++stage;}break;
    case 12:if(stream_test_move(game,(DlVec3){-5,0,0},&input)&&resident==1){
        remembered=game->enemies[0];stream_checkpoint("east_retired",game);++stage;}break;
    case 13:if(stream_test_move(game,(DlVec3){-1.1f,0,0},&input)){input.use=true;++stage;}break;
    case 14:if(game->door_open){stream_checkpoint("east_reload_gate_open",game);++stage;}break;
    case 15:if(stream_test_move(game,(DlVec3){5,0,2},&input)){input.use=true;++stage;}break;
    case 16:{
        const DlEnemy *enemy=&game->enemies[0];
        bool guard=enemy->position.x==remembered.position.x&&enemy->position.y==remembered.position.y&&
            enemy->position.z==remembered.position.z&&enemy->yaw==remembered.yaw&&enemy->awareness==remembered.awareness&&
            enemy->state==remembered.state&&enemy->patrol_index==remembered.patrol_index&&enemy->search_timer==remembered.search_timer;
        assertf(game->loot_value==25&&game->collected_objects==collected&&guard,"Stream mission state lost on reload");
        stream_checkpoint("east_reload",game);
#ifdef DL_STREAM_TEST_CAPTURE
        debugf("DL64 capture_complete views=2\n");
#endif
        debugf("DL64 stream_test_complete passed=1 input_only=1 guard_persisted=1 audio_calls=%lu\n",(unsigned long)audio_call_total);
        ++stage;break;
    }
    default:break;
    }
    return input;
}
#endif
#ifdef DL_BAFFORD_STREAM_TEST
/* Source street traversal uses ordinary turning/walking inputs. Both regions
 * remain visible in this open corridor until a real visibility policy exists. */
static uint64_t bafford_stream_test_tick;
static void bafford_stream_checkpoint(const char *name,const DlGame *game){
    const DlWorldStream *world=dl_stream_rom_world();heap_stats_t heap;
    assertf(world,"Bafford diagnostic has no active stream mission");
    sys_get_heap_stats(&heap);
    debugf("DL64 bafford_stream {\"checkpoint\":\"%s\",\"tick\":%llu,\"elapsed\":%.4f,\"player\":[%.4f,%.4f,%.4f],\"grounded\":%d,\"collision_clear\":%d,\"heap_used\":%d,\"models\":%d,\"colliders\":%d,\"textures\":%d,\"stream\":{\"current\":%lu,\"resident\":[",
        name,(unsigned long long)bafford_stream_test_tick,game->elapsed,
        game->player.x,game->player.y,game->player.z,game->grounded,
        dl_position_clear(game,game->player,.18f,1.65f),heap.used,
        game->level->model_count,game->level->collider_count,game->level->texture_count,
        (unsigned long)dl_stream_rom_current(game));
    bool first=true;for(size_t i=0;i<world->slot_count;++i)if(world->slots[i].state==DL_STREAM_RESIDENT){
        debugf("%s%lu",first?"":",",(unsigned long)world->slots[i].region_id);first=false;}
    debugf("],\"reserved\":%lu,\"allocated\":%lu,\"completed_loads\":%llu,\"retired\":%lu,\"readbytes\":%llu,\"stalls\":%llu}}\n",
        (unsigned long)world->stats.reserved_bytes,(unsigned long)world->stats.allocated_slot_bytes,
        (unsigned long long)world->stats.completed_loads,(unsigned long)dl_stream_rom_retired(),
        (unsigned long long)world->stats.bytes_read,(unsigned long long)world->stats.read_stalls);
}
static bool bafford_stream_move(const DlGame *game,float x,DlInput *input){
    float dx=x-game->player.x,dz=49.4f-game->player.z;
    if(dx*dx+dz*dz<.015f)return true;
    float error=atan2f(dx,dz)-game->yaw;
    while(error>3.14159265f)error-=6.28318531f;
    while(error< -3.14159265f)error+=6.28318531f;
    input->turn=fmaxf(-1,fminf(1,-error/(2.3f*DL_TICK_SECONDS)));
    input->forward=fabsf(error)<.1f?1:0;return false;
}
static DlInput bafford_stream_test_input(const DlGame *game,const DlLauncher *launcher,DlLaunchInput *menu){
    static unsigned stage,warmup;DlInput input={0};*menu=(DlLaunchInput){0};++bafford_stream_test_tick;
    if(launcher->menu_open){
        int probe=-1;for(int i=0;i<launcher->bundle->level_count;++i)
            if(strcmp(launcher->bundle->levels[i].id,"bafford-stream")==0||
               strcmp(launcher->bundle->levels[i].id,"bafford-expanded")==0)probe=i;
        assertf(probe>=0,"Bafford stream entry is missing from diagnostic menu");
        menu->level_delta=probe-launcher->selected_level;menu->accept=true;return input;
    }
    assertf(dl_stream_rom_active(),"Bafford diagnostic did not select a stream mission");
    assertf(!game->caught&&!game->complete,"Bafford diagnostic reached an unexpected terminal state");
    assertf(bafford_stream_test_tick<7200,"Bafford diagnostic timed out stage=%u",stage);
    if(stage==0){
        if(++warmup<90)return input;
        bafford_stream_checkpoint("start",game);
#ifdef DL_BAFFORD_STREAM_TEST_CAPTURE
        dl_render_request_capture(1);
#endif
        ++stage;
    }
    assertf(game->grounded&&fabsf(game->player.y+2.4384f)<.03f&&
        dl_position_clear(game,game->player,.18f,1.65f),"Bafford street lost floor/body clearance");
    assertf(dl_stream_rom_current(game),"Bafford traversal left covered regions");
    const DlWorldStream *world=dl_stream_rom_world();unsigned resident=0;
    for(size_t i=0;i<world->slot_count;++i)resident+=world->slots[i].state==DL_STREAM_RESIDENT;
    assertf(resident==2&&world->stats.completed_loads==2&&!dl_stream_rom_retired(),
        "Open Bafford street must retain both visible regions");
    switch(stage){
    case 1:if(bafford_stream_move(game,-6.522049f,&input)){bafford_stream_checkpoint("west_ready",game);++stage;}break;
    case 2:if(bafford_stream_move(game,-2.5f,&input)){bafford_stream_checkpoint("east_crossed",game);++stage;}break;
    case 3:if(bafford_stream_move(game,2.2f,&input)){bafford_stream_checkpoint("east_turnaround",game);++stage;}break;
    case 4:
#ifdef DL_BAFFORD_STREAM_TEST_CAPTURE
        {float error=atan2f(-6.522049f-game->player.x,49.4f-game->player.z)-game->yaw;
        while(error>3.14159265f)error-=6.28318531f;
        while(error< -3.14159265f)error+=6.28318531f;
        if(fabsf(error)>.05f){input.turn=fmaxf(-1,fminf(1,-error/(2.3f*DL_TICK_SECONDS)));break;}
        dl_render_request_capture(2);}
#endif
        ++stage;break;
    case 5:if(bafford_stream_move(game,-3.6f,&input)){bafford_stream_checkpoint("west_crossed",game);++stage;}break;
    case 6:if(bafford_stream_move(game,-6.522049f,&input)){bafford_stream_checkpoint("west_return",game);++stage;}break;
    case 7:if(bafford_stream_move(game,2.2f,&input)){bafford_stream_checkpoint("east_return",game);++stage;}break;
    case 8:
#ifdef DL_BAFFORD_STREAM_TEST_CAPTURE
        debugf("DL64 bafford_capture_complete views=2\n");
#endif
        debugf("DL64 bafford_stream_test_complete passed=1 input_only=1 coverage=1 feet_stable=1 collision_clear=1 audio_calls=%lu\n",(unsigned long)audio_call_total);
        ++stage;break;
    default:break;
    }
    return input;
}
#endif
#ifdef DL_BAFFORD_EXPANDED_TEST
static uint64_t bafford_expanded_tick;
static void bafford_expanded_checkpoint(const char *name,const DlGame *game,unsigned jumps){
    const DlWorldStream *world=dl_stream_rom_world();heap_stats_t heap;sys_get_heap_stats(&heap);
    assertf(world,"Expanded Bafford diagnostic has no mission");
    debugf("DL64 bafford_expanded {\"checkpoint\":\"%s\",\"tick\":%llu,\"elapsed\":%.4f,\"player\":[%.4f,%.4f,%.4f],\"grounded\":%d,\"vertical_velocity\":%.4f,\"jump_inputs\":%u,\"collision_clear\":%d,\"heap_used\":%d,\"models\":%d,\"colliders\":%d,\"textures\":%d,\"stream\":{\"current\":%lu,\"resident\":[",
        name,(unsigned long long)bafford_expanded_tick,game->elapsed,
        game->player.x,game->player.y,game->player.z,game->grounded,game->vertical_velocity,jumps,
        dl_position_clear(game,game->player,.18f,1.65f),heap.used,
        game->level->model_count,game->level->collider_count,game->level->texture_count,
        (unsigned long)dl_stream_rom_current(game));
    bool first=true;for(size_t i=0;i<world->slot_count;++i)if(world->slots[i].state==DL_STREAM_RESIDENT){
        debugf("%s%lu",first?"":",",(unsigned long)world->slots[i].region_id);first=false;}
    debugf("],\"reserved\":%lu,\"allocated\":%lu,\"completed_loads\":%llu,\"retired\":%lu,\"readbytes\":%llu,\"stalls\":%llu}}\n",
        (unsigned long)world->stats.reserved_bytes,(unsigned long)world->stats.allocated_slot_bytes,
        (unsigned long long)world->stats.completed_loads,(unsigned long)dl_stream_rom_retired(),
        (unsigned long long)world->stats.bytes_read,(unsigned long long)world->stats.read_stalls);
}
static bool bafford_expanded_move(const DlGame *game,float x,float floor,DlInput *input){
    float dx=x-game->player.x,dz=48.5f-game->player.z;
    if(dx*dx+dz*dz<.0121f)return game->grounded&&fabsf(game->player.y-floor)<.015f;
    float error=atan2f(dx,dz)-game->yaw;
    while(error>3.14159265f)error-=6.28318531f;
    while(error< -3.14159265f)error+=6.28318531f;
    input->turn=fmaxf(-1,fminf(1,-error/(2.3f*DL_TICK_SECONDS)));
    input->forward=fabsf(error)<.1f?1:0;return false;
}
static DlInput bafford_expanded_input(const DlGame *game,const DlLauncher *launcher,DlLaunchInput *menu){
    static unsigned stage,warmup,jumps;static bool air_seen[2];
    DlInput input={0};*menu=(DlLaunchInput){0};++bafford_expanded_tick;
    if(launcher->menu_open){
        int probe=-1;for(int i=0;i<launcher->bundle->level_count;++i)
            if(strcmp(launcher->bundle->levels[i].id,"bafford-expanded")==0)probe=i;
        assertf(probe>=0,"Expanded Bafford entry missing from menu");
        menu->level_delta=probe-launcher->selected_level;menu->accept=true;return input;
    }
    assertf(dl_stream_rom_active()&&!game->caught&&!game->complete,"Expanded Bafford has unexpected mission state");
    assertf(bafford_expanded_tick<7200,"Expanded Bafford timed out stage=%u",stage);
    if(!stage){if(++warmup<90)return input;bafford_expanded_checkpoint("start",game,jumps);
#ifdef DL_BAFFORD_EXPANDED_TEST_CAPTURE
        dl_render_request_capture(1);
#endif
        ++stage;}
    assertf(dl_stream_rom_current(game)&&game->player.y>=-2.4684f&&game->player.y<=-1.35f&&
        dl_position_clear(game,game->player,.18f,1.65f),"Expanded street lost coverage/body clearance");
    if(game->grounded)assertf(fabsf(game->player.y+2.4384f)<.015f||fabsf(game->player.y+2.1336f)<.015f,
        "Expanded street reached an unexpected floor");
    const DlWorldStream *world=dl_stream_rom_world();unsigned resident=0;
    for(size_t i=0;i<world->slot_count;++i)resident+=world->slots[i].state==DL_STREAM_RESIDENT;
    assertf(resident==2&&world->stats.completed_loads==2&&!dl_stream_rom_retired(),"Expanded open street lost visible regions");
    switch(stage){
    case 1:if(bafford_expanded_move(game,-6.522049f,-2.4384f,&input)){bafford_expanded_checkpoint("west_center",game,jumps);++stage;}break;
    case 2:if(bafford_expanded_move(game,7.8f,-2.4384f,&input)){bafford_expanded_checkpoint("east_approach",game,jumps);++stage;}break;
    case 3:input.jump=true;++jumps;bafford_expanded_checkpoint("jump_east_takeoff",game,jumps);++stage;break;
    case 4:
        if(!air_seen[0]&&!game->grounded&&game->player.x>8.382f&&game->player.y> -2.0336f){
            air_seen[0]=true;bafford_expanded_checkpoint("jump_east_air",game,jumps);}
        if(bafford_expanded_move(game,9.8f,-2.1336f,&input)){assertf(air_seen[0],"East bridge ascent skipped physical jump");
            bafford_expanded_checkpoint("bridge_east_landed",game,jumps);++stage;}break;
    case 5:if(bafford_expanded_move(game,11.5f,-2.1336f,&input)){bafford_expanded_checkpoint("bridge_east_edge",game,jumps);++stage;}break;
    case 6:if(bafford_expanded_move(game,15.1f,-2.4384f,&input)){bafford_expanded_checkpoint("east_street",game,jumps);++stage;}break;
    case 7:
#ifdef DL_BAFFORD_EXPANDED_TEST_CAPTURE
        {float error=atan2f(-game->player.x,48.5f-game->player.z)-game->yaw;
        while(error>3.14159265f)error-=6.28318531f;
        while(error< -3.14159265f)error+=6.28318531f;
        if(fabsf(error)>.05f){input.turn=fmaxf(-1,fminf(1,-error/(2.3f*DL_TICK_SECONDS)));break;}
        dl_render_request_capture(2);}
#endif
        ++stage;break;
    case 8:if(bafford_expanded_move(game,12.3f,-2.4384f,&input)){bafford_expanded_checkpoint("west_approach",game,jumps);++stage;}break;
    case 9:input.jump=true;++jumps;bafford_expanded_checkpoint("jump_west_takeoff",game,jumps);++stage;break;
    case 10:
        if(!air_seen[1]&&!game->grounded&&game->player.x<12.0396f&&game->player.y> -2.0336f){
            air_seen[1]=true;bafford_expanded_checkpoint("jump_west_air",game,jumps);}
        if(bafford_expanded_move(game,10.8f,-2.1336f,&input)){assertf(air_seen[1],"West bridge ascent skipped physical jump");
            bafford_expanded_checkpoint("bridge_west_landed",game,jumps);++stage;}break;
    case 11:if(bafford_expanded_move(game,8.8f,-2.1336f,&input)){bafford_expanded_checkpoint("bridge_west_edge",game,jumps);++stage;}break;
    case 12:if(bafford_expanded_move(game,7.8f,-2.4384f,&input)){bafford_expanded_checkpoint("west_street",game,jumps);++stage;}break;
    case 13:if(bafford_expanded_move(game,-6.522049f,-2.4384f,&input)){bafford_expanded_checkpoint("west_return",game,jumps);++stage;}break;
    case 14:
        assertf(jumps==2&&air_seen[0]&&air_seen[1],"Expanded street jump evidence incomplete");
#ifdef DL_BAFFORD_EXPANDED_TEST_CAPTURE
        debugf("DL64 bafford_expanded_capture_complete views=2\n");
#endif
        debugf("DL64 bafford_expanded_test_complete passed=1 input_only=1 coverage=1 bridge_jumps=2 collision_clear=1 audio_calls=%lu\n",(unsigned long)audio_call_total);
        ++stage;break;
    default:break;
    }
    return input;
}
#endif
#ifdef DL_BAFFORD_DISTRICT_TEST
#include "stream_scene.h"
static uint64_t bafford_district_tick;
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
static uint64_t district_perf_tick_offset;
static float district_perf_motion_seconds;
static uint32_t district_perf_blocked_ticks;
/* 1: initial/restart admission; 2: loading delays an authored interaction;
 * 4: interaction state not yet applied. Ordinary walking never waits for IO. */
static uint32_t district_perf_route_wait;
static bool district_perf_use_loading;
static bool district_perf_complete_logged;
static bool district_perf_complete_pending;
static unsigned district_perf_complete_visited;
static uint64_t district_perf_complete_loads;
#endif
static uint32_t bafford_district_visited;
static unsigned bafford_district_jumps;
static int bafford_district_door=-1,bafford_district_loot=-1;
static uint32_t bafford_district_loot_home;
static bool bafford_district_loot_retired,bafford_district_loot_reloaded;
static bool bafford_district_resident(const DlWorldStream *world,uint32_t id){
    for(size_t i=0;i<world->slot_count;++i)
        if(world->slots[i].state==DL_STREAM_RESIDENT&&world->slots[i].region_id==id)return true;
    return false;
}
static bool bafford_district_settled(const DlWorldStream *world){
    if(!dl_stream_rom_settled())return false;
    for(size_t i=0;i<world->slot_count;++i)
        if(world->slots[i].state!=DL_STREAM_FREE&&world->slots[i].state!=DL_STREAM_RESIDENT)return false;
    return true;
}
static void bafford_district_checkpoint(const char *name,const DlGame *game,unsigned epoch){
    const DlWorldStream *world=dl_stream_rom_world();heap_stats_t heap;sys_get_heap_stats(&heap);
    int triangles=0;for(int i=0;i<game->level->model_count;++i)
        if(game->level->models[i].role==DL_MODEL_STATIC)
            triangles+=game->level->meshes[game->level->models[i].mesh].index_count/3;
    debugf("DL64 bafford_district {\"checkpoint\":\"%s\",\"epoch\":%u,\"tick\":%llu,\"elapsed\":%.4f,\"player\":[%.4f,%.4f,%.4f],\"grounded\":%d,\"vertical_velocity\":%.4f,\"jump_inputs\":%u,\"collision_clear\":%d,\"heap_used\":%d,\"models\":%d,\"triangles\":%d,\"colliders\":%d,\"floors\":%d,\"textures\":%d,\"door_index\":%d,\"door_open\":%d,\"loot_index\":%d,\"loot_home\":%lu,\"loot_value\":%lu,\"collected_objects\":%llu,\"loot_retired\":%d,\"loot_reloaded\":%d,\"stream\":{\"current\":%lu,\"resident\":[",
        name,epoch,(unsigned long long)bafford_district_tick,game->elapsed,
        game->player.x,game->player.y,game->player.z,game->grounded,game->vertical_velocity,bafford_district_jumps,
        dl_position_clear(game,game->player,.18f,1.65f),heap.used,
        game->level->model_count,triangles,game->level->collider_count,game->level->floor_count,game->level->texture_count,
        bafford_district_door,dl_game_object_open(game,bafford_district_door),bafford_district_loot,
        (unsigned long)bafford_district_loot_home,(unsigned long)game->loot_value,(unsigned long long)game->collected_objects,
        bafford_district_loot_retired,bafford_district_loot_reloaded,(unsigned long)dl_stream_rom_current(game));
    bool first=true;for(size_t i=0;i<world->slot_count;++i)if(world->slots[i].state==DL_STREAM_RESIDENT){
        debugf("%s%lu",first?"":",",(unsigned long)world->slots[i].region_id);first=false;}
    debugf("]");
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    uint32_t view_mask=dl_stream_rom_view_mask();first=true;debugf(",\"view\":[");
    for(uint32_t id=1;id<=world->mission->region_count;++id)if(view_mask&(UINT32_C(1)<<(id-1))){
        debugf("%s%lu",first?"":",",(unsigned long)id);first=false;}
    debugf("]");
#endif
    debugf(",\"slots\":%lu,\"settled\":%d,\"reserved\":%lu,\"allocated\":%lu,\"completed_loads\":%llu,\"retired\":%lu,\"readbytes\":%llu,\"stalls\":%llu}}\n",
        (unsigned long)world->slot_count,bafford_district_settled(world),(unsigned long)world->stats.reserved_bytes,
        (unsigned long)world->stats.allocated_slot_bytes,(unsigned long long)world->stats.completed_loads,
        (unsigned long)dl_stream_rom_retired(),(unsigned long long)world->stats.bytes_read,(unsigned long long)world->stats.read_stalls);
}
static bool bafford_district_drive(const DlGame *game,DlVec3 target,DlInput *input){
    float dx=target.x-game->player.x,dz=target.z-game->player.z,distance=sqrtf(dx*dx+dz*dz);
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    /* A single input is reused across all ticks in this render frame. Limit
     * only the final approach so catch-up ticks cannot pass the authored stair
     * landing. Farther away, retain ordinary full-speed movement. */
    float tolerance=.002f,arrival=.025f,magnitude=1;
    float travel=2.1f*district_perf_motion_seconds;
    if(travel>distance)magnitude=distance/travel;
#else
    float tolerance=.08f,arrival=.1f,magnitude=1;
#endif
    if(distance>tolerance){float s=sinf(game->yaw),c=cosf(game->yaw);
        input->forward=magnitude*(s*dx+c*dz)/distance;input->strafe=magnitude*(-c*dx+s*dz)/distance;}
    return distance<arrival&&game->grounded&&fabsf(game->player.y-target.y)<.04f;
}
#ifdef DL_BAFFORD_DISTRICT_TEST_CAPTURE
static bool bafford_district_turn(const DlGame *game,float x,float z,DlInput *input){
    float error=atan2f(x-game->player.x,z-game->player.z)-game->yaw;
    while(error>3.14159265f)error-=6.28318531f;
    while(error< -3.14159265f)error+=6.28318531f;
    if(fabsf(error)<.05f)return true;
    input->turn=fmaxf(-1,fminf(1,-error/(2.3f*DL_TICK_SECONDS)));return false;
}
#endif
static DlInput bafford_district_input(const DlGame *game,const DlLauncher *launcher,DlLaunchInput *menu){
    typedef struct {const char *name;DlVec3 point;bool jump;int use,capture;} Waypoint;
    static const Waypoint route[]={
        {"street_center",{0,-2.4384f,48.5f},false,0,0},
        {"ramp_start",{0,-2.35f,45.98f},false,0,0},
        {"ramp_top",{0,-1.0935f,40.95f},false,0,0},
        {"first_stair",{0,-.6096f,40.4f},true,0,0},
        {"door_open",{0,-.6096f,40.4f},false,1,0},
        {"entrance",{0,0,39.3f},true,0,0},
        {"main_room",{0,0,34.6f},false,0,0},
        {"loot_collected",{0,0,34.6f},false,2,1},
        {"west_exit",{-2.25f,0,33.3f},false,0,0},
        {"rear_entry",{-2.25f,0,32.2f},false,0,0},
        {"courtyard",{-2.25f,0,29.8f},false,0,2},
        {"courtyard_east_approach",{5,0,29.8f},false,0,0},
        {"courtyard_east_crossed",{5,0,33.1f},false,0,0},
        {"courtyard_east_back",{5,0,29.8f},false,0,0},
        {"courtyard_after_east",{-2.25f,0,29.8f},false,0,0},
        {"next_approach",{-1.2f,0,26.3f},false,0,0},
        {"next_deep",{-1.2f,0,23.9f},false,0,0},
        {"next_west_approach",{-1.2f,0,26.5f},false,0,0},
        {"next_west_crossed",{-4.2f,0,26.5f},false,0,0},
        {"next_west_return",{-1.2f,0,26.5f},false,0,0},
        {"courtyard_after_next",{-1.2f,0,28.5f},false,0,0},
        {"west_approach",{-5,0,28.5f},false,0,0},
        {"west_deep",{-5,0,26.3f},false,0,0},
        {"west_return",{-5,0,28.5f},false,0,0},
        {"courtyard_after_west",{-2.25f,0,29.8f},false,0,0},
        {"west_corner_return",{-2.25f,0,32.2f},false,0,0},
        {"west_exit_return",{-2.25f,0,33.3f},false,0,0},
        {"main_room_reloaded",{0,0,34.6f},false,0,0},
        {"door_return",{0,0,39.3f},false,0,0},
        {"door_closed",{0,-.3048f,40.4f},false,1,0},
        {"ramp_return",{0,-2.35f,45.98f},false,0,0},
        {"street_return",{0,-2.4384f,48.5f},false,0,0},
        {"east_street",{5,-2.4384f,48.5f},false,0,0},
        {"east_ramp_top",{5,-.056f,36.8f},false,0,0},
        {"east_interior",{5,0,35.5f},false,0,3}
    };
    static unsigned stage,warmup;static bool initiated,used,air_seen[2];static uint64_t prior_loads;
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    static unsigned hold;
#endif
    DlInput input={0};*menu=(DlLaunchInput){0};
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    district_perf_route_wait=0;
#endif
    if(launcher->menu_open){int target=0;bool found=false;
        for(int i=0;i<launcher->bundle->level_count;++i)if(!strcmp(launcher->bundle->levels[i].id,"bafford-district")){target=i;found=true;}
        assertf(found,"District diagnostic bundle entry absent");
        if(launcher->selected_level!=target)menu->level_delta=1;else menu->accept=true;return input;
    }
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    ++bafford_district_tick;
#endif
    const DlWorldStream *world=dl_stream_rom_world();assertf(world,"District diagnostic mission absent");
    if(stage==0){
        if(++warmup<90||!bafford_district_settled(world)){
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            district_perf_route_wait=1;
#endif
            return input;}
        for(int i=0;i<game->level->object_count;++i){
            if(game->level->objects[i].kind==DL_OBJECT_DOOR&&bafford_district_door<0)bafford_district_door=i;
            if(game->level->objects[i].kind==DL_OBJECT_LOOT&&bafford_district_loot<0)bafford_district_loot=i;}
        assertf(bafford_district_door>=0&&bafford_district_loot>=0,"District authored gameplay fixtures absent");
        DlMissionEntity entity;assertf(dl_mission_entity(world->mission,game->level->stream->object_entity_ids[bafford_district_loot],&entity)==DL_MISSION_OK,"District loot home invalid");
        bafford_district_loot_home=entity.home_region_id;bafford_district_checkpoint("start",game,0);++stage;
    }
    if(stage>sizeof(route)/sizeof(*route)+2)return input;
    assertf(!game->caught&&!game->complete&&game->player.y>=-2.47f&&game->player.y<.4f&&
        dl_position_clear(game,game->player,.18f,1.65f),"District route lost physical floor/body coverage");
    uint32_t current=dl_stream_rom_current(game);assertf(current,"District route left region coverage");
    bafford_district_visited|=UINT32_C(1)<<(current-1);
    bool collected=(game->collected_objects&(UINT64_C(1)<<bafford_district_loot))!=0;
    if(collected&&!bafford_district_resident(world,bafford_district_loot_home))bafford_district_loot_retired=true;
    if(collected&&bafford_district_loot_retired&&bafford_district_resident(world,bafford_district_loot_home))bafford_district_loot_reloaded=true;
    assertf(bafford_district_tick<18000,"District input route timed out");
    if(stage<=sizeof(route)/sizeof(*route)){
        const Waypoint *waypoint=&route[stage-1];
        if(!initiated){
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            if(!bafford_district_settled(world))return input;
#endif
            initiated=true;
            if(waypoint->jump){assertf(game->grounded,"District stair jump lacks takeoff support");
                ++bafford_district_jumps;input.jump=true;
                bafford_district_checkpoint(bafford_district_jumps==1?"jump1_takeoff":"jump2_takeoff",game,0);}
        }
        if(waypoint->jump&&!game->grounded&&game->vertical_velocity>0&&!air_seen[bafford_district_jumps-1]){
            air_seen[bafford_district_jumps-1]=true;bafford_district_checkpoint(bafford_district_jumps==1?"jump1_air":"jump2_air",game,0);}
        DlVec3 drive_target=waypoint->point;
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        /* The ordinary successful jump lands at z40.4684 on the first tread.
         * The body's radius at nominal z40.4 can already touch the higher
         * next tread; use the witnessed clear landing for both nearby actions. */
        if(stage==4||stage==5)drive_target.z=40.4684f;
        /* The return interaction must stay inside the door's use radius.
         * This is the supported position witnessed in the ordinary tour. */
        if(stage==30)drive_target.z=40.3352f;
#endif
        bool reached=bafford_district_drive(game,drive_target,&input);
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        if(waypoint->use){
            /* Use is applied before movement within the next simulation tick.
             * Finish the precise approach before issuing the one-shot action,
             * so a still-occupied closing door cannot reject an early press. */
            float x=drive_target.x-game->player.x,z=drive_target.z-game->player.z;
            if(x*x+z*z>.000004f)reached=false;
        }
#endif
        if(reached
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            &&bafford_district_settled(world)
#endif
        ){
            if(waypoint->use&&!used){used=true;input.use=true;return input;}
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            if((waypoint->use==1&&dl_game_object_open(game,bafford_district_door)!=(stage==5))||
               (waypoint->use==2&&!collected)){
                district_perf_route_wait=4|(district_perf_use_loading?2:0);return input;}
            district_perf_use_loading=false;
#else
            if(++hold<12)return input;
#endif
            if(waypoint->jump)assertf(air_seen[bafford_district_jumps-1],"District stair crossing skipped real jump");
            if(waypoint->use==1)assertf(dl_game_object_open(game,bafford_district_door)==(stage==5),"District direct door state failed");
            if(waypoint->use==2)assertf(collected&&game->loot_value==game->level->objects[bafford_district_loot].value,"District pickup failed");
            if(!strcmp(waypoint->name,"main_room_reloaded"))assertf(collected&&bafford_district_loot_retired&&bafford_district_loot_reloaded&&dl_game_object_open(game,bafford_district_door),"District mission state did not survive home retirement/reload");
#ifdef DL_BAFFORD_DISTRICT_TEST_CAPTURE
            if(waypoint->capture){float x=waypoint->capture==1?-2.25f:waypoint->capture==2?-1.2f:5;
                float z=waypoint->capture==1?33.3f:waypoint->capture==2?23.9f:42.67f;
                if(!bafford_district_turn(game,x,z,&input))return input;
                dl_render_request_capture(waypoint->capture);}
#endif
            bafford_district_checkpoint(waypoint->name,game,0);++stage;initiated=false;used=false;
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            hold=0;
        }else hold=0;
#else
        }
#endif
    }else if(stage==sizeof(route)/sizeof(*route)+1){
        prior_loads=world->stats.completed_loads;assertf(dl_stream_rom_retired()&&bafford_district_jumps==2,"District residency/jump evidence incomplete");
        input.restart=true;++stage;warmup=0;
    }else{
        if(++warmup<90||!bafford_district_settled(world)){
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            district_perf_route_wait=1;
#endif
            return input;}
        assertf(!game->collected_objects&&!game->loot_value&&!dl_game_object_open(game,bafford_district_door)&&
            fabsf(game->player.x+6.522049f)<.03f&&fabsf(game->player.y+2.4384f)<.03f&&fabsf(game->player.z-49.8252936f)<.03f,"District remote restart did not rebuild source spawn");
        unsigned visited=0;for(unsigned i=0;i<32;++i)visited+=(bafford_district_visited>>i)&1u;
        assertf(visited>=5&&prior_loads>visited,"District lacked multi-region load/retire/reload proof");
        bafford_district_checkpoint("restart_loaded",game,1);
#ifdef DL_BAFFORD_DISTRICT_TEST_CAPTURE
        debugf("DL64 bafford_district_capture_complete views=3\n");
#endif
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        district_perf_complete_pending=true;district_perf_complete_visited=visited;district_perf_complete_loads=prior_loads;
#else
        debugf("DL64 bafford_district_test_complete passed=1 input_only=1 coverage=1 collision_clear=1 stair_jumps=2 visited_regions=%u loot_persist=1 restart_reload=1 pre_restart_loads=%llu audio_calls=%lu\n",
            visited,(unsigned long long)prior_loads,(unsigned long)audio_call_total);
#endif
        ++stage;
    }
    return input;
}
#endif
static void gameplay_tick(void *context, const DlInput *input, uint64_t tick) {
    (void)tick; DlGame *game=context;
    if(stream_recovery_pending)return;
#ifndef DL_INPUT_REPLAY
    float elapsed_before_tick=game->elapsed;
#endif
    DlInput effective=*input;dl_stream_rom_filter_input(game,&effective);
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    if((input->forward&&!effective.forward)||(input->strafe&&!effective.strafe)||(input->jump&&!effective.jump))
        ++district_perf_blocked_ticks;
    if(input->use&&!effective.use){district_perf_use_loading=true;district_perf_route_wait|=2;}
#endif
    input=&effective;
#ifdef DL_INPUT_REPLAY
    float before=game->elapsed;
    if(input->restart)dl_game_start(game,game->level,replay_start_preset);
    else dl_game_update(game,input,DL_TICK_SECONDS);
    if(game->elapsed<before&&!input->restart)dl_game_start(game,game->level,replay_start_preset);
#else
    dl_game_update(game,input,DL_TICK_SECONDS);
    /* A frame can contain several fixed ticks. Stop advancing after the game
     * recovers from a fall: its spawn must be reloaded before physics resumes
     * against what was the remote region's working collision scene. */
    if(dl_stream_rom_active()&&game->elapsed<elapsed_before_tick){
        stream_recovery_pending=true;return;
    }
#endif
    for(unsigned i=0;i<game->event_count;++i)event_sound(game,&game->events[i]);
    for(unsigned i=0;i<game->event_count;++i)if(game->events[i].kind==DL_EVENT_GUARD_STATE) {
        const DlWorldEvent *event=&game->events[i];const DlEnemy *enemy=&game->enemies[event->actor];
        debugf("DL64 guard_state from=%s to=%s t=%.2f sees=%d hears=%d alert=%.3f id=%s index=%d\n",
        dl_guard_state_name((DlGuardState)event->previous),dl_guard_state_name((DlGuardState)event->detail),event->time,enemy->sees_player,enemy->heard_sound,
        enemy->awareness,game->level->enemies[event->actor].id,event->actor);
    }
#ifdef DL_INPUT_REPLAY
    debugf("DL64 replay_tick {\"tick\":%llu,\"player\":[%.7f,%.7f,%.7f],\"yaw\":%.7f,\"pitch\":%.7f,\"visibility\":%.7f,\"door_open\":%d,\"complete\":%d,\"caught\":%d,\"loot_value\":%lu,\"enemies\":[",
        (unsigned long long)(tick+1),game->player.x,game->player.y,game->player.z,game->yaw,game->pitch,game->visibility,
        game->door_open,game->complete,game->caught,(unsigned long)game->loot_value);
    for(int i=0;i<game->enemy_count;++i){const DlEnemy *e=&game->enemies[i];
        debugf("%s{\"position\":[%.7f,%.7f,%.7f],\"state\":\"%s\",\"awareness\":%.7f,\"patrol_index\":%d,\"sees_player\":%d,\"heard_sound\":%d}",
            i?",":"",e->position.x,e->position.y,e->position.z,dl_guard_state_name(e->state),e->awareness,e->patrol_index,e->sees_player,e->heard_sound);}
    debugf("],\"objects\":[");
    for(int i=0;i<game->level->object_count;++i){uint64_t bit=UINT64_C(1)<<i;
        debugf("%s{\"open\":%d,\"collected\":%d,\"completed\":%d}",i?",":"",dl_game_object_open(game,i),
            (game->collected_objects&bit)!=0,(game->completed_objectives&bit)!=0);}
    debugf("],\"events\":[");
    for(unsigned i=0;i<game->event_count;++i){const DlWorldEvent *e=&game->events[i];
        debugf("%s{\"kind\":%d,\"position\":[%.7f,%.7f,%.7f],\"time\":%.7f,\"strength\":%.7f,\"actor\":%d,\"detail\":%d,\"previous\":%d}",
            i?",":"",e->kind,e->position.x,e->position.y,e->position.z,e->time,e->strength,e->actor,e->detail,e->previous);}
    debugf("],\"grounded\":%d,\"crouched\":%d}\n",game->grounded,game->crouched);
#endif
}

int main(void) {
    debug_init_emulog();
    int memory = get_memory_size();
    require_expansion_pak(memory);
#ifdef DL_RENDER_T3D
    /* One scanning, one queued, one being prepared. This lets the CPU prepare
     * poses while RSP/RDP finish the preceding frame (153,600 extra bytes). */
    display_init(RESOLUTION_320x240, DEPTH_16_BPP, 3, GAMMA_NONE, FILTERS_RESAMPLE);
#else
    display_init(RESOLUTION_320x240, DEPTH_16_BPP, 2, GAMMA_NONE, FILTERS_RESAMPLE);
#endif
    dl_profile_init_display();
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
    if(dl_bundle.has_rom_assets)assertf(dfs_init(DFS_DEFAULT_LOCATION)==DFS_ESUCCESS,"ROM asset filesystem unavailable");
    dl_music_init();
    dl_render_init();

    DlLauncher launcher;
    assertf(dl_launcher_init(&launcher,&dl_bundle),"Invalid level bundle");
    DlGame game = {0};
    DlTickClock clock = {0};
    DlRenderStats stats = { .memory_bytes = memory, .frame_ms = 16.67f };
#ifdef DL_AUTOPLAY
    /* Start hidden profiling runs with the ordinary gameplay HUD. */
    stats.debug = false;
    bool profile_terminal = false;
#endif
#ifdef DL_DEBUG_OVERLAY
    stats.debug = true;
#endif
    if (launcher.has_active) start_level(&game,&launcher,&stats);
#ifdef DL_INPUT_REPLAY
    replay_start_preset=launcher.active_start;
#endif
    debugf("DL64 controls stick=look dpad=look c_up_down=move c_left_right=strafe l=jump z=crouch a=use b=noise r=restart start=debug z_start=level_menu\n");
    debugf("DL64 menu_ready levels=%d initial=%s mode=%s\n",dl_bundle.level_count,
        dl_bundle.levels[launcher.selected_level].id,launcher.menu_open?"menu":"direct");
#ifdef DL_AUTOPLAY
    debugf("DL64 replay_start mode=input-only scene_version=%lu fixed_dt=0.016666667\n",
        (unsigned long)game.level->version);
#endif
    uint32_t previous = TICKS_READ(), next_report = previous;
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
    uint32_t perf_sequence=0,perf_epoch=0,perf_total_dropped=0;
    bool perf_started=false;
    debugf("DL64 stream_perf_config version=2 ticks_per_second=%lu sim_rate=%u dt=wall route=continuous frame_budget_hz=30 service_budget_us=8000\n",
        (unsigned long)TICKS_PER_SECOND,DL_TICK_RATE);
#endif
    while (1) {
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        uint32_t perf_frame_start=TICKS_READ();
#endif
        dl_profile_frame_begin(stats.debug);
        /* PI world reads use a separate aligned staging buffer. Service music
         * once that transfer finishes so foreground cartridge users serialize. */
        if(dl_stream_rom_music_ready())dl_music_service();
        uint32_t frame_start = TICKS_READ();
        uint32_t interval_ticks=frame_start-previous;
        float elapsed = (float)interval_ticks / TICKS_PER_SECOND;
        previous = frame_start;
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        if (elapsed < 0.001f) elapsed = 1.0f / 60.0f;
#endif
        stats.frame_ms += (elapsed * 1000.0f - stats.frame_ms) * 0.06f;
        /* The core subdivides collision movement; this cap prevents focus
         * loss or emulator debugger pauses from jumping the simulation. */
        float dt = elapsed;
        DlProfileMark mark = dl_profile_mark();
        bool next_debug = stats.debug;
        DlLaunchInput menu;
        DlInput input = read_input(&next_debug,&launcher,&menu);
#ifdef DL_STREAM_TEST
        dt=DL_TICK_SECONDS;
        input=stream_test_input(&game,&launcher,&menu);
#endif
#ifdef DL_BAFFORD_STREAM_TEST
        dt=DL_TICK_SECONDS;
        input=bafford_stream_test_input(&game,&launcher,&menu);
#endif
#ifdef DL_BAFFORD_EXPANDED_TEST
        dt=DL_TICK_SECONDS;
        input=bafford_expanded_input(&game,&launcher,&menu);
#endif
#ifdef DL_BAFFORD_DISTRICT_TEST
#ifndef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        dt=DL_TICK_SECONDS;
#else
        bafford_district_tick=district_perf_tick_offset+clock.tick;
        double perf_submitted=fmin((double)dt,(double)DL_MAX_FRAME_TICKS/DL_TICK_RATE);
        unsigned perf_predicted=(unsigned)floor((clock.accumulator+perf_submitted+1e-9)*DL_TICK_RATE);
        if(perf_predicted>DL_MAX_FRAME_TICKS)perf_predicted=DL_MAX_FRAME_TICKS;
        district_perf_motion_seconds=(float)((double)perf_predicted/DL_TICK_RATE);
#endif
        input=bafford_district_input(&game,&launcher,&menu);
#endif
#ifdef DL_INPUT_REPLAY
        input=(DlInput){0};menu=(DlLaunchInput){0};
#endif
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
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
            if(perf_started){district_perf_tick_offset+=clock.tick;++perf_epoch;
                perf_total_dropped+=(uint32_t)ceil(clock.dropped_seconds*TICKS_PER_SECOND);}
            perf_started=true;
#endif
            clock=(DlTickClock){0};
            start_level(&game,&launcher,&stats);
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
            dl_tick_clear_pending(&clock);
            if (action == DL_LAUNCH_MENU_OPENED) clear_sounds();
            dl_music_pause(action == DL_LAUNCH_MENU_OPENED);
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
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        DlProfileMark perf_service_start=dl_profile_mark();
#endif
        dl_stream_rom_service(&game,&stats);
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        DlProfileMark perf_service_end=dl_profile_mark();
        uint32_t perf_service_ticks=perf_service_end.ticks-perf_service_start.ticks;
        uint32_t perf_service_audio=perf_service_end.audio_ticks-perf_service_start.audio_ticks;
        assertf(perf_service_audio<=perf_service_ticks,"Overlapping performance service/audio timing");
        uint32_t perf_stage=dl_stream_rom_service_stage();
        uint32_t perf_sim_before=(uint32_t)clock.tick;
#endif
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
        uint32_t old_sound = game.sound_serial;
        float old_elapsed = game.elapsed;
        mark = dl_profile_mark();
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        district_perf_blocked_ticks=0;
#endif
#ifdef DL_INPUT_REPLAY
        if(clock.tick<DL_REPLAY_TICKS) {
            dl_tick_step(&clock,dl_replay_inputs[clock.tick],gameplay_tick,&game);
            if(clock.tick==DL_REPLAY_TICKS)debugf("DL64 input_replay_done ticks=%llu\n",(unsigned long long)clock.tick);
        } else game.event_count=0;
        (void)dt;(void)old_elapsed;
#else
        dl_tick_advance(&clock, dt, input, gameplay_tick, &game);
#endif
        dl_profile_record(DL_PROFILE_GAMEPLAY, mark);
        /* Falling out of bounds resets the same selected test start as R. */
        if ((game.elapsed < old_elapsed || stream_recovery_pending)
#ifdef DL_INPUT_REPLAY
            && false
#endif
        ) {
            stream_recovery_pending=false;
            dl_profile_frame_end();
            clock=(DlTickClock){0};
            start_level(&game,&launcher,&stats);
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
        if (new_event && game.event != DL_EVENT_NONE && game.event != DL_EVENT_STEP)
            debugf("DL64 event kind=%d t=%.2f door=%d complete=%d caught=%d sound=%lu\n",
                game.event, game.elapsed, game.door_open, game.complete, game.caught, (unsigned long)game.sound_serial);
        dl_profile_record(DL_PROFILE_AUDIO_EVENTS, mark);
        if ((int32_t)(frame_start - next_report) >= 0) {
            dl_music_report();
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
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        uint32_t perf_render_start=TICKS_READ();
#endif
        stats.cpu_ms = dl_render_frame(&game, &stats) + update_ms;
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        uint32_t perf_render_ticks=TICKS_READ()-perf_render_start;
#endif
        dl_stream_rom_submitted();
        ++stats.frames;
        dl_profile_frame_end();
#ifdef DL_BAFFORD_DISTRICT_PERFORMANCE_TEST
        if(!district_perf_complete_logged){
        heap_stats_t perf_heap;sys_get_heap_stats(&perf_heap);
        const DlWorldStream *perf_world=dl_stream_rom_world();uint32_t perf_resident=0;
        for(size_t i=0;i<perf_world->slot_count;++i)if(perf_world->slots[i].state==DL_STREAM_RESIDENT)
            perf_resident|=UINT32_C(1)<<(perf_world->slots[i].region_id-1);
        uint32_t perf_frame_ticks=TICKS_READ()-perf_frame_start;
        /* One compact hexadecimal row per completed gameplay frame. Its output
         * overhead remains in the next raw wall interval; no samples are hidden. */
        debugf("DL64 stream_perf_frame seq=%lu epoch=%lu samples=%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx,%lx\n",
            (unsigned long)++perf_sequence,(unsigned long)perf_epoch,
            (unsigned long)perf_frame_ticks,(unsigned long)interval_ticks,(unsigned long)perf_service_ticks,
            (unsigned long)(perf_service_ticks-perf_service_audio),(unsigned long)perf_render_ticks,
            (unsigned long)llround((double)dt*TICKS_PER_SECOND),(unsigned long)perf_sim_before,(unsigned long)clock.tick,
            (unsigned long)llround(clock.accumulator*TICKS_PER_SECOND),
            (unsigned long)ceil(clock.dropped_seconds*TICKS_PER_SECOND),(unsigned long)perf_stage,
            (unsigned long)perf_resident,(unsigned long)perf_heap.used,(unsigned long)district_perf_blocked_ticks,
            (unsigned long)district_perf_route_wait);
        if(district_perf_complete_pending){
            district_perf_complete_pending=false;district_perf_complete_logged=true;dl_profile_report();dl_music_report();
            debugf("DL64 stream_perf_complete frames=%lu epochs=%lu dropped_ticks=%lu\n",
                (unsigned long)perf_sequence,(unsigned long)(perf_epoch+1),
                (unsigned long)(perf_total_dropped+(uint32_t)ceil(clock.dropped_seconds*TICKS_PER_SECOND)));
            debugf("DL64 bafford_district_test_complete passed=1 input_only=1 coverage=1 collision_clear=1 stair_jumps=2 visited_regions=%u loot_persist=1 restart_reload=1 pre_restart_loads=%llu audio_calls=%lu\n",
                district_perf_complete_visited,(unsigned long long)district_perf_complete_loads,(unsigned long)audio_call_total);
        }
        }
#endif
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
