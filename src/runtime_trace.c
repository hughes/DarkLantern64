#include "runtime_trace.h"
#include <string.h>

void dl_runtime_trace_init(DlRuntimeTrace *t,uint32_t session,DlRuntimeClock clock,void *context){
    memset(t,0,sizeof *t);t->session=session;t->clock=clock;t->context=context;
    if(clock)t->last_tick=clock(context);
}
uint64_t dl_runtime_trace_time(DlRuntimeTrace *t){
    if(t->clock){uint32_t tick=t->clock(t->context);t->now+=(uint32_t)(tick-t->last_tick);t->last_tick=tick;}
    return t->now;
}
uint64_t dl_runtime_region_job(uint32_t slot,uint32_t generation){
    /* Full slot/generation pair, disjoint from high-bit candidate IDs. */
    return ((uint64_t)generation<<32)|(slot+1u);
}
uint64_t dl_runtime_trace_push(DlRuntimeTrace *t,DlRuntimeRecord record){
    if(!t||t->finished)return 0;
    uint32_t start=t->clock?t->clock(t->context):0;
    record.seq=++t->sequence;record.time=dl_runtime_trace_time(t);
    if(t->count==DL_RUNTIME_TRACE_CAPACITY)++t->dropped;
    else{t->records[(t->head+t->count)%DL_RUNTIME_TRACE_CAPACITY]=record;++t->count;}
    if(t->clock)t->trace_ticks+=(uint32_t)(t->clock(t->context)-start);
    return record.seq;
}
void dl_runtime_trace_event(DlRuntimeTrace *t,const char *kind,const char *reason,
    uint64_t job,uint32_t region,uint32_t slot,uint32_t generation,
    uint32_t value,uint32_t total,uint32_t mask){
    if(!t)return;
    DlRuntimeRecord r={.type=DL_RT_EVENT,.kind=kind,.reason=reason,.job=job};
    r.data[0]=region;r.data[1]=slot;r.data[2]=generation;r.data[3]=value;r.data[4]=total;r.data[5]=mask;
    dl_runtime_trace_push(t,r);
}
void dl_runtime_trace_flush_limit(DlRuntimeTrace *t,DlRuntimeSink sink,void *context,size_t limit){
    if(!t||!sink)return;
    uint32_t start=t->clock?t->clock(t->context):0;
    size_t count=t->count<limit?t->count:limit;
    for(size_t i=0;i<count;++i){sink(context,&t->records[(t->head+i)%DL_RUNTIME_TRACE_CAPACITY]);++t->emitted;}
    t->head=(t->head+count)%DL_RUNTIME_TRACE_CAPACITY;
    t->count-=count;
    if(t->clock)t->trace_ticks+=(uint32_t)(t->clock(t->context)-start);
}
void dl_runtime_trace_flush(DlRuntimeTrace *t,DlRuntimeSink sink,void *context){
    dl_runtime_trace_flush_limit(t,sink,context,SIZE_MAX);
}
