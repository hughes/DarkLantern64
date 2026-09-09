#include "tick.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
typedef struct { int ticks, uses, jumps; float distance; } Result;
static void tick(void *ctx,const DlInput *input,uint64_t index) {
    Result *r=ctx; assert(index==(uint64_t)r->ticks);
    ++r->ticks; r->uses+=input->use; r->jumps+=input->jump;
    r->distance+=input->forward*DL_TICK_SECONDS;
}
int main(void) {
    DlTickClock a={0},b={0}; Result x={0},y={0};
    for(int i=0;i<120;++i)dl_tick_advance(&a,1.0/120,(DlInput){.forward=1,.use=i==0},tick,&x);
    for(int i=0;i<30;++i)dl_tick_advance(&b,1.0/30,(DlInput){.forward=1,.use=i==0},tick,&y);
    assert(x.ticks==60&&y.ticks==60&&x.uses==1&&y.uses==1&&x.distance==y.distance);
    dl_tick_advance(&a,0,(DlInput){.jump=true},tick,&x);
    dl_tick_advance(&a,10,(DlInput){0},tick,&x);
    assert(x.ticks==68&&x.jumps==1&&a.dropped_seconds>9);
    dl_tick_advance(&a,0,(DlInput){.use=true},tick,&x);
    dl_tick_clear_pending(&a);
    dl_tick_step(&a,(DlInput){0},tick,&x);
    assert(x.uses==1&&x.ticks==69);
    assert(dl_tick_advance(&a,NAN,(DlInput){0},tick,&x)==0);
    puts("PASS fixed ticks, frame partitioning, edges, pause and bounded catch-up");
}
