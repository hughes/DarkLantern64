#include "render_batches.h"
#include "animation.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)

static int topology(const DlMesh *mesh,const DlRenderBatches *packed){
    unsigned next_index=0,next_vertex=0,next_group=0,mixed=0;
    for(unsigned b=0;b<packed->batch_count;++b){
        const DlRenderBatch *batch=&packed->batches[b];
        CHECK(batch->index_offset==next_index&&batch->vertex_offset==next_vertex&&batch->group_offset==next_group);
        CHECK(batch->vertex_count>0&&batch->vertex_count<=70&&batch->vertex_count%2==0);
        CHECK(batch->index_count>0&&batch->index_count%3==0);
        CHECK(batch->group_count>0&&batch->group_count<=32);
        int next_local=0,last_bone=-1;
        for(int g=0;g<batch->group_count;++g){
            DlRenderBatchGroup group=packed->groups[batch->group_offset+g];
            CHECK(group.bone>last_bone&&group.vertex_start==next_local&&group.vertex_start%2==0);
            CHECK(group.vertex_count>0&&group.vertex_count%2==0);
            CHECK(group.vertex_start+group.vertex_count<=batch->vertex_count);
            last_bone=group.bone;next_local+=group.vertex_count;
            for(int v=group.vertex_start;v<group.vertex_start+group.vertex_count;++v){
                DlRenderVertexRef ref=packed->vertices[batch->vertex_offset+v];
                CHECK(ref.source_vertex<mesh->vertex_count&&ref.triangle_index<mesh->index_count/3);
                CHECK((mesh->animation?mesh->animation->vertex_bones[ref.source_vertex]:0)==group.bone);
                /* Every padded or real ref corresponds to its named source face. */
                int start=ref.triangle_index*3;
                CHECK(mesh->indices[start]==ref.source_vertex||mesh->indices[start+1]==ref.source_vertex||
                    mesh->indices[start+2]==ref.source_vertex);
            }
        }
        CHECK(next_local==batch->vertex_count);
        for(int i=0;i<batch->index_count;++i){
            unsigned source_index=batch->index_offset+(unsigned)i;
            uint8_t local=packed->indices[source_index];
            CHECK(local<batch->vertex_count);
            DlRenderVertexRef ref=packed->vertices[batch->vertex_offset+local];
            CHECK(ref.source_vertex==mesh->indices[source_index]);
            if(!mesh->normals)CHECK(ref.triangle_index==source_index/3);
            if(mesh->animation&&i%3==0){
                uint8_t a=mesh->animation->vertex_bones[mesh->indices[source_index]],
                    c=mesh->animation->vertex_bones[mesh->indices[source_index+1]],
                    d=mesh->animation->vertex_bones[mesh->indices[source_index+2]];
                if(a!=c||a!=d)++mixed;
            }
        }
        next_vertex+=batch->vertex_count;next_group+=batch->group_count;next_index+=batch->index_count;
    }
    CHECK(next_vertex==packed->vertex_count&&next_group==packed->group_count&&next_index==packed->index_count);
    CHECK(packed->index_count==(unsigned)mesh->index_count);
    CHECK(packed->allocated_bytes==packed->batch_count*sizeof *packed->batches+
        packed->vertex_count*sizeof *packed->vertices+packed->group_count*sizeof *packed->groups+packed->index_count);
    if(mesh->animation&&mesh->vertex_count>3)CHECK(mixed>0);
    return 0;
}

static int fixtures(void){
    enum {VERTICES=217,TRIANGLES=521};
    DlVec3 vertices[VERTICES]={0};
    DlNormal normals[VERTICES]={0};
    uint8_t bones[VERTICES];
    uint16_t indices[TRIANGLES*3];
    DlAnimationAsset animation={.bone_count=32,.vertex_bones=bones};
    DlMesh mesh={.vertices=vertices,.vertex_count=VERTICES,.indices=indices,.index_count=TRIANGLES*3};
    for(int v=0;v<VERTICES;++v)bones[v]=(uint8_t)(v%32);
    for(int i=0;i<TRIANGLES*3;++i)indices[i]=(uint16_t)((i*37+i/11)%VERTICES);
    for(int skinned=0;skinned<2;++skinned)for(int smooth=0;smooth<2;++smooth){
        mesh.animation=skinned?&animation:NULL;mesh.normals=smooth?normals:NULL;
        DlRenderBatches packed={0},second={0};
        CHECK(dl_render_batches_build(&mesh,&packed));
        CHECK(topology(&mesh,&packed)==0);
        CHECK(dl_render_batches_build(&mesh,&second));
        CHECK(packed.batch_count>1&&packed.batch_count==second.batch_count);
        CHECK(packed.vertex_count==second.vertex_count&&packed.group_count==second.group_count);
        CHECK(memcmp(packed.batches,second.batches,packed.batch_count*sizeof *packed.batches)==0);
        CHECK(memcmp(packed.vertices,second.vertices,packed.vertex_count*sizeof *packed.vertices)==0);
        CHECK(memcmp(packed.indices,second.indices,packed.index_count)==0);
        CHECK(!dl_render_batches_build(&mesh,&packed)); /* Explicit lifetime, no leak on accidental rebuild. */
        dl_render_batches_free(&packed);dl_render_batches_free(&second);
        CHECK(packed.batch_count==0&&!packed.batches&&!packed.vertices&&!packed.groups&&!packed.indices);
    }
    DlRenderBatches failed={0};
    indices[12]=VERTICES;
    CHECK(!dl_render_batches_build(&mesh,&failed)&&!failed.batches);
    indices[12]=0;bones[12]=32;
    CHECK(!dl_render_batches_build(&mesh,&failed)&&!failed.batches);
    mesh.animation=NULL;--mesh.index_count;
    CHECK(!dl_render_batches_build(&mesh,&failed)&&!failed.batches);
    mesh=(DlMesh){0};
    CHECK(dl_render_batches_build(&mesh,&failed)&&failed.batch_count==0);
    dl_render_batches_free(&failed);dl_render_batches_free(NULL);
    return 0;
}

static int repeated_and_degenerate(void){
    /* Many repeated triangles share only one pair load. Split before the
     * batch's uint16 index count overflows, even when vertex capacity is free. */
    DlVec3 vertices[3]={0};DlNormal normals[3]={0};
    uint16_t *indices=malloc(66000*sizeof *indices);CHECK(indices);
    for(int i=0;i<66000;++i)indices[i]=(uint16_t)(i%3);
    DlMesh mesh={.vertices=vertices,.vertex_count=3,.indices=indices,.index_count=66000,.normals=normals};
    DlRenderBatches packed={0};
    CHECK(dl_render_batches_build(&mesh,&packed)&&packed.batch_count==2);
    CHECK(packed.batches[0].index_count==65535&&packed.batches[1].index_count==465);
    CHECK(packed.vertex_count==8&&topology(&mesh,&packed)==0);
    dl_render_batches_free(&packed);
    mesh.index_count=3;indices[0]=indices[1]=indices[2]=2;mesh.normals=NULL;
    CHECK(dl_render_batches_build(&mesh,&packed)&&packed.vertex_count==2);
    CHECK(topology(&mesh,&packed)==0);
    dl_render_batches_free(&packed);free(indices);
    return 0;
}

static int same_batches(const DlRenderBatches *a,const DlRenderBatches *b){
    CHECK(a->batch_count==b->batch_count&&a->vertex_count==b->vertex_count&&
        a->group_count==b->group_count&&a->index_count==b->index_count&&a->allocated_bytes==b->allocated_bytes);
    if(a->batch_count)CHECK(memcmp(a->batches,b->batches,a->batch_count*sizeof *a->batches)==0);
    if(a->vertex_count)CHECK(memcmp(a->vertices,b->vertices,a->vertex_count*sizeof *a->vertices)==0);
    if(a->group_count)CHECK(memcmp(a->groups,b->groups,a->group_count*sizeof *a->groups)==0);
    if(a->index_count)CHECK(memcmp(a->indices,b->indices,a->index_count)==0);
    return 0;
}

static int sequential_static(void){
    enum {MAX_VERTICES=65535};
    DlVec3 *vertices=calloc(MAX_VERTICES,sizeof *vertices);DlNormal *normals=calloc(MAX_VERTICES,sizeof *normals);
    DlVec2 *uvs=calloc(MAX_VERTICES,sizeof *uvs);uint8_t *bones=calloc(MAX_VERTICES,sizeof *bones);
    uint16_t *indices=malloc(MAX_VERTICES*sizeof *indices);CHECK(vertices&&normals&&uvs&&bones&&indices);
    for(unsigned i=0;i<MAX_VERTICES;++i)indices[i]=(uint16_t)i;
    DlAnimationAsset single_bone={.bone_count=1,.vertex_bones=bones};
    const int triangles[]={0,1,2,21,22,23,24,25,45,46,47,48,91,92,93,511,1440,21844,21845};
    for(unsigned c=0;c<sizeof triangles/sizeof triangles[0];++c)
    for(int smooth=0;smooth<2;++smooth)for(int textured=0;textured<2;++textured){
        int count=triangles[c]*3;
        DlMesh mesh={.vertices=vertices,.vertex_count=count,.indices=indices,.index_count=count,
            .normals=smooth?normals:NULL,.uvs=textured?uvs:NULL};
        DlRenderBatches packed={0},reference={0};
        CHECK(dl_render_batches_build(&mesh,&packed));CHECK(topology(&mesh,&packed)==0);
        /* A one-bone asset selects the unchanged general algorithm, while its
         * group 0 layout is exactly the nonanimated legacy result. */
        mesh.animation=&single_bone;CHECK(dl_render_batches_build(&mesh,&reference));
        CHECK(same_batches(&packed,&reference)==0);
        if(triangles[c]>=23){CHECK(packed.batches[0].index_count==69&&packed.batches[0].vertex_count==70);
            CHECK(memcmp(&packed.vertices[68],&packed.vertices[69],sizeof *packed.vertices)==0);}
        dl_render_batches_free(&packed);dl_render_batches_free(&reference);
    }
    /* Unreferenced trailing vertices do not change the sequential topology. */
    DlMesh mesh={.vertices=vertices,.vertex_count=90,.indices=indices,.index_count=72};
    DlRenderBatches packed={0},reference={0};CHECK(dl_render_batches_build(&mesh,&packed));
    mesh.animation=&single_bone;CHECK(dl_render_batches_build(&mesh,&reference));CHECK(same_batches(&packed,&reference)==0);
    dl_render_batches_free(&packed);dl_render_batches_free(&reference);
    /* A repeated corner must use the general key/remapping path, with either
     * flat faces or authored normals. It still matches the legacy algorithm. */
    indices[71]=indices[70];mesh.animation=NULL;
    for(int smooth=0;smooth<2;++smooth){mesh.normals=smooth?normals:NULL;
        CHECK(dl_render_batches_build(&mesh,&packed));CHECK(topology(&mesh,&packed)==0);
        mesh.animation=&single_bone;CHECK(dl_render_batches_build(&mesh,&reference));CHECK(same_batches(&packed,&reference)==0);
        dl_render_batches_free(&packed);dl_render_batches_free(&reference);mesh.animation=NULL;
    }
    DlRenderBatches failed={.vertex_count=137,.allocated_bytes=55},before=failed;
    indices[71]=90;CHECK(!dl_render_batches_build(&mesh,&failed)&&memcmp(&failed,&before,sizeof failed)==0);
    indices[71]=71;--mesh.index_count;
    CHECK(!dl_render_batches_build(&mesh,&failed)&&memcmp(&failed,&before,sizeof failed)==0);
    free(vertices);free(normals);free(uvs);free(bones);free(indices);return 0;
}

int main(void){
    CHECK(fixtures()==0);CHECK(repeated_and_degenerate()==0);CHECK(sequential_static()==0);
    puts("render batches: byte-exact sequential static/general layouts, triangle order/connectivity, 32-joint pair padding, flat face isolation, deterministic lifetimes and index limits passed");
    return 0;
}
