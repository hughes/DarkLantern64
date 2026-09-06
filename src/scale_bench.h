#ifndef DARKLANTERN_SCALE_BENCH_H
#define DARKLANTERN_SCALE_BENCH_H

#include "game.h"

/* Diagnostic ROM only. Run between frames with the normal audio callback
 * active. Reports CP0 CPU elapsed ticks minus the measured audio ISR time.
 * It neither renders nor changes the caller's authored level. */
void dl_scale_benchmark(const DlLevel *level);

#endif
