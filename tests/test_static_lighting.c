#include "static_lighting.h"
#include "render_shading.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#define CHECK(x) do{if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 1;}}while(0)
static DlVec3 plus(DlVec3 a,DlVec3 b){return (DlVec3){a.x+b.x,a.y+b.y,a.z+b.z};}
static DlVec3 minus(DlVec3 a,DlVec3 b){return (DlVec3){a.x-b.x,a.y-b.y,a.z-b.z};}
static DlVec3 times(DlVec3 a,float s){return (DlVec3){a.x*s,a.y*s,a.z*s};}
static float clampf(float x){return x<0?0:x>255?255:x;}
/* Frozen pre-extraction renderer order. Unlike the cooker, this builds the
 * complete transformed vertex array once, as the original renderer does. */
static void legacy(const DlLevel *level,int model_index,bool open,DlStaticLightingColor *out){
    const DlModelInstance *m=&level->models[model_index];const DlMesh *mesh=&level->meshes[m->mesh];
    DlVec3 p[16],lo=mesh->vertices[0],hi=lo;
    for(int i=1;i<mesh->vertex_count;++i){
        DlVec3 v=mesh->vertices[i];
        lo=(DlVec3){fminf(lo.x,v.x),fminf(lo.y,v.y),fminf(lo.z,v.z)};
        hi=(DlVec3){fmaxf(hi.x,v.x),fmaxf(hi.y,v.y),fmaxf(hi.z,v.z)};
    }
    float hx=lo.x*m->scale.x,hz=(lo.z+hi.z)*.5f*m->scale.z;
    DlVec3 s={sinf(m->rotation.x),sinf(m->rotation.y),sinf(m->rotation.z)};
    DlVec3 c={cosf(m->rotation.x),cosf(m->rotation.y),cosf(m->rotation.z)};
    bool hinge=open&&m->role==DL_MODEL_DOOR;
    for(int i=0;i<mesh->vertex_count;++i){
        DlVec3 v=mesh->vertices[i];v.x*=m->scale.x;v.y*=m->scale.y;v.z*=m->scale.z;
        if(hinge){float x=v.x-hx,z=v.z-hz;v.x=hx+z;v.z=hz-x;}
        v=(DlVec3){v.x,c.x*v.y-s.x*v.z,s.x*v.y+c.x*v.z};
        v=(DlVec3){c.y*v.x+s.y*v.z,v.y,-s.y*v.x+c.y*v.z};
        v=(DlVec3){c.z*v.x-s.z*v.y,s.z*v.x+c.z*v.y,v.z};
        p[i]=plus(v,m->position);
    }
    DlGame game={.level=level,.door_open=open};
    for(int t=0;t<mesh->index_count/3;++t){
        DlVec3 a=p[mesh->indices[t*3]],b=p[mesh->indices[t*3+1]],cpoint=p[mesh->indices[t*3+2]];
        DlVec3 u=minus(b,a),v=minus(cpoint,a);
        DlVec3 n={u.y*v.z-u.z*v.y,u.z*v.x-u.x*v.z,u.x*v.y-u.y*v.x};
        float length=sqrtf(n.x*n.x+n.y*n.y+n.z*n.z);
        n=length>.000001f?times(n,1/length):(DlVec3){0,1,0};
        for(int j=0;j<3;++j){
            int vi=mesh->indices[t*3+j];DlVec3 normal=mesh->normals?dl_render_normal(mesh->normals[vi],m->scale,s,c,hinge):n;
            DlVec3 front=dl_surface_light(&game,plus(p[vi],times(normal,.035f)),normal);
            DlVec3 back=m->double_sided?dl_surface_light(&game,plus(p[vi],times(normal,-.035f)),times(normal,-1)):front;
            float values[2][3]={{front.x,front.y,front.z},{back.x,back.y,back.z}};
            for(int side=0;side<2;++side){
                uint8_t *dest=side?out[t].back[j]:out[t].front[j];
                for(int channel=0;channel<3;++channel)dest[channel]=(uint8_t)(int)clampf(m->color[channel]*sqrtf(fmaxf(0,values[side][channel]*level->environment.exposure))+m->emissive[channel]);
                dest[3]=255;
            }
        }
    }
}
int main(void){
    _Static_assert(sizeof(DlStaticLightingColor)==24,"renderer cache ABI");
    DlVec3 vertices[]={{-.7f,0,-.2f},{.8f,0,-.2f},{.8f,1.9f,.3f},{-.7f,1.9f,.3f}};
    uint16_t indices[]={0,1,2,0,2,3};
    DlNormal normals[]={{0,40,-121},{20,30,-120},{-30,80,-95},{0,127,0}};
    DlMesh mesh={.vertices=vertices,.vertex_count=4,.indices=indices,.index_count=6};
    DlModelInstance model={.scale={1,1,1},.color={179,127,83,255},.emissive={1,8,13}};
    DlLight lights[]={{{-1,3,-2},8,.9f,{1,.5f,.2f}},{{2,2,1},6,1.3f,{.3f,.5f,1}}};
    DlCollider boxes[]={{{0,1,1},{.5f,1,.1f},.2f,true},{{-2,1,-2},{.3f,1,.4f},-.4f,false}};
    DlLevel level={.version=2,.models=&model,.model_count=1,.meshes=&mesh,.mesh_count=1,
        .lights=lights,.light_count=2,.colliders=boxes,.collider_count=2,
        .environment={.enabled=true,.ambient={.04f,.06f,.1f},.moon_direction={-.4f,.8f,.2f},
                      .moon_color={.3f,.45f,1},.moon_intensity=.7f,.exposure=1.35f}};
    DlStaticLightingColor actual[2],expected[2];unsigned comparisons=0;
    for(int smooth=0;smooth<2;++smooth)for(int i=0;i<120;++i)for(int opened=0;opened<2;++opened){
        mesh.normals=smooth?normals:NULL;
        model.role=i%3==0?DL_MODEL_DOOR:i%3==1?DL_MODEL_STATIC:DL_MODEL_CONTROL;
        model.double_sided=(i%2)!=0;
        model.position=(DlVec3){(i%7)*.27f-1,(i%4)*.11f,(i%9)*.19f-1};
        model.rotation=(DlVec3){(i%5)*.21f,(i%13)*.19f,(i%3)*-.13f};
        model.scale=(DlVec3){i%2?-.73f:1.3f,.5f+(i%6)*.21f,.4f+(i%8)*.17f};
        CHECK(dl_static_lighting_model(&level,0,opened!=0,actual,2));
        legacy(&level,0,opened!=0,expected);
        CHECK(memcmp(actual,expected,sizeof(actual))==0);++comparisons;
    }
    model.role=DL_MODEL_GUARD;CHECK(!dl_static_lighting_eligible(&level,0));
    model.role=DL_MODEL_OBJECTIVE;CHECK(!dl_static_lighting_eligible(&level,0));
    model.role=DL_MODEL_STATIC;mesh.animation=(const struct DlAnimationAsset *)&mesh;
    CHECK(!dl_static_lighting_eligible(&level,0));mesh.animation=NULL;
    memset(actual,0x5a,sizeof(actual));memcpy(expected,actual,sizeof(actual));
    CHECK(!dl_static_lighting_model(&level,0,false,actual,1));CHECK(!memcmp(actual,expected,sizeof(actual)));
    indices[5]=99;CHECK(!dl_static_lighting_model(&level,0,false,actual,2));CHECK(!memcmp(actual,expected,sizeof(actual)));indices[5]=3;
    level.environment.enabled=false;CHECK(!dl_static_lighting_eligible(&level,0));
    CHECK(!dl_static_lighting_eligible(NULL,0));
    printf("Static lighting: %u exact legacy comparisons passed (flat/smooth, sides, hinge, rotations, signed nonuniform scale)\n",comparisons);
    return 0;
}
