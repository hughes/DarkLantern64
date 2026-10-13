#ifndef DARKLANTERN_STREAM_ROM_H
#define DARKLANTERN_STREAM_ROM_H

#include "game.h"
#include "render.h"
#include "world_stream.h"

/* N64 platform adapter for bounded authored working sets and legacy probes. */
void dl_stream_rom_close(void);
void dl_stream_rom_start(DlGame *game, const DlLevel *bootstrap, int preset);
void dl_stream_rom_service(DlGame *game, DlRenderStats *stats);
void dl_stream_rom_filter_input(const DlGame *game, DlInput *input);
void dl_stream_rom_submitted(void);
bool dl_stream_rom_music_ready(void);
bool dl_stream_rom_active(void);
const DlWorldStream *dl_stream_rom_world(void);
uint32_t dl_stream_rom_current(const DlGame *game);
uint32_t dl_stream_rom_retired(void);

#endif
