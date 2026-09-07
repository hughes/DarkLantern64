#include "render_texture_packing.h"
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)

static int periodic_interpolation(void){
    enum {COUNT=131,TRIANGLES=173};
    DlVec3 vertices[COUNT]={0};DlNormal normals[COUNT]={0};DlVec2 uv[COUNT];
    uint16_t indices[TRIANGLES*3];
    for(int i=0;i<COUNT;++i)uv[i]=(DlVec2){1024+(i%13)/128.0f,-1024+(i%17)/64.0f};
    for(int i=0;i<TRIANGLES*3;++i)indices[i]=(uint16_t)((i*37+i/7)%COUNT);
    DlMesh mesh={.vertices=vertices,.vertex_count=COUNT,.indices=indices,.index_count=TRIANGLES*3,.uvs=uv};
    for(int smooth=0;smooth<2;++smooth){
        mesh.normals=smooth?normals:NULL;DlRenderBatches batches={0};
        CHECK(dl_render_batches_build(&mesh,&batches)&&batches.batch_count>1);
        int16_t *packed=malloc(batches.vertex_count*2*sizeof *packed);CHECK(packed);
        DlTexturePackError error;
        CHECK(dl_render_texture_pack_ex(&mesh,&batches,32,64,packed,&error));
        CHECK(error.status==DL_TEXTURE_PACK_OK);
        for(unsigned b=0;b<batches.batch_count;++b){
            DlRenderBatch batch=batches.batches[b];
            for(unsigned axis=0;axis<2;++axis){
                double period=(axis?64:32)*32;
                uint16_t first=batches.vertices[batch.vertex_offset].source_vertex;
                double first_uv=axis?uv[first].v:uv[first].u;
                double base=first_uv-packed[batch.vertex_offset*2+axis]/period;
                CHECK(base==round(base));
                for(int v=0;v<batch.vertex_count;++v){
                    unsigned at=batch.vertex_offset+(unsigned)v;
                    uint16_t source=batches.vertices[at].source_vertex;
                    double value=axis?uv[source].v:uv[source].u;
                    CHECK(value-packed[at*2+axis]/period==base);
                    /* Equal source refs, including pair padding, retain UVs. */
                    for(int previous=0;previous<v;++previous){
                        unsigned earlier=batch.vertex_offset+(unsigned)previous;
                        if(batches.vertices[earlier].source_vertex==source)
                            CHECK(packed[earlier*2+axis]==packed[at*2+axis]);
                    }
                }
                for(int i=0;i<batch.index_count;i+=3){
                    const double weights[]={.17,.31,.52};double old=0,decoded=0;
                    for(int corner=0;corner<3;++corner){
                        unsigned at=batch.vertex_offset+batches.indices[batch.index_offset+i+corner];
                        uint16_t source=batches.vertices[at].source_vertex;
                        old+=weights[corner]*(axis?uv[source].v:uv[source].u);
                        decoded+=weights[corner]*packed[at*2+axis]/period;
                    }
                    CHECK(fabs((old-decoded)-base)<.000000001);
                }
            }
        }
        free(packed);dl_render_batches_free(&batches);
    }
    return 0;
}

static int boundaries_and_failures(void){
    DlVec3 vertices[3]={0};DlVec2 uv[3]={{0,0},{1,0},{0,1}};uint16_t indices[]={0,1,2};
    DlMesh mesh={.vertices=vertices,.vertex_count=3,.indices=indices,.index_count=3,.uvs=uv};
    DlRenderBatches batches={0};CHECK(dl_render_batches_build(&mesh,&batches));
    CHECK(batches.vertex_count==4);
    int16_t output[8],saved[8];DlTexturePackError error;
    CHECK(dl_render_texture_pack(&mesh,&batches,32,32,output));
    CHECK(output[0]==0&&output[1]==0&&output[2]==1024&&output[3]==0&&output[4]==0&&output[5]==1024);
    CHECK(output[6]==output[4]&&output[7]==output[5]);
    uv[0].u=-32;uv[1].u=1023.0f/32; /* Exactly 2047 texels, s16 low endpoint. */
    CHECK(dl_render_texture_pack(&mesh,&batches,32,32,output)&&output[0]==INT16_MIN);
    uv[0].u=-1023.0f/32;uv[1].u=32767.0f/1024; /* s16 high endpoint. */
    CHECK(dl_render_texture_pack(&mesh,&batches,32,32,output)&&output[2]==INT16_MAX);
    for(int i=0;i<8;++i)output[i]=saved[i]=12345;
    uv[0].u=-32;uv[1].u=32;
    CHECK(!dl_render_texture_pack_ex(&mesh,&batches,32,32,output,&error));
    CHECK(error.status==DL_TEXTURE_PACK_SPAN_TOO_WIDE&&error.batch==0&&error.axis==0&&error.span_texels==2048);
    CHECK(memcmp(output,saved,sizeof output)==0);
    uv[0].u=1.0f/64;uv[1].u=32;uv[2].u=1;
    CHECK(!dl_render_texture_pack_ex(&mesh,&batches,64,32,output,&error));
    CHECK(error.status==DL_TEXTURE_PACK_NO_REPEAT_BASE&&error.span_texels==2047);
    CHECK(memcmp(output,saved,sizeof output)==0);
    uv[0].u=NAN;
    CHECK(!dl_render_texture_pack_ex(&mesh,&batches,32,32,output,&error));
    CHECK(error.status==DL_TEXTURE_PACK_NONFINITE_UV&&error.source_vertex==0&&error.axis==0);
    uv[0].u=0;uv[1].u=1;uv[2].u=0;uv[2].v=INFINITY;
    CHECK(!dl_render_texture_pack_ex(&mesh,&batches,32,32,output,&error));
    CHECK(error.status==DL_TEXTURE_PACK_NONFINITE_UV&&error.source_vertex==2&&error.axis==1);
    CHECK(memcmp(output,saved,sizeof output)==0);
    uv[2].v=1;batches.vertices[2].source_vertex=3;
    CHECK(!dl_render_texture_pack_ex(&mesh,&batches,32,32,output,&error));
    CHECK(error.status==DL_TEXTURE_PACK_INVALID_INPUT&&error.source_vertex==3);
    batches.vertices[2].source_vertex=2;
    CHECK(!dl_render_texture_pack(&mesh,&batches,0,32,output));
    CHECK(!dl_render_texture_pack(&mesh,&batches,32,-1,output));
    CHECK(!dl_render_texture_pack(&mesh,&batches,32,32,NULL));
    for(int i=0;i<3;++i)uv[i]=(DlVec2){FLT_MAX,-FLT_MAX};
    CHECK(dl_render_texture_pack(&mesh,&batches,32,32,output));
    for(int i=0;i<8;++i)CHECK(output[i]==0);
    CHECK(strcmp(dl_render_texture_pack_status_name(DL_TEXTURE_PACK_SPAN_TOO_WIDE),"batch UV span exceeds 2047 texels")==0);
    dl_render_batches_free(&batches);
    mesh=(DlMesh){0};CHECK(dl_render_texture_pack(&mesh,&batches,32,32,NULL));
    return 0;
}

int main(void){
    CHECK(periodic_interpolation()==0);CHECK(boundaries_and_failures()==0);
    puts("texture packing: large signed offsets, periodic interpolation across batches, unchanged atlas UVs, padding, s16 endpoints and transactional rejection passed");
    return 0;
}
