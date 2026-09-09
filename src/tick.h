#ifndef DL_TICK_H
#define DL_TICK_H
#include "game.h"
#ifdef __cplusplus
extern "C" {
#endif
#define DL_TICK_RATE 60
#define DL_TICK_SECONDS (1.0f / DL_TICK_RATE)
#define DL_MAX_FRAME_TICKS 8
typedef struct {
    double accumulator, dropped_seconds;
    uint64_t tick;
    DlInput pending;
} DlTickClock;
typedef void (*DlTickCallback)(void *context, const DlInput *input, uint64_t tick);
/* Edges survive render frames with no tick and are consumed once. Excess wall
 * time is discarded explicitly; simulation never receives a larger step. */
int dl_tick_advance(DlTickClock *clock, double seconds, DlInput input,
                    DlTickCallback callback, void *context);
void dl_tick_step(DlTickClock *clock, DlInput input, DlTickCallback callback, void *context);
void dl_tick_clear_pending(DlTickClock *clock);
#ifdef __cplusplus
}
#endif
#endif
