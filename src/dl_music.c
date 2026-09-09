#include "dl_music.h"
#include <libdragon.h>
#include <stdlib.h>

enum { MUSIC_CHANNELS = 8, MUSIC_BLOCKS = 4 };
static xm64player_t player;
static const DlMusicCue *current;
static int16_t *pcm;
static int block_frames;
static bool initialized, opened;
static volatile bool enabled;
/* One producer on the CPU foreground, one consumer in the AI interrupt.
 * The foreground cannot run while consume's returned block is being mixed. */
static volatile uint32_t read_block, write_block, consumed, underruns;
static int peaks[MUSIC_BLOCKS];
static volatile int heard_peak;
static uint64_t service_ticks;
static uint32_t generated;
static bool loop_reported;

void dl_music_init(void) {
    block_frames = audio_get_buffer_length();
}

const int16_t *dl_music_consume(size_t frames) {
    if (!enabled) return NULL;
    if (frames != (size_t)block_frames || read_block == write_block) {
        ++underruns;
        return NULL;
    }
    uint32_t slot = read_block % MUSIC_BLOCKS;
    const int16_t *out = pcm + slot * block_frames * 2;
    if (peaks[slot] > heard_peak) heard_peak = peaks[slot];
    ++consumed;
    ++read_block;
    return out;
}

static void fill(void) {
    uint32_t start = TICKS_READ();
    /* Bound each call even if a very slow producer cannot fill the queue. */
    for (int n = 0; n < MUSIC_BLOCKS && write_block - read_block < MUSIC_BLOCKS; ++n) {
        uint32_t slot = write_block % MUSIC_BLOCKS;
        int16_t *out = pcm + slot * block_frames * 2;
        /* The inspected mixer_poll synchronizes its high-priority RSP work.
         * Do not drain Tiny3D's normal queue with an additional rspq_wait. */
        mixer_poll(out, block_frames);
        int peak = 0;
        for (int i = 0; i < block_frames * 2; ++i) {
            int value = abs((int)out[i]);
            if (value > peak) peak = value;
        }
        peaks[slot] = peak;
        __asm__ volatile("" ::: "memory");
        ++write_block; /* Publish only complete, RSP-finished PCM. */
        ++generated;
    }
    service_ticks += (uint32_t)(TICKS_READ() - start);
}

void dl_music_service(void) {
    if (opened && enabled) fill();
}

void dl_music_pause(bool paused) {
    enabled = opened && !paused;
}

void dl_music_stop(void) {
    enabled = false;
    if (opened) {
        dl_music_report();
        /* Only foreground calls the player/mixer; the IRQ reads completed PCM.
         * mixer_poll has finished its audio work before control reaches here. */
        xm64player_close(&player);
        opened = false;
        debugf("DL64 music_stop cue=%s\n", current->id);
    }
    current = NULL;
    read_block = write_block = 0;
}

void dl_music_start(const DlMusicCue *cue) {
    dl_music_stop();
    if (!cue || !cue->path) return;
    if (!initialized) {
        mixer_init(MUSIC_CHANNELS);
        pcm = malloc_uncached(MUSIC_BLOCKS * block_frames * 2 * sizeof(int16_t));
        assertf(pcm, "Cannot allocate music PCM queue");
        initialized = true;
    }
    current = cue;
    consumed = underruns = generated = 0;
    heard_peak = 0;
    loop_reported = false;
    service_ticks = 0;
    xm64player_open(&player, cue->path);
    assertf(xm64player_num_channels(&player) <= MUSIC_CHANNELS, "Music channel budget exceeded");
    xm64player_set_loop(&player, true);
    xm64player_set_vol(&player, cue->gain);
    xm64player_play(&player, 0);
    opened = true;
    fill(); /* Prime before the interrupt is allowed to consume. */
    enabled = true;
    debugf("DL64 music_start cue=%s rate=%d block_frames=%d queue_blocks=%d queue_bytes=%u gain=%.2f\n",
           cue->id, audio_get_frequency(), block_frames, MUSIC_BLOCKS,
           (unsigned)(MUSIC_BLOCKS * block_frames * 4), cue->gain);
}

void dl_music_report(void) {
    if (!opened) return;
    disable_interrupts();
    uint32_t blocks = consumed, missing = underruns, queued = write_block - read_block;
    int peak = heard_peak;
    enable_interrupts();
    debugf("DL64 music cue=%s consumed_frames=%llu generated_blocks=%lu queued=%lu underruns=%lu peak=%d service_ticks=%llu ticks_per_second=%lu enabled=%d\n",
           current->id, (unsigned long long)blocks * block_frames, (unsigned long)generated,
           (unsigned long)queued, (unsigned long)missing, peak,
           (unsigned long long)service_ticks, (unsigned long)TICKS_PER_SECOND, enabled);
    if (!loop_reported && (uint64_t)blocks * block_frames >= (uint64_t)audio_get_frequency() * 49) {
        debugf("DL64 music_loop_ok cue=%s underruns=%lu peak=%d\n", current->id, (unsigned long)missing, peak);
        loop_reported = true;
    }
}
