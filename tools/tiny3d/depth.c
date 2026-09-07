/* Bounded correctness fixture, deliberately synchronous and not a benchmark.
 * Generated input retains the courtyard surfaces' source triangles/transforms. */
#include <libdragon.h>
#include <t3d/t3d.h>
#include <t3d/rsp/rsp_tiny3d.h>
#include <math.h>
#include <string.h>

typedef struct { float x,y,z; } Vec;
typedef struct { unsigned count,index_count;const Vec *vertices;const uint8_t *indices;float world[12]; } Mesh;
typedef struct { Vec eye;unsigned distance,angle,jitter; } View;
#include "fixture.h"
#ifndef DL_DEPTH_PROBE_VIEW
#define DL_DEPTH_PROBE_VIEW 33
#endif
enum { W=320,H=240,TOP=27,BOTTOM=192,PIXELS=W*H };
static const float focal=164,far_clip=64;
static Vec eye,right,up,forward;
static float near_clip;
static uint8_t expected[PIXELS];
static uint16_t worst[6][PIXELS];
static unsigned worst_lost[6],worst_view[6],worst_order[6];
static T3DMat4FP *matrices;
static T3DVertPacked *packed[2];
static T3DViewport viewport;
#ifdef DL_DEPTH_PROBE
static bool probing;
static uint8_t *vertex_cache[2];
static T3DMat4FP *mvp_cache;
typedef struct { int16_t xy[2],z,flags;uint8_t rgba[4];int16_t uv[2],clip_int[4];uint16_t clip_frac[4];int16_t inv_int;uint16_t inv_frac; } CachedVertex;
_Static_assert(sizeof(CachedVertex)==36,"Tiny3D cache layout");
#endif
static Vec add(Vec a,Vec b){return (Vec){a.x+b.x,a.y+b.y,a.z+b.z};}
static Vec sub(Vec a,Vec b){return (Vec){a.x-b.x,a.y-b.y,a.z-b.z};}
static Vec mul(Vec a,float b){return (Vec){a.x*b,a.y*b,a.z*b};}
static float dot(Vec a,Vec b){return a.x*b.x+a.y*b.y+a.z*b.z;}
static Vec cross(Vec a,Vec b){return (Vec){a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
static Vec norm(Vec v){return mul(v,1/sqrtf(dot(v,v)));}
static Vec world_point(const Mesh *mesh,Vec p){const float *m=mesh->world;return (Vec){m[0]*p.x+m[1]*p.y+m[2]*p.z+m[3],m[4]*p.x+m[5]*p.y+m[6]*p.z+m[7],m[8]*p.x+m[9]*p.y+m[10]*p.z+m[11]};}
static Vec view_point(Vec p){Vec v=sub(p,eye);return (Vec){dot(v,right),dot(v,up),dot(v,forward)};}
static void camera(const View *v){
    eye=v->eye;forward=norm(sub(window_center,eye));right=norm(cross(forward,(Vec){0,1,0}));up=cross(right,forward);
    t3d_viewport_set_projection(&viewport,2*atanf((BOTTOM-TOP)*.5f/focal),near_clip*64,far_clip*64);
    t3d_mat4_identity(&viewport.matCamera);
    const Vec axes[]={right,up,mul(forward,-1)};
    for(unsigned model=0;model<2;++model){
        T3DMat4 matrix={0};const float *m=meshes[model].world;
        Vec relative=sub((Vec){m[3],m[7],m[11]},eye);
        for(unsigned row=0;row<3;++row){
            for(unsigned col=0;col<3;++col)
                matrix.m[col][row]=(axes[row].x*m[col]+axes[row].y*m[4+col]+axes[row].z*m[8+col])*(64.0f/1024.0f);
            matrix.m[3][row]=dot(axes[row],relative)*64;
        }
        matrix.m[3][3]=1;t3d_mat4_to_fixed(&matrices[model],&matrix);
    }
}
static float plane(Vec v,unsigned p){
    switch(p){case 0:return v.z-near_clip;case 1:return far_clip-v.z;
    case 2:return v.x+v.z*(W*.5f/focal);case 3:return v.z*(W*.5f/focal)-v.x;
    case 4:return v.z*((BOTTOM-TOP)*.5f/focal)-v.y;default:return v.y+v.z*((BOTTOM-TOP)*.5f/focal);}
}
static void triangle(Vec a,Vec b,Vec c,unsigned green){
    Vec storage[2][16],*in=storage[0],*out=storage[1];in[0]=a;in[1]=b;in[2]=c;unsigned count=3;
    for(unsigned p=0;p<6&&count;++p){
        unsigned next=0;Vec previous=in[count-1];float pd=plane(previous,p);
        for(unsigned n=0;n<count;++n){Vec current=in[n];float cd=plane(current,p);
            if((pd>=0)!=(cd>=0)){assert(next<16);out[next++]=add(previous,mul(sub(current,previous),pd/(pd-cd)));}
            if(cd>=0){assert(next<16);out[next++]=current;}previous=current;pd=cd;
        }count=next;Vec *swap=in;in=out;out=swap;
    }
    float vertices[16][7];
    for(unsigned n=0;n<count;++n){float inv=1/in[n].z;float *v=vertices[n];
        v[0]=W*.5f+in[n].x*focal*inv;v[1]=(TOP+BOTTOM)*.5f-in[n].y*focal*inv;
        v[2]=fminf(.999999f,fmaxf(0,far_clip/(far_clip-near_clip)*(1-near_clip*inv)));
#ifdef DL_DEPTH_QUANTIZED_CPU
        v[2]=floorf(v[2]*32767)/32767;
#endif
        v[3]=green?0:1;v[4]=green?1:0;v[5]=0;v[6]=1;
    }
    for(unsigned n=1;n+1<count;++n)rdpq_triangle(&TRIFMT_ZBUF_SHADE,vertices[0],vertices[n],vertices[n+1]);
}
static void draw(unsigned model,unsigned backend){
    const Mesh *mesh=&meshes[model];
    if(backend){
        t3d_matrix_set(&matrices[model],false);t3d_vert_load(packed[model],0,mesh->count);
#ifdef DL_DEPTH_PROBE
        if(probing){rspq_dma_to_rdram(vertex_cache[model],RSP_T3D_TRI_BUFFER&0xfff,mesh->count*36,false);rspq_dma_to_rdram(&mvp_cache[model],RSP_T3D_MATRIX_MVP&0xfff,64,false);}
#endif
        for(unsigned n=0;n<mesh->index_count;n+=3)t3d_tri_draw(mesh->indices[n],mesh->indices[n+1],mesh->indices[n+2]);
        t3d_tri_sync();
    }else for(unsigned n=0;n<mesh->index_count;n+=3)triangle(
        view_point(world_point(mesh,mesh->vertices[mesh->indices[n]])),
        view_point(world_point(mesh,mesh->vertices[mesh->indices[n+1]])),
        view_point(world_point(mesh,mesh->vertices[mesh->indices[n+2]])),model);
}
static bool green(uint16_t p){return (p>>11)<4&&((p>>6)&31)>27&&((p>>1)&31)<4;}
static void capture(unsigned id){
    debugf("DL64 capture_begin id=%u width=320 height=240 format=rgba5551\n",id+1);
    const char *hex="0123456789abcdef";char row[1281];
    for(unsigned y=0;y<H;++y){for(unsigned x=0;x<W;++x){uint16_t p=worst[id][y*W+x];for(unsigned d=0;d<4;++d)row[x*4+d]=hex[(p>>(12-d*4))&15];}row[1280]=0;debugf("DL64 capture_row id=%u y=%u data=%s\n",id+1,y,row);}
    debugf("DL64 capture_end id=%u\n",id+1);
}
int main(void){
    debug_init_isviewer();display_init(RESOLUTION_320x240,DEPTH_16_BPP,3,GAMMA_NONE,FILTERS_RESAMPLE);
    rdpq_init();t3d_init((T3DInitParams){0});surface_t depth=surface_alloc(FMT_RGBA16,W,H);
    matrices=malloc_uncached(2*sizeof(T3DMat4FP));assert(matrices);
#ifdef DL_DEPTH_PROBE
    mvp_cache=malloc_uncached(2*sizeof(T3DMat4FP));assert(mvp_cache);
    for(unsigned n=0;n<2;++n){vertex_cache[n]=malloc_uncached(70*36+8);assert(vertex_cache[n]);}
#endif
    viewport=t3d_viewport_create();t3d_viewport_set_area(&viewport,0,TOP,W,BOTTOM-TOP);
    for(unsigned model=0;model<2;++model){const Mesh *mesh=&meshes[model];assert(!(mesh->count&1)&&mesh->count<=70);
        packed[model]=malloc_uncached(mesh->count/2*sizeof(T3DVertPacked));assert(packed[model]);memset(packed[model],0,mesh->count/2*sizeof(T3DVertPacked));
        for(unsigned n=0;n<mesh->count;++n){T3DVertPacked *pair=&packed[model][n/2];int16_t *pos=n&1?pair->posB:pair->posA;
            pos[0]=(int16_t)roundf(mesh->vertices[n].x*1024);pos[1]=(int16_t)roundf(mesh->vertices[n].y*1024);pos[2]=(int16_t)roundf(mesh->vertices[n].z*1024);
            if(n&1)pair->rgbaB=model?0x00ff00ff:0xff0000ff;else pair->rgbaA=model?0x00ff00ff:0xff0000ff;
        }
    }
    debugf("DL64 depth_study boot views=%u wall_triangles=%u window_triangles=%u gap_m=0.0475\n",(unsigned)(sizeof(views)/sizeof(*views)),meshes[0].index_count/3,meshes[1].index_count/3);
    const float clips[]={.12f,.24f,.4f};const uint8_t ambient[]={255,255,255,255};
    for(unsigned backend=0;backend<2;++backend)for(unsigned clip=0;clip<3;++clip){
        near_clip=clips[clip];unsigned slot=backend*3+clip;
        for(unsigned view=0;view<sizeof(views)/sizeof(*views);++view){
#ifdef DL_DEPTH_SINGLE_VIEW
            if(view!=DL_DEPTH_PROBE_VIEW)continue;
#endif
            rspq_wait();camera(&views[view]);unsigned area=0;
#ifdef DL_DEPTH_PROBE
            probing=backend==1&&clip==0&&view==DL_DEPTH_PROBE_VIEW;
#endif
            for(unsigned order=0;order<3;++order){
                surface_t *screen=display_get();rdpq_attach(screen,&depth);
                if(backend){t3d_frame_start();t3d_viewport_attach(&viewport);t3d_light_set_ambient(ambient);t3d_light_set_count(0);t3d_state_set_drawflags(T3D_FLAG_SHADED|T3D_FLAG_DEPTH);}
                else{rdpq_set_mode_standard();rdpq_mode_zbuf(true,true);}
                rdpq_mode_combiner(RDPQ_COMBINER_SHADE);rdpq_mode_antialias(AA_NONE);rdpq_mode_dithering(DITHER_NONE_NONE);
                rdpq_set_scissor(0,TOP,W,BOTTOM);rdpq_clear(RGBA32(0,0,0,255));rdpq_clear_z(0xffff);
                if(order==1)draw(0,backend);
                draw(1,backend);
                if(order==2)draw(0,backend);
                rdpq_detach_show();rspq_wait();const uint16_t *pixels=UncachedAddr(screen->buffer);unsigned lost=0;
#ifdef DL_DEPTH_PROBE
                if(probing&&order==1)for(unsigned model=0;model<2;++model)for(unsigned n=0;n<meshes[model].count;++n){
                    CachedVertex *cached=&((CachedVertex*)vertex_cache[model])[n];Vec world=world_point(&meshes[model],meshes[model].vertices[n]);Vec p=view_point(world);
                    double cz=cached->clip_int[2]+cached->clip_frac[2]/65536.0,cw=cached->clip_int[3]+cached->clip_frac[3]/65536.0;
                    double inverse=cached->inv_int+cached->inv_frac/65536.0;
                    double exact=(far_clip/(far_clip-near_clip)*(1-2*near_clip/p.z))*.5+.5;
                    debugf("DL64 depth_vertex model=%u vertex=%u depth=%.8f exact_z=%.8f cache_z=%d cache_x=%d cache_y=%d clip_z=%.8f clip_w=%.8f inv_w=%.8f\n",model,n,(double)p.z,exact*32767,cached->z,cached->xy[0],cached->xy[1],cz,cw,inverse);
                }
#endif
                for(unsigned y=TOP;y<BOTTOM;++y)for(unsigned x=0;x<W;++x){unsigned at=y*W+x;bool visible=green(pixels[at]);
                    if(!order){expected[at]=visible;area+=visible;}else lost+=expected[at]&&!visible;
                }
                if(order){
                    debugf("DL64 depth_case backend=%u near_mm=%u view=%u distance=%u angle=%u jitter=%u order=%u area=%u lost=%u\n",backend,(unsigned)roundf(near_clip*1000),view,views[view].distance,views[view].angle,views[view].jitter,order,area,lost);
                    if(lost>worst_lost[slot]||(!view&&order==1)||(worst_lost[slot]==0&&view==DL_DEPTH_PROBE_VIEW&&order==1)){worst_lost[slot]=lost;worst_view[slot]=view;worst_order[slot]=order;memcpy(worst[slot],pixels,sizeof(worst[slot]));}
                }
            }
        }
    }
    for(unsigned n=0;n<6;++n){debugf("DL64 depth_worst id=%u view=%u order=%u lost=%u\n",n+1,worst_view[n],worst_order[n],worst_lost[n]);capture(n);}
    debugf("DL64 capture_complete views=6\nDL64 depth_study complete\n");
    for(;;)wait_ms(1000);
}
