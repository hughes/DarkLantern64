#ifndef DL_MUSIC_H
#define DL_MUSIC_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct { const char *id, *path; float gain; } DlMusicCue;

/* Foreground owner: initialize after audio_init and the ROM filesystem. */
void dl_music_init(void);
void dl_music_start(const DlMusicCue *cue);
void dl_music_stop(void);
void dl_music_pause(bool paused);
void dl_music_service(void);
void dl_music_report(void);

/* AI interrupt only: return one completed PCM block, or NULL for silence.
 * No player, file, allocator, floating point or RSP calls are made here. */
const int16_t *dl_music_consume(size_t frames);
#endif
