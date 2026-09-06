#include "render_shading.h"
#include <stdio.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
static float dot3(DlVec3 a,DlVec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
static DlVec3 matrix_vector(float matrix[3][3],DlVec3 v){
    return (DlVec3){matrix[0][0]*v.x+matrix[0][1]*v.y+matrix[0][2]*v.z,
                   matrix[1][0]*v.x+matrix[1][1]*v.y+matrix[1][2]*v.z,
                   matrix[2][0]*v.x+matrix[2][1]*v.y+matrix[2][2]*v.z};
}
int main(void){
    CHECK(sizeof(DlNormal)==3);
    DlVec3 n=dl_render_normal((DlNormal){0,0,127},(DlVec3){1,1,1},(DlVec3){0,1,0},(DlVec3){1,0,1},false);
    CHECK(fabsf(n.x-1)<.000001f&&fabsf(n.y)<.000001f&&fabsf(n.z)<.000001f);
    /* A transformed normal must stay perpendicular to two independently
     * transformed tangents, including anisotropic scale and a door hinge. */
    DlVec3 scale={2,.25f,3},a={-70,40,0},b={-3600,-6300,6500};
    for(int hinge=0;hinge<2;++hinge)for(int i=0;i<19;++i){
        float x=.13f*i,y=-.19f*i,z=.07f*i;
        DlVec3 s={sinf(x),sinf(y),sinf(z)},c={cosf(x),cosf(y),cosf(z)};
        float rx[3][3]={{1,0,0},{0,c.x,-s.x},{0,s.x,c.x}};
        float ry[3][3]={{c.y,0,s.y},{0,1,0},{-s.y,0,c.y}};
        float rz[3][3]={{c.z,-s.z,0},{s.z,c.z,0},{0,0,1}};
        float h[3][3]={{0,0,1},{0,1,0},{-1,0,0}};
        float scaling[3][3]={{scale.x,0,0},{0,scale.y,0},{0,0,scale.z}};
        DlVec3 tangents[2]={a,b};
        n=dl_render_normal((DlNormal){40,70,90},scale,s,c,hinge!=0);
        CHECK(fabsf(dot3(n,n)-1)<.000001f);
        for(int j=0;j<2;++j){
            DlVec3 t=matrix_vector(scaling,tangents[j]);
            if(hinge)t=matrix_vector(h,t);
            t=matrix_vector(rz,matrix_vector(ry,matrix_vector(rx,t)));
            CHECK(fabsf(dot3(n,t))/sqrtf(dot3(t,t))<.000001f);
        }
    }
    /* The cue is a bounded color lift, fades out, and repeats without state. */
    CHECK(dl_loot_highlight_lift(0,8,0)==0&&dl_loot_highlight_lift(1,100,0)==0);
    for(int i=0;i<360;++i){
        float t=i*.01f,near=dl_loot_highlight_lift(t,3,0),far=dl_loot_highlight_lift(t,6,0);
        CHECK(near>=.13999f&&near<=.78001f);
        CHECK(fabsf(far-near*.5f)<.000001f);
        CHECK(fabsf(near-dl_loot_highlight_lift(t+3.6f,3,0))<.000001f);
    }
    CHECK(dl_loot_highlight_lift(1.8f,3,0)-dl_loot_highlight_lift(0,3,0)>.639f);
    puts("render shading: packed normal transforms, tangent orthogonality, bounded pulse and distance fade passed");
    return 0;
}
