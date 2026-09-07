#include "render_lighting.h"
#include "render_shading.h"
#include "render_transform.h"
#include "animation.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
static float clamp_reference(float x){return x<0?0:x>255?255:x;}
static DlLightingColor reference_scalar(const DlGame *game,const DlModelInstance *model,
    DlVec3 normal,float light){
    float d=normal.x*0.30f+normal.y*0.81f+normal.z*-0.50f;
    float shape=0.72f+0.28f*fmaxf(0,d);
    float exposure=(0.24f+0.92f*light)*shape;
    float r=model->color[0],g=model->color[1],b=model->color[2];
    if(model->role==DL_MODEL_CONTROL&&game->door_open){r=88;g=185;b=117;}
    if(model->role==DL_MODEL_GUARD&&model->enemy_index>=0&&
       model->enemy_index<game->enemy_count&&game->enemies[model->enemy_index].state==DL_CHASE){
        r=180;g=72;b=56;
    }
    if(model->role==DL_MODEL_OBJECTIVE)exposure=0.62f+0.18f*shape;
    return (DlLightingColor){(uint8_t)(int)clamp_reference(r*exposure+light*12),
        (uint8_t)(int)clamp_reference(g*exposure+light*6),
        (uint8_t)(int)clamp_reference(b*exposure),255};
}
static DlLightingColor reference_night(DlLightingColor body,DlVec3 normal){
    float d=normal.x*0.30f+normal.y*0.81f+normal.z*-0.50f;
    float shape=.70f+.30f*fmaxf(0,d);
    return (DlLightingColor){(uint8_t)(int)(body.r*shape),(uint8_t)(int)(body.g*shape),
        (uint8_t)(int)(body.b*shape),255};
}
static int same_color(DlLightingColor a,DlLightingColor b){return memcmp(&a,&b,sizeof a)==0;}

static int color_parity(void){
    DlGame game={0};game.enemy_count=1;
    DlModelInstance model={0};
    /* Exercise clipping, both normal directions, objective/control/chase
     * overrides, missing guards, all roles, and a broad set of byte boundaries. */
    for(int role=DL_MODEL_STATIC;role<=DL_MODEL_OBJECTIVE;++role){
        model.role=(DlModelRole)role;
        for(int state=0;state<8;++state){
            game.door_open=(state&1)!=0;
            game.enemies[0].state=(state&2)?DL_CHASE:DL_PATROL;
            model.enemy_index=(state&4)?-1:0;
            for(int i=0;i<4000;++i){
                float y=(float)((i*17)%2001)/1000-1;
                float angle=i*0.173f,radius=sqrtf(fmaxf(0,1-y*y));
                DlVec3 normal={radius*cosf(angle),y,radius*sinf(angle)};
                DlVec3 reverse={-normal.x,-normal.y,-normal.z};
                float light=(float)(i%129)/128;
                model.color[0]=(uint8_t)(i*23);
                model.color[1]=(uint8_t)(i*61);
                model.color[2]=(uint8_t)(i*11);
                DlScalarLighting context;
                dl_lighting_scalar_prepare(&game,&model,light,&context);
                DlLightingPair pair=dl_lighting_scalar_pair(&context,normal,true);
                CHECK(same_color(pair.front,reference_scalar(&game,&model,normal,light)));
                CHECK(same_color(pair.back,reference_scalar(&game,&model,reverse,light)));
                pair=dl_lighting_scalar_pair(&context,normal,false);
                CHECK(same_color(pair.front,pair.back));
                DlLightingColor body={model.color[0],model.color[1],model.color[2],255};
                pair=dl_lighting_night_pair(body,normal,true);
                CHECK(same_color(pair.front,reference_night(body,normal)));
                CHECK(same_color(pair.back,reference_night(body,reverse)));
                pair=dl_lighting_night_pair(body,normal,false);
                CHECK(same_color(pair.front,pair.back));
            }
        }
    }
    return 0;
}

static int groups(void){
    /* Vertices can be arbitrarily distant or split for atlas seams. Equal
     * bind normals may be shared only when their joint also matches. */
    DlNormal normals[]={{0,127,0},{0,127,0},{0,127,0},{127,0,0},
        {127,0,0},{0,127,0},{0,0,-127},{0,0,-127}};
    uint8_t bones[]={0,0,1,1,1,1,0,1};
    DlAnimationAsset animation={.bone_count=2,.vertex_bones=bones};
    DlMesh mesh={.vertex_count=8,.normals=normals,.animation=&animation};
    uint16_t map[8],representatives[8];
    CHECK(dl_lighting_build_groups(&mesh,map,representatives,8)==5);
    const uint16_t expected[]={0,0,1,2,2,1,3,4},reps[]={0,2,3,6,7};
    CHECK(memcmp(map,expected,sizeof expected)==0);
    CHECK(memcmp(representatives,reps,sizeof reps)==0);
    for(int pose=0;pose<40;++pose){
        float a=pose*.19f,s=sinf(a),c=cosf(a);
        DlAnimMatrix skin[2]={{{1,0,0,0,0,1,0,0,0,0,1,0}},
            {{c,0,s,.3f,0,1,0,.7f,-s,0,c,-.5f}}};
        DlVec3 grouped[5];
        DlVec3 scale={2,.5f,3},sine={s,.5f,0},cosine={c,sqrtf(.75f),1};
        for(int group=0;group<5;++group){
            int v=representatives[group];
            DlNormal p=normals[v];
            grouped[group]=dl_render_normal_vector(dl_animation_normal(&skin[bones[v]],
                (DlVec3){p.x,p.y,p.z}),scale,sine,cosine,pose%2!=0);
        }
        for(int v=0;v<8;++v){
            DlNormal p=normals[v];
            DlVec3 direct=dl_render_normal_vector(dl_animation_normal(&skin[bones[v]],
                (DlVec3){p.x,p.y,p.z}),scale,sine,cosine,pose%2!=0);
            CHECK(memcmp(&direct,&grouped[map[v]],sizeof direct)==0);
        }
    }
    uint16_t preserved[8];memcpy(preserved,map,sizeof map);
    CHECK(dl_lighting_build_groups(&mesh,map,representatives,7)==-1);
    CHECK(memcmp(preserved,map,sizeof map)==0);
    bones[7]=2;
    CHECK(dl_lighting_build_groups(&mesh,map,representatives,8)==-1);
    CHECK(memcmp(preserved,map,sizeof map)==0);
    bones[7]=1;
    CHECK(dl_lighting_build_groups(&mesh,map,map,8)==-1);
    mesh.animation=NULL;
    CHECK(dl_lighting_build_groups(&mesh,map,representatives,8)==3);
    mesh.normals=NULL;
    CHECK(dl_lighting_build_groups(&mesh,map,representatives,8)==-1);
    return 0;
}

static int max_channel_error(DlLightingColor a,DlLightingColor b){
    int r=abs((int)a.r-b.r),g=abs((int)a.g-b.g),blue=abs((int)a.b-b.b);
    return r>g?(r>blue?r:blue):(g>blue?g:blue);
}
static int bulk_groups(void){
    enum {COUNT=128,BONES=8};
    DlNormal normals[COUNT];uint8_t joints[COUNT];uint16_t representatives[COUNT];
    for(int i=0;i<COUNT;++i){
        normals[i]=(DlNormal){(int8_t)(i%127-63),(int8_t)((i*3)%127-63),(int8_t)((i*7)%127-63)};
        joints[i]=(uint8_t)(i%BONES);representatives[i]=(uint16_t)i;
    }
    DlAnimationAsset animation={.bone_count=BONES,.vertex_bones=joints};
    DlMesh mesh={.normals=normals,.vertex_count=COUNT,.animation=&animation};
    DlLightingNormalGroup prepared[COUNT];
    CHECK(dl_lighting_prepare_normals(&mesh,representatives,COUNT,prepared));
    for(int i=0;i<COUNT;++i){
        DlVec3 n=prepared[i].unit_normal;
        CHECK(fabsf(n.x*n.x+n.y*n.y+n.z*n.z-1)<.000001f&&prepared[i].bone==joints[i]);
    }
    DlGame game={0};game.enemy_count=1;
    DlModelInstance model={.role=DL_MODEL_GUARD,.enemy_index=0};
    int max_error=0,changed=0;
    for(int pose=0;pose<100;++pose)for(int scaling=0;scaling<4;++scaling){
        DlVec3 scale=scaling==0?(DlVec3){1,1,1}:scaling==1?(DlVec3){.3f,.3f,.3f}:
            scaling==2?(DlVec3){.3f,2.4f,.7f}:(DlVec3){-1,1,1};
        float angle=pose*.127f;
        DlVec3 s={sinf(angle),sinf(angle*-.7f),sinf(angle*.3f)},
            c={cosf(angle),cosf(angle*-.7f),cosf(angle*.3f)};
        DlAnimMatrix palette[BONES],skin[BONES];
        for(int b=0;b<BONES;++b){
            float a=angle*(b+1)*.17f;
            dl_render_instance_matrix(&skin[b],(DlVec3){.3f,.4f,.5f},(DlVec3){1,1,1},
                (DlVec3){sinf(a),0,0},(DlVec3){cosf(a),1,1},false,0,0);
            dl_render_normal_matrix(&palette[b],scale,s,c,false,&skin[b]);
        }
        model.color[0]=(uint8_t)(pose*17);model.color[1]=(uint8_t)(pose*23);model.color[2]=(uint8_t)(pose*71);
        game.enemies[0].state=pose%2?DL_CHASE:DL_PATROL;
        float light=(float)(pose%33)/32;DlScalarLighting context;
        dl_lighting_scalar_prepare(&game,&model,light,&context);
        DlLightingPair scalar[COUNT],night[COUNT];
        DlLightingColor body={model.color[0],model.color[1],model.color[2],255};
        dl_lighting_scalar_groups(&context,prepared,COUNT,palette,BONES,scale,true,scalar);
        dl_lighting_night_groups(body,prepared,COUNT,palette,BONES,scale,true,night);
        for(int i=0;i<COUNT;++i){
            DlNormal packed=normals[i];
            DlVec3 normal=dl_render_normal_vector(dl_animation_normal(&skin[joints[i]],
                (DlVec3){packed.x,packed.y,packed.z}),scale,s,c,false);
            DlVec3 reverse={-normal.x,-normal.y,-normal.z};
            int error=max_channel_error(scalar[i].front,reference_scalar(&game,&model,normal,light));
            if(error)++changed;
            if(error>max_error)max_error=error;
            CHECK(error<=1&&max_channel_error(scalar[i].back,reference_scalar(&game,&model,reverse,light))<=1);
            CHECK(max_channel_error(night[i].front,reference_night(body,normal))<=1);
            CHECK(max_channel_error(night[i].back,reference_night(body,reverse))<=1);
        }
        dl_lighting_scalar_groups(&context,prepared,COUNT,palette,BONES,scale,false,scalar);
        dl_lighting_night_groups(body,prepared,COUNT,palette,BONES,scale,false,night);
        for(int i=0;i<COUNT;++i){
            CHECK(same_color(scalar[i].front,scalar[i].back));
            CHECK(same_color(night[i].front,night[i].back));
        }
    }
    printf("bulk lighting: 51200 uniform/nonuniform/reflected scalar/night groups; changed scalar front=%d, max byte error=%d\n",changed,max_error);
    normals[0]=(DlNormal){0};
    CHECK(!dl_lighting_prepare_normals(&mesh,representatives,COUNT,prepared));
    representatives[0]=COUNT;
    CHECK(!dl_lighting_prepare_normals(&mesh,representatives,COUNT,prepared));
    return 0;
}

int main(void){
    CHECK(sizeof(DlLightingColor)==4&&sizeof(DlLightingPair)==8);
    CHECK(color_parity()==0);
    CHECK(groups()==0);
    CHECK(bulk_groups()==0);
    puts("render lighting: 160000 scalar/night normal pairs match original bytes; joint/seam grouping and transformed normals passed");
    return 0;
}
