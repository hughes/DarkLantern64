#include "render_visibility.h"
#include <stdio.h>
#include <stdint.h>

#define CHECK(c) do { if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return false;} } while(0)

static bool plane_boundaries(void){
    DlRenderFrustum f=dl_render_frustum(.12f,64,160.0f/164,82.5f/164);
    CHECK(dl_render_sphere_visible(&f,(DlVec3){0,0,4},.5f));
    CHECK(!dl_render_sphere_visible(&f,(DlVec3){0,0,-4},.5f));
    CHECK(!dl_render_sphere_visible(&f,(DlVec3){0,0,65},.5f));
    CHECK(dl_render_sphere_visible(&f,(DlVec3){0,0,64.5f},.5f));
    CHECK(dl_render_sphere_visible(&f,(DlVec3){0,0,-.38f},.5f));
    CHECK(!dl_render_sphere_visible(&f,(DlVec3){0,0,-.40f},.5f));
    CHECK(dl_render_sphere_visible(&f,(DlVec3){0,0,0},50));
    for(int sign=-1;sign<=1;sign+=2){
        float edge=10*f.horizontal+.5f*f.horizontal_length;
        CHECK(dl_render_sphere_visible(&f,(DlVec3){sign*edge,0,10},.5f));
        CHECK(!dl_render_sphere_visible(&f,(DlVec3){sign*(edge+.02f),0,10},.5f));
        edge=10*f.vertical+.5f*f.vertical_length;
        CHECK(dl_render_sphere_visible(&f,(DlVec3){0,sign*edge,10},.5f));
        CHECK(!dl_render_sphere_visible(&f,(DlVec3){0,sign*(edge+.02f),10},.5f));
    }
    return true;
}

static uint32_t random_state=0x614bc12fu;
static float random_range(float low,float high){
    random_state=random_state*1664525u+1013904223u;
    return low+(high-low)*(float)(random_state>>8)*(1.0f/16777216.0f);
}
/* Independent oracle: generate an actually visible point by projecting a
 * screen coordinate, then put that point inside a sphere with an arbitrary
 * center offset. No such sphere may be rejected, including near/far edges,
 * large bounds whose centers are outside the view, and corner intersections. */
static bool visible_points_are_never_culled(void){
    DlRenderFrustum f=dl_render_frustum(.12f,64,160.0f/164,82.5f/164);
    for(int i=0;i<12000;++i){
        float z=i%3==0?.12f:i%3==1?64:random_range(.12f,64);
        float x=i%5==0?160:i%5==1?-160:random_range(-160,160);
        float y=i%7==0?82.5f:i%7==1?-82.5f:random_range(-82.5f,82.5f);
        DlVec3 point={x*z/164,y*z/164,z};
        DlVec3 offset={random_range(-40,40),random_range(-40,40),random_range(-40,40)};
        float radius=sqrtf(offset.x*offset.x+offset.y*offset.y+offset.z*offset.z);
        DlVec3 center={point.x+offset.x,point.y+offset.y,point.z+offset.z};
        CHECK(dl_render_sphere_visible(&f,center,radius));
    }
    return true;
}

int main(void){
    if(!plane_boundaries()||!visible_points_are_never_culled())return 1;
    puts("render visibility: six-plane boundary cases and 12000 visible-point containment cases passed");
    return 0;
}
