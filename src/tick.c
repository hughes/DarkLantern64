#include "tick.h"
#include <math.h>
#include <string.h>
static void queue_input(DlTickClock *c, DlInput input) {
    input.use |= c->pending.use; input.noise |= c->pending.noise;
    input.jump |= c->pending.jump; input.restart |= c->pending.restart;
    c->pending = input;
}
static void consume(DlTickClock *c, DlTickCallback callback, void *context) {
    DlInput input = c->pending;
    c->pending.use = c->pending.noise = c->pending.jump = c->pending.restart = false;
    callback(context, &input, c->tick);
    ++c->tick;
}
void dl_tick_clear_pending(DlTickClock *c) {
    if (!c) return;
    c->accumulator = 0; memset(&c->pending, 0, sizeof(c->pending));
}
void dl_tick_step(DlTickClock *c, DlInput input, DlTickCallback callback, void *context) {
    if (!c || !callback) return;
    queue_input(c, input); consume(c, callback, context);
}
int dl_tick_advance(DlTickClock *c, double seconds, DlInput input,
                    DlTickCallback callback, void *context) {
    if (!c || !callback || !isfinite(seconds) || seconds < 0) return 0;
    queue_input(c, input);
    const double step = 1.0 / DL_TICK_RATE;
    const double cap = step * DL_MAX_FRAME_TICKS;
    if (seconds > cap) { c->dropped_seconds += seconds-cap; seconds=cap; }
    c->accumulator += seconds;
    int count = 0;
    while (c->accumulator + 1e-9 >= step && count < DL_MAX_FRAME_TICKS) {
        c->accumulator -= step;
        if (c->accumulator < 0) c->accumulator = 0;
        consume(c, callback, context); ++count;
    }
    return count;
}
