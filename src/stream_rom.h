#ifndef DARKLANTERN_STREAM_ROM_H
#define DARKLANTERN_STREAM_ROM_H

#include "game.h"
#include "render.h"
#include "world_stream.h"
#include "stream_coordinator.h"

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
/* Last service work: 0 idle/read, 1 assemble, 2 GPU preparation, 3 publication. */
uint32_t dl_stream_rom_service_stage(void);
bool dl_stream_rom_settled(void);
uint32_t dl_stream_rom_view_mask(void);
void dl_stream_rom_snapshot(const DlGame *game, DlStreamCoordinatorSnapshot *snapshot);
/* Renderer commit observes the new published bank before view_regions is
 * updated by the coordinator. Return that bank's exact ownership identity. */
void dl_stream_rom_lighting_view(const DlGame *game,uint32_t *revision,uint32_t *mask);
/* Foreground log transport. Compile-time disabled in ordinary builds. */
void dl_stream_rom_trace_flush(void);
void dl_stream_rom_trace_poll(void); /* maintain extended clock during menus */
void dl_stream_rom_trace_finish(void);
void dl_stream_rom_trace_startup_ready(const DlGame *game, uint32_t elapsed, uint32_t audio);

#endif
