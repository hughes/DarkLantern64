#include "render_batches.h"
#include "animation.h"
#include <stdlib.h>
#include <string.h>

typedef struct {
    DlRenderVertexRef refs[DL_RENDER_BATCH_VERTICES];
    uint8_t bones[DL_RENDER_BATCH_VERTICES];
    uint8_t bone_counts[DL_ANIMATION_MAX_BONES];
    int count,first_triangle,triangle_count;
} BatchScratch;

static bool same_key(DlRenderVertexRef a,DlRenderVertexRef b,bool smooth){
    return a.source_vertex==b.source_vertex&&(smooth||a.triangle_index==b.triangle_index);
}

static void flush(const DlMesh *mesh,const BatchScratch *scratch,DlRenderBatches *result,bool write){
    uint8_t remap[DL_RENDER_BATCH_VERTICES];
    int vertex_count=0,group_count=0;
    for(int bone=0;bone<DL_ANIMATION_MAX_BONES;++bone){
        int count=scratch->bone_counts[bone];
        if(!count)continue;
        if(write){
            result->groups[result->group_count+group_count]=(DlRenderBatchGroup){
                (uint8_t)bone,(uint8_t)vertex_count,(uint8_t)(count+(count&1))};
            int local=vertex_count;
            for(int v=0;v<scratch->count;++v){
                if(scratch->bones[v]!=bone)continue;
                result->vertices[result->vertex_count+local]=scratch->refs[v];
                remap[v]=(uint8_t)local++;
            }
            if(count&1)result->vertices[result->vertex_count+local]=
                result->vertices[result->vertex_count+local-1];
        }
        vertex_count+=count+(count&1);
        ++group_count;
    }
    int index_count=scratch->triangle_count*3;
    if(write){
        result->batches[result->batch_count]=(DlRenderBatch){
            result->vertex_count,result->group_count,result->index_count,
            (uint16_t)index_count,(uint8_t)vertex_count,(uint8_t)group_count};
        int begin=scratch->first_triangle*3;
        for(int i=0;i<index_count;++i){
            DlRenderVertexRef ref={mesh->indices[begin+i],(uint16_t)((begin+i)/3)};
            for(int v=0;v<scratch->count;++v){
                if(!same_key(ref,scratch->refs[v],mesh->normals!=NULL))continue;
                result->indices[result->index_count+i]=remap[v];
                break;
            }
        }
    }
    ++result->batch_count;
    result->vertex_count+=(uint32_t)vertex_count;
    result->group_count+=(uint32_t)group_count;
    result->index_count+=(uint32_t)index_count;
}

static void pack(const DlMesh *mesh,DlRenderBatches *result,bool write){
    BatchScratch scratch={0};
    bool smooth=mesh->normals!=NULL;
    const uint8_t *bones=mesh->animation?mesh->animation->vertex_bones:NULL;
    for(int triangle=0;triangle<mesh->index_count/3;){
        DlRenderVertexRef additions[3];
        uint8_t added_bones[3],counts[DL_ANIMATION_MAX_BONES];
        memcpy(counts,scratch.bone_counts,sizeof counts);
        int added_count=0;
        for(int corner=0;corner<3;++corner){
            DlRenderVertexRef ref={mesh->indices[triangle*3+corner],(uint16_t)triangle};
            bool found=false;
            for(int v=0;v<scratch.count&&!found;++v)found=same_key(ref,scratch.refs[v],smooth);
            for(int v=0;v<added_count&&!found;++v)found=same_key(ref,additions[v],smooth);
            if(found)continue;
            uint8_t bone=bones?bones[ref.source_vertex]:0;
            additions[added_count]=ref;added_bones[added_count++]=bone;
            ++counts[bone];
        }
        int packed_count=0;
        for(int b=0;b<DL_ANIMATION_MAX_BONES;++b)packed_count+=counts[b]+(counts[b]&1);
        if(packed_count>DL_RENDER_BATCH_VERTICES||scratch.triangle_count==UINT16_MAX/3){
            flush(mesh,&scratch,result,write);
            memset(&scratch,0,sizeof scratch);
            continue; /* Retry this triangle with an empty cache. */
        }
        if(!scratch.triangle_count)scratch.first_triangle=triangle;
        for(int v=0;v<added_count;++v){
            scratch.refs[scratch.count]=additions[v];
            scratch.bones[scratch.count++]=added_bones[v];
        }
        memcpy(scratch.bone_counts,counts,sizeof counts);
        ++scratch.triangle_count;++triangle;
    }
    if(scratch.triangle_count)flush(mesh,&scratch,result,write);
}

void dl_render_batches_free(DlRenderBatches *batches){
    if(!batches)return;
    free(batches->batches);free(batches->vertices);
    free(batches->groups);free(batches->indices);
    memset(batches,0,sizeof *batches);
}

bool dl_render_batches_build(const DlMesh *mesh,DlRenderBatches *out){
    if(!mesh||!out||out->batches||out->vertices||out->groups||out->indices||
       mesh->vertex_count<0||mesh->vertex_count>UINT16_MAX||
       mesh->index_count<0||mesh->index_count%3||mesh->index_count/3>UINT16_MAX||
       (mesh->vertex_count&&!mesh->vertices)||(mesh->index_count&&!mesh->indices))return false;
    const uint8_t *bones=mesh->animation?mesh->animation->vertex_bones:NULL;
    if(mesh->animation){
        if(!bones||!mesh->animation->bone_count||mesh->animation->bone_count>DL_ANIMATION_MAX_BONES)return false;
        for(int v=0;v<mesh->vertex_count;++v)if(bones[v]>=mesh->animation->bone_count)return false;
    }
    for(int i=0;i<mesh->index_count;++i)if(mesh->indices[i]>=mesh->vertex_count)return false;
    DlRenderBatches counts={0};
    pack(mesh,&counts,false);
    DlRenderBatches result={0};
    if(counts.batch_count){
        result.batches=malloc(sizeof *result.batches*counts.batch_count);
        result.vertices=malloc(sizeof *result.vertices*counts.vertex_count);
        result.groups=malloc(sizeof *result.groups*counts.group_count);
        result.indices=malloc(sizeof *result.indices*counts.index_count);
        if(!result.batches||!result.vertices||!result.groups||!result.indices){
            dl_render_batches_free(&result);return false;
        }
        result.allocated_bytes=sizeof *result.batches*counts.batch_count+
            sizeof *result.vertices*counts.vertex_count+
            sizeof *result.groups*counts.group_count+
            sizeof *result.indices*counts.index_count;
        pack(mesh,&result,true);
    }
    *out=result;
    return true;
}
