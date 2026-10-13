#include "runtime_trace_text.h"

typedef struct { char *output; size_t capacity, used; bool valid; } Writer;
static void text(Writer *writer,const char *value){
    while(*value){
        if(writer->used==writer->capacity){writer->valid=false;return;}
        writer->output[writer->used++]=*value++;
    }
}
static void number(Writer *writer,uint64_t value){
    char digits[21],*at=digits+20;*at='\0';
    /* Frequent clocks/serials fit u32. Constant u32 division avoids printf's
     * general full-width conversion path on VR4300. Keep every u64 digit for
     * candidate tokens and extended clocks instead of narrowing them. */
    if(value<=UINT32_MAX){
        uint32_t small=(uint32_t)value;
        do{*--at=(char)('0'+small%10u);small/=10u;}while(small);
    }else{
        do{*--at=(char)('0'+value%10u);value/=10u;}while(value);
    }
    text(writer,at);
}
static void field(Writer *writer,const char *key,uint64_t value){
    text(writer," ");text(writer,key);text(writer,"=");number(writer,value);
}
static void label(Writer *writer,const char *key,const char *value){
    text(writer," ");text(writer,key);text(writer,"=");text(writer,value);
}
size_t dl_runtime_trace_text(char *output,size_t capacity,uint32_t session,const DlRuntimeRecord *r){
    if(!output||!r||!capacity||
        (r->type!=DL_RT_EVENT&&r->type!=DL_RT_SLOT&&r->type!=DL_RT_COST)||
        (r->type==DL_RT_EVENT&&(!r->kind||!r->reason)))return 0;
    Writer writer={output,capacity,0,true};
    text(&writer,r->type==DL_RT_EVENT?"DL64 rt_event":r->type==DL_RT_SLOT?"DL64 rt_slot":"DL64 rt_cost");
    field(&writer,"session",session);field(&writer,"seq",r->seq);field(&writer,"time",r->time);
    if(r->type==DL_RT_EVENT){
        label(&writer,"kind",r->kind);label(&writer,"reason",r->reason);field(&writer,"job",r->job);
        const char *keys[]={"region","slot","generation","value","total","mask"};
        for(unsigned i=0;i<6;++i)field(&writer,keys[i],r->data[i]);
    }else if(r->type==DL_RT_SLOT){
        field(&writer,"snapshot",r->wide[2]);field(&writer,"job",r->job);
        const char *keys[]={"slot","generation","region","state","bytes","total","pins"};
        for(unsigned i=0;i<7;++i)field(&writer,keys[i],r->data[i]);
        field(&writer,"rsp",r->wide[0]);field(&writer,"rdp",r->wide[1]);
    }else{
        field(&writer,"job",r->job);field(&writer,"stage",r->data[0]);
        const char *keys[]={"elapsed","cpu","audio","commit"};
        for(unsigned i=0;i<4;++i)field(&writer,keys[i],r->wide[i]);
        field(&writer,"startup",r->data[1]);field(&writer,"samples",r->data[2]);field(&writer,"max_cpu",r->data[3]);
    }
    text(&writer,"\n");return writer.valid?writer.used:0;
}
