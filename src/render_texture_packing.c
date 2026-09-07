#include "render_texture_packing.h"
#include <math.h>

static bool fail(DlTexturePackError *error,DlTexturePackStatus status,
    uint32_t batch,uint32_t vertex,unsigned axis,double span){
    if(error)*error=(DlTexturePackError){status,batch,vertex,axis,span};
    return false;
}

static bool batch_base(const DlMesh *mesh,const DlRenderBatches *batches,
    uint32_t index,unsigned axis,int dimension,double *base,DlTexturePackError *error){
    const DlRenderBatch *batch=&batches->batches[index];
    double minimum=0,maximum=0;
    for(unsigned v=0;v<batch->vertex_count;++v){
        uint32_t source=batches->vertices[batch->vertex_offset+v].source_vertex;
        if(source>=(uint32_t)mesh->vertex_count)
            return fail(error,DL_TEXTURE_PACK_INVALID_INPUT,index,source,axis,0);
        DlVec2 uv=mesh->uvs[source];double value=axis?uv.v:uv.u;
        if(!isfinite(value))return fail(error,DL_TEXTURE_PACK_NONFINITE_UV,index,source,axis,0);
        if(v==0||value<minimum)minimum=value;
        if(v==0||value>maximum)maximum=value;
    }
    double span=(maximum-minimum)*dimension;
    if(span>2047)return fail(error,DL_TEXTURE_PACK_SPAN_TOO_WIDE,index,UINT32_MAX,axis,span);
    double period=(double)dimension*32;
    *base=0;
    double low=round(minimum*period),high=round(maximum*period);
    if(isfinite(low)&&isfinite(high)&&low>=INT16_MIN&&high<=INT16_MAX)return true;
    /* Signed s16's centre is -0.5, not zero. The integer nearest this target
     * is the best possible whole-repeat rebase for the interval. Validate the
     * rounded endpoints before any narrowing conversion. Double intermediates
     * preserve small differences between large, finite float source UVs. */
    *base=round((minimum+maximum)*.5+.5/period);
    low=round((minimum-*base)*period);high=round((maximum-*base)*period);
    if(!isfinite(low)||!isfinite(high)||low<INT16_MIN||high>INT16_MAX)
        return fail(error,DL_TEXTURE_PACK_NO_REPEAT_BASE,index,UINT32_MAX,axis,span);
    return true;
}

bool dl_render_texture_pack_ex(const DlMesh *mesh,const DlRenderBatches *batches,
    int width,int height,int16_t *out_uv_pairs,DlTexturePackError *error){
    if(error)*error=(DlTexturePackError){DL_TEXTURE_PACK_OK,UINT32_MAX,UINT32_MAX,0,0};
    if(!mesh||!batches||width<=0||height<=0||mesh->vertex_count<0||
        (batches->vertex_count&&(!out_uv_pairs||!mesh->uvs||!batches->vertices||!batches->batches))||
        (batches->batch_count==0)!=(batches->vertex_count==0))
        return fail(error,DL_TEXTURE_PACK_INVALID_INPUT,UINT32_MAX,UINT32_MAX,0,0);
    uint32_t next_vertex=0;
    for(uint32_t b=0;b<batches->batch_count;++b){
        const DlRenderBatch *batch=&batches->batches[b];
        if(batch->vertex_offset!=next_vertex||batch->vertex_count==0||
            batch->vertex_count>batches->vertex_count-next_vertex)
            return fail(error,DL_TEXTURE_PACK_INVALID_INPUT,b,UINT32_MAX,0,0);
        next_vertex+=batch->vertex_count;
        double base;
        if(!batch_base(mesh,batches,b,0,width,&base,error)||
            !batch_base(mesh,batches,b,1,height,&base,error))return false;
    }
    if(next_vertex!=batches->vertex_count)
        return fail(error,DL_TEXTURE_PACK_INVALID_INPUT,UINT32_MAX,UINT32_MAX,0,0);
    /* Recompute the two scalars instead of allocating a per-batch base table. */
    for(uint32_t b=0;b<batches->batch_count;++b){
        const DlRenderBatch *batch=&batches->batches[b];double base[2];
        batch_base(mesh,batches,b,0,width,&base[0],NULL);
        batch_base(mesh,batches,b,1,height,&base[1],NULL);
        for(unsigned v=0;v<batch->vertex_count;++v){
            uint32_t packed=batch->vertex_offset+v;
            DlVec2 uv=mesh->uvs[batches->vertices[packed].source_vertex];
            out_uv_pairs[(size_t)packed*2]=(int16_t)round(((double)uv.u-base[0])*width*32);
            out_uv_pairs[(size_t)packed*2+1]=(int16_t)round(((double)uv.v-base[1])*height*32);
        }
    }
    return true;
}

bool dl_render_texture_pack(const DlMesh *mesh,const DlRenderBatches *batches,
    int width,int height,int16_t *out_uv_pairs){
    return dl_render_texture_pack_ex(mesh,batches,width,height,out_uv_pairs,NULL);
}

const char *dl_render_texture_pack_status_name(DlTexturePackStatus status){
    switch(status){
    case DL_TEXTURE_PACK_OK:return "ok";
    case DL_TEXTURE_PACK_INVALID_INPUT:return "invalid mesh/batch/texture input";
    case DL_TEXTURE_PACK_NONFINITE_UV:return "nonfinite source UV";
    case DL_TEXTURE_PACK_SPAN_TOO_WIDE:return "batch UV span exceeds 2047 texels";
    case DL_TEXTURE_PACK_NO_REPEAT_BASE:return "no integer repeat offset fits signed 10.5 coordinates";
    default:return "unknown texture packing status";
    }
}
