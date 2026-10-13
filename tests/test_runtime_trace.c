/* Reuse the independent package fixture and exercise real manager boundaries. */
#define main world_stream_fixture_main
#include "test_world_stream.c"
#undef main
#include "runtime_trace.h"

static uint32_t clock_tick;
static uint32_t fake_clock(void *context){(void)context;return clock_tick;}
static DlRuntimeRecord captured[DL_RUNTIME_TRACE_CAPACITY*2];
static size_t captured_count;
static void capture(void *context,const DlRuntimeRecord *record){
    (void)context;if(captured_count<sizeof captured/sizeof captured[0])captured[captured_count++]=*record;
}
static int trace_queue(void){
    DlRuntimeTrace trace;clock_tick=UINT32_MAX-5;
    dl_runtime_trace_init(&trace,3,fake_clock,NULL);
    clock_tick=4;CHECK(dl_runtime_trace_time(&trace)==10);
    dl_runtime_trace_event(&trace,"demand","reversal",0,0,0,0,0,0,2);
    for(unsigned i=0;i<DL_RUNTIME_TRACE_CAPACITY+3;++i)
        dl_runtime_trace_event(&trace,"read_progress","partial",1,1,0,1,i,100,0);
    CHECK(trace.count==DL_RUNTIME_TRACE_CAPACITY&&trace.dropped==4);
    CHECK(trace.sequence==DL_RUNTIME_TRACE_CAPACITY+4);
    captured_count=0;dl_runtime_trace_flush_limit(&trace,capture,NULL,8);
    CHECK(captured_count==8&&trace.count==DL_RUNTIME_TRACE_CAPACITY-8);
    CHECK(trace.emitted==8);
    dl_runtime_trace_flush(&trace,capture,NULL);
    CHECK(trace.emitted==DL_RUNTIME_TRACE_CAPACITY&&captured_count==DL_RUNTIME_TRACE_CAPACITY);
    CHECK(captured[0].time==10&&captured[0].seq==1);
    dl_runtime_trace_event(&trace,"cancel","reversal",1,1,0,1,0,100,0);
    dl_runtime_trace_flush(&trace,capture,NULL);
    CHECK(captured[captured_count-1].seq==DL_RUNTIME_TRACE_CAPACITY+5);
    trace.finished=true;CHECK(dl_runtime_trace_push(&trace,(DlRuntimeRecord){0})==0);
    CHECK(dl_runtime_region_job(0,1)!=dl_runtime_region_job(0,2));
    CHECK(dl_runtime_region_job(0,1)!=dl_runtime_region_job(1,1));
    return 0;
}
static int ring_order(void){
    DlRuntimeTrace trace;dl_runtime_trace_init(&trace,4,fake_clock,NULL);captured_count=0;
    for(unsigned i=0;i<DL_RUNTIME_TRACE_CAPACITY;++i)dl_runtime_trace_event(&trace,"read_progress","slice",1,1,0,1,i,1000,0);
    dl_runtime_trace_flush_limit(&trace,capture,NULL,DL_RUNTIME_TRACE_CAPACITY-1);
    CHECK(trace.count==1);
    for(unsigned i=1;i<DL_RUNTIME_TRACE_CAPACITY;++i)dl_runtime_trace_event(&trace,"read_progress","slice",1,1,0,1,i,1000,0);
    dl_runtime_trace_flush(&trace,capture,NULL);
    CHECK(captured_count==DL_RUNTIME_TRACE_CAPACITY*2-1&&!trace.dropped);
    for(size_t i=0;i<captured_count;++i)CHECK(captured[i].seq==i+1);
    return 0;
}
static int manager_events(void){
    prepare();CHECK(initialize()==0);
    DlRuntimeTrace trace;clock_tick=0;dl_runtime_trace_init(&trace,1,fake_clock,NULL);stream.trace=&trace;
    DlRegionHandle old,new;CHECK(dl_world_stream_request(&stream,1,&old)==DL_STREAM_OK);
    reader.short_read=7;clock_tick=10;CHECK(dl_world_stream_pump(&stream,100)==7);
    clock_tick=20;CHECK(dl_world_stream_cancel(&stream,old)==DL_STREAM_OK);
    CHECK(dl_world_stream_request(&stream,1,&new)==DL_STREAM_OK);
    CHECK(old.generation!=new.generation&&old.slot==new.slot);
    CHECK(dl_world_stream_cancel(&stream,old)==DL_STREAM_HANDLE);
    CHECK(slots[new.slot].state==DL_STREAM_REQUESTED);
    reader.short_read=0;stream.read_slice=4096;
    clock_tick=30;CHECK(dl_world_stream_pump(&stream,4096)==436);
    CHECK(slots[new.slot].state==DL_STREAM_VALIDATING);
    clock_tick=40;CHECK(dl_world_stream_pump(&stream,1)==0);
    const DlMissionChunk *view;CHECK(dl_world_stream_borrow(&stream,new,&view)==DL_STREAM_OK);
    CHECK(dl_world_stream_submit(&stream,new,5,8)==DL_STREAM_OK);
    CHECK(dl_world_stream_retire(&stream,new)==DL_STREAM_OK);
    CHECK(dl_world_stream_complete(&stream,5,7)==DL_STREAM_OK);
    CHECK(dl_world_stream_release(&stream,new)==DL_STREAM_OK);
    CHECK(slots[new.slot].state==DL_STREAM_RETIRING);
    clock_tick=60;CHECK(dl_world_stream_complete(&stream,5,8)==DL_STREAM_OK);
    CHECK(slots[new.slot].state==DL_STREAM_FREE);
    captured_count=0;dl_runtime_trace_flush(&trace,capture,NULL);
    const char *expected[]={"request","read_progress","cancel","release","request","read_progress","read_complete","resident","retire","release"};
    CHECK(captured_count==sizeof expected/sizeof expected[0]);
    for(size_t i=0;i<captured_count;++i)CHECK(!strcmp(captured[i].kind,expected[i]));
    CHECK(captured[1].data[3]==7&&!strcmp(captured[1].reason,"partial"));
    CHECK(captured[0].job==dl_runtime_region_job(old.slot,old.generation));
    CHECK(captured[4].job==dl_runtime_region_job(new.slot,new.generation));
    CHECK(captured[9].time==60&&!trace.dropped);
    return 0;
}
int main(void){
    CHECK(world_stream_fixture_main(0,NULL)==0);
    CHECK(trace_queue()==0);CHECK(ring_order()==0);CHECK(manager_events()==0);
    puts("runtime trace: partial reads, cancellation/reversal, stale generation isolation, fence retirement, timestamp wrap and explicit overflow passed");
    return 0;
}
