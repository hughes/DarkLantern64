/* Isolated console experiment; no gameplay/rendering load is represented. */
#include <libdragon.h>
#include <malloc.h>
#include <stdio.h>
#include <stdlib.h>

static void audition(const char *label, int seconds) {
    uint64_t frames = 0;
    unsigned blocks = 0;
    int peak = 0;
    size_t heap_peak = 0;
    int length = audio_get_buffer_length();
    int rate = audio_get_frequency();
    while (frames < (uint64_t)seconds * rate) {
        if (!audio_can_write()) continue;
        short *out = audio_write_begin();
        mixer_poll(out, length);
        rspq_wait(); /* Diagnostic PCM access; not a proposed game scheduling policy. */
        for (int i = 0; i < length * 2; ++i) {
            int value = abs((int)out[i]);
            if (value > peak) peak = value;
        }
        audio_write_end();
        frames += length;
        ++blocks;
        size_t heap = mallinfo().uordblks;
        if (heap > heap_peak) heap_peak = heap;
    }
    debugf("DL64 music_phase kind=%s frames=%llu blocks=%u peak=%d heap_peak=%lu\n",
           label, (unsigned long long)frames, blocks, peak, (unsigned long)heap_peak);
}

int main(void) {
    debug_init_emulog();
    console_init();
    console_set_render_mode(RENDER_AUTOMATIC);
    printf("DarkLantern64 music study\n\nLanterns Below\n48 second original loop\n\nFirst: XM64, 8 channels\nThen: stereo VADPCM, 2 channels\n50 seconds each, automatic\n\nStandalone audio test.\nNo game or geometry workload.\n");
    assert(dfs_init(DFS_DEFAULT_LOCATION) == DFS_ESUCCESS);
    audio_init(32000, 3);
    mixer_init(8);
    debugf("DL64 music_boot rate=%d buffer_frames=%d heap_base=%lu\n",
           audio_get_frequency(), audio_get_buffer_length(), (unsigned long)mallinfo().uordblks);
    xm64player_t xm;
    xm64player_open(&xm, "rom:/lanterns_below.xm64");
    xm64player_play(&xm, 0);
    audition("xm64", 50);
    xm64player_stop(&xm);
    while (xm.playing) {
        if (!audio_can_write()) continue;
        short *out = audio_write_begin();
        mixer_poll(out, audio_get_buffer_length());
        rspq_wait();
        audio_write_end();
    }
    xm64player_close(&xm);
    debugf("DL64 music_closed kind=xm64 heap=%lu\n", (unsigned long)mallinfo().uordblks);
    wav64_t wav;
    wav64_open(&wav, "rom:/lanterns_below.wav64");
    wav64_set_loop(&wav, true);
    wav64_play(&wav, 0);
    /* XM leaves its last per-channel gain behind; establish the WAV bus gain. */
    mixer_ch_set_vol(0, 1.0f, 1.0f);
    audition("wav64", 50);
    mixer_ch_stop(0);
    rspq_wait();
    wav64_close(&wav);
    debugf("DL64 music_study_done heap=%lu\n", (unsigned long)mallinfo().uordblks);
    for (;;) {
        if (audio_can_write()) audio_write_silence();
    }
}
