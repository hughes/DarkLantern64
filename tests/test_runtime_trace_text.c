#include "runtime_trace_text.h"
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static size_t reference(char *out,size_t capacity,uint32_t session,const DlRuntimeRecord *r){
    const uint32_t *d=r->data;const uint64_t *w=r->wide;
    if(r->type==DL_RT_EVENT)return (size_t)snprintf(out,capacity,
        "DL64 rt_event session=%u seq=%"PRIu64" time=%"PRIu64" kind=%s reason=%s job=%"PRIu64" region=%u slot=%u generation=%u value=%u total=%u mask=%u\n",
        session,r->seq,r->time,r->kind,r->reason,r->job,d[0],d[1],d[2],d[3],d[4],d[5]);
    if(r->type==DL_RT_SLOT)return (size_t)snprintf(out,capacity,
        "DL64 rt_slot session=%u seq=%"PRIu64" time=%"PRIu64" snapshot=%"PRIu64" job=%"PRIu64" slot=%u generation=%u region=%u state=%u bytes=%u total=%u pins=%u rsp=%"PRIu64" rdp=%"PRIu64"\n",
        session,r->seq,r->time,w[2],r->job,d[0],d[1],d[2],d[3],d[4],d[5],d[6],w[0],w[1]);
    return (size_t)snprintf(out,capacity,
        "DL64 rt_cost session=%u seq=%"PRIu64" time=%"PRIu64" job=%"PRIu64" stage=%u elapsed=%"PRIu64" cpu=%"PRIu64" audio=%"PRIu64" commit=%"PRIu64" startup=%u samples=%u max_cpu=%u\n",
        session,r->seq,r->time,r->job,d[0],w[0],w[1],w[2],w[3],d[1],d[2],d[3]);
}
int main(void){
    const uint64_t values[]={0,1,UINT32_MAX,(uint64_t)UINT32_MAX+1,UINT64_C(0x8000000000000001),UINT64_MAX};
    const DlRuntimeRecordType types[]={DL_RT_EVENT,DL_RT_SLOT,DL_RT_COST};
    for(size_t t=0;t<3;++t)for(size_t v=0;v<sizeof values/sizeof values[0];++v){
        DlRuntimeRecord record={.type=types[t],.kind="prepare_begin",.reason="resident_set_changed",
            .seq=values[v],.time=values[v],.job=values[v]};
        for(unsigned i=0;i<16;++i)record.data[i]=UINT32_MAX-i;
        for(unsigned i=0;i<4;++i)record.wide[i]=values[v];
        char expected[512],actual[512];
        size_t bytes=reference(expected,sizeof expected,UINT32_MAX,&record);
        assert(bytes<sizeof expected);
        assert(dl_runtime_trace_text(actual,sizeof actual,UINT32_MAX,&record)==bytes);
        assert(!memcmp(expected,actual,bytes));
        /* Test every truncation, not only the maximum-size transport buffer.
         * The caller must receive zero and no byte outside its capacity changes. */
        for(size_t capacity=0;capacity<bytes;++capacity){
            unsigned char guarded[514];memset(guarded,0xa5,sizeof guarded);
            assert(!dl_runtime_trace_text((char *)guarded+1,capacity,UINT32_MAX,&record));
            assert(guarded[0]==0xa5&&guarded[capacity+1]==0xa5&&guarded[513]==0xa5);
        }
        assert(dl_runtime_trace_text(actual,bytes,UINT32_MAX,&record)==bytes);
    }
    DlRuntimeRecord unsupported={.type=DL_RT_MOTION};char output[512];memset(output,'x',sizeof output);
    assert(!dl_runtime_trace_text(output,sizeof output,1,&unsupported)&&output[0]=='x');
    assert(!dl_runtime_trace_text(NULL,sizeof output,1,&unsupported));
    puts("runtime trace text: exact v1 protocol, full u64 tokens/clocks, all truncations and bounded writes passed");
    return 0;
}
