#include "render_transform.h"
#include "render_shading.h"
#include <stdio.h>
#include <string.h>

#define CHECK(c) do{if(!(c)){fprintf(stderr,"%s:%d: %s\n",__FILE__,__LINE__,#c);return 1;}}while(0)
static float max_point_error,max_normal_error,max_view_error,max_fixed_error;
static float distance(DlVec3 a,DlVec3 b){
    float x=a.x-b.x,y=a.y-b.y,z=a.z-b.z;return sqrtf(x*x+y*y+z*z);
}
static DlVec3 reference_rotate(DlVec3 p,DlVec3 sine,DlVec3 cosine){
    p=(DlVec3){p.x,cosine.x*p.y-sine.x*p.z,sine.x*p.y+cosine.x*p.z};
    p=(DlVec3){cosine.y*p.x+sine.y*p.z,p.y,-sine.y*p.x+cosine.y*p.z};
    return (DlVec3){cosine.z*p.x-sine.z*p.y,sine.z*p.x+cosine.z*p.y,p.z};
}
/* Independent copy of the old renderer's scale/hinge/Euler/translation order. */
static DlVec3 reference_point(DlVec3 p,DlVec3 position,DlVec3 scale,
    DlVec3 sine,DlVec3 cosine,bool door,float hx,float hz){
    p.x*=scale.x;p.y*=scale.y;p.z*=scale.z;
    if(door){float x=p.x-hx,z=p.z-hz;p.x=hx+z;p.z=hz-x;}
    p=reference_rotate(p,sine,cosine);
    return (DlVec3){p.x+position.x,p.y+position.y,p.z+position.z};
}
static DlVec3 reference_camera(DlVec3 world,DlVec3 eye,DlCameraBasis camera){
    DlVec3 rel={world.x-eye.x,world.y-eye.y,world.z-eye.z};
    return (DlVec3){rel.x*camera.right.x+rel.y*camera.right.y+rel.z*camera.right.z,
        rel.x*camera.up.x+rel.y*camera.up.y+rel.z*camera.up.z,
        -(rel.x*camera.forward.x+rel.y*camera.forward.y+rel.z*camera.forward.z)};
}
static DlAnimMatrix reference_bone(float angle){
    DlVec3 s={sinf(angle),sinf(angle*.7f),sinf(angle*-.4f)};
    DlVec3 c={cosf(angle),cosf(angle*.7f),cosf(angle*-.4f)};
    DlVec3 x=reference_rotate((DlVec3){1,0,0},s,c),
        y=reference_rotate((DlVec3){0,1,0},s,c),z=reference_rotate((DlVec3){0,0,1},s,c);
    return (DlAnimMatrix){{x.x,y.x,z.x,.7f,x.y,y.y,z.y,-.4f,x.z,y.z,z.z,.3f}};
}

static int parity(void){
    for(int pose=0;pose<100;++pose)for(int door=0;door<2;++door)for(int animated=0;animated<2;++animated){
        float angle=pose*.137f;
        DlVec3 s={sinf(angle),sinf(angle*-.3f),sinf(angle*.7f)},
            c={cosf(angle),cosf(angle*-.3f),cosf(angle*.7f)};
        DlVec3 position={20*sinf(angle*.71f),pose*.07f,-17*cosf(angle*.29f)};
        DlVec3 scale={(pose%7==0?-1:1)*(.3f+(pose%5)*.6f),.25f+(pose%3)*.4f,2.7f};
        float hx=-.61f*scale.x,hz=.23f*scale.z;
        DlAnimMatrix instance,bone=reference_bone(angle*.61f),world,normal_matrix;
        dl_render_instance_matrix(&instance,position,scale,s,c,door!=0,hx,hz);
        if(animated)dl_render_affine_compose(&world,&instance,&bone);else world=instance;
        dl_render_normal_matrix(&normal_matrix,scale,s,c,door!=0,animated?&bone:NULL);
        CHECK(normal_matrix.m[3]==0&&normal_matrix.m[7]==0&&normal_matrix.m[11]==0);
        DlVec3 eye={position.x+3.1f,position.y+1.7f,position.z-6.3f};
        DlCameraBasis camera=dl_camera_basis(angle*-.73f,angle*.17f);
        float gpu=pose%2?64:32,packed=pose%3?1024:512;
        DlAnimMatrix view;
        dl_render_modelview(&view,&world,eye,camera,gpu,packed);
        float columns[4][4];dl_render_matrix_column_major(columns,&view);
        for(int column=0;column<4;++column){
            CHECK(columns[column][3]==(column==3?1:0));
            for(int row=0;row<3;++row)CHECK(columns[column][row]==view.m[row*4+column]);
        }
        for(int vertex=0;vertex<96;++vertex){
            DlVec3 p={sinf(vertex*1.37f)*1.9f,cosf(vertex*.73f)*1.7f,sinf(vertex*.21f)*1.6f};
            DlVec3 skinned=animated?dl_animation_point(&bone,p):p;
            DlVec3 expected=reference_point(skinned,position,scale,s,c,door!=0,hx,hz);
            DlVec3 actual=dl_render_matrix_point(&world,p);
            float error=distance(expected,actual);max_point_error=fmaxf(max_point_error,error);
            CHECK(error<.00002f);
            DlNormal packed_normal={(int8_t)(vertex%127-63),(int8_t)((vertex*3)%127-63),(int8_t)((vertex*7)%127-63)};
            DlVec3 n={packed_normal.x,packed_normal.y,packed_normal.z};
            DlVec3 bone_normal=animated?dl_animation_normal(&bone,n):n;
            DlVec3 expected_normal=dl_render_normal_vector(bone_normal,scale,s,c,door!=0);
            DlVec3 actual_normal=dl_render_matrix_normal(&normal_matrix,n);
            error=distance(expected_normal,actual_normal);max_normal_error=fmaxf(max_normal_error,error);
            CHECK(error<.000002f);
            DlVec3 expected_view=reference_camera(expected,eye,camera);
            DlVec3 gpu_view=dl_render_matrix_point(&view,(DlVec3){p.x*packed,p.y*packed,p.z*packed});
            DlVec3 actual_view={gpu_view.x/gpu,gpu_view.y/gpu,gpu_view.z/gpu};
            error=distance(expected_view,actual_view);max_view_error=fmaxf(max_view_error,error);
            CHECK(error<.00003f);
            /* Reproduce T3D's column-major vector multiply independently. */
            DlVec3 column_result={
                columns[0][0]*p.x*packed+columns[1][0]*p.y*packed+columns[2][0]*p.z*packed+columns[3][0],
                columns[0][1]*p.x*packed+columns[1][1]*p.y*packed+columns[2][1]*p.z*packed+columns[3][1],
                columns[0][2]*p.x*packed+columns[1][2]*p.y*packed+columns[2][2]*p.z*packed+columns[3][2]};
            CHECK(distance(column_result,gpu_view)<.001f);
            /* Measure the additional integer-vertex/16.16-matrix error without
             * pretending float parity is parity after target quantization. */
            DlVec3 q={roundf(p.x*packed),roundf(p.y*packed),roundf(p.z*packed)};
            DlAnimMatrix fixed=view;
            for(int i=0;i<12;++i)fixed.m[i]=(float)(int32_t)(view.m[i]*65536)/65536;
            DlVec3 decoded=dl_render_matrix_point(&fixed,q);
            decoded=(DlVec3){decoded.x/gpu,decoded.y/gpu,decoded.z/gpu};
            error=distance(expected_view,decoded);max_fixed_error=fmaxf(max_fixed_error,error);
            CHECK(error<.008f);
        }
        /* Composition and camera conversion explicitly permit in-place use. */
        DlAnimMatrix alias=instance,expected;
        dl_render_affine_compose(&expected,&instance,&bone);
        dl_render_affine_compose(&alias,&alias,&bone);
        CHECK(memcmp(&alias,&expected,sizeof alias)==0);
        alias=bone;dl_render_affine_compose(&alias,&instance,&alias);
        CHECK(memcmp(&alias,&expected,sizeof alias)==0);
        alias=world;dl_render_modelview(&alias,&alias,eye,camera,gpu,packed);
        CHECK(memcmp(&alias,&view,sizeof alias)==0);
    }
    return 0;
}

static int conventions(void){
    DlAnimMatrix identity,view,normals;
    dl_render_instance_matrix(&identity,(DlVec3){0},(DlVec3){1,1,1},(DlVec3){0},(DlVec3){1,1,1},false,0,0);
    DlCameraBasis camera=dl_camera_basis(0,0);
    dl_render_modelview(&view,&identity,(DlVec3){0},camera,64,1024);
    DlVec3 point=dl_render_matrix_point(&view,(DlVec3){1024,2048,3072});
    CHECK(point.x==-64&&point.y==128&&point.z==-192); /* +X is screen-left at yaw zero. */
    dl_render_normal_matrix(&normals,(DlVec3){1,1,1},(DlVec3){0},(DlVec3){1,1,1},false,NULL);
    point=dl_render_matrix_normal(&normals,(DlVec3){0});
    CHECK(point.x==0&&point.y==1&&point.z==0);
    /* Camera-relative origin remains stable far from world zero. */
    identity.m[3]=100000;identity.m[7]=-200000;identity.m[11]=300000;
    dl_render_modelview(&view,&identity,(DlVec3){100000,-200000,300000},camera,64,1024);
    point=dl_render_matrix_point(&view,(DlVec3){1024,2048,3072});
    CHECK(point.x==-64&&point.y==128&&point.z==-192);
    /* Points on a scaled hinge stay on the hinge when the door opens. */
    dl_render_instance_matrix(&view,(DlVec3){0},(DlVec3){2,3,4},(DlVec3){0},(DlVec3){1,1,1},true,-2,4);
    point=dl_render_matrix_point(&view,(DlVec3){-1,.5f,1});
    CHECK(point.x==-2&&point.y==1.5f&&point.z==4);
    return 0;
}

static int cached_instances(void){
    DlRenderPoseCache caches[4]={0};
    for(int level=0;level<2;++level){
        /* A new level can reuse every model slot with different authored data. */
        memset(caches,0,sizeof caches);
        unsigned rebuilt[4]={0};
        for(int frame=0;frame<120;++frame){
            DlVec3 eye={frame*.031f,1.7f+level,cosf(frame*.07f)*7};
            DlCameraBasis camera=dl_camera_basis(frame*.013f,frame*.003f);
            for(int kind=0;kind<4;++kind){
                /* Scenery, hinged door, moving guard, spinning objective. */
                bool dynamic=kind>=2;
                bool door=kind==1&&((frame/30+level)%2!=0);
                DlVec3 position={3.1f+level*9,level*5.3f,-2.7f},rotation={.17f,-.8f,.11f};
                DlVec3 scale={level?-1.3f:.8f,1.9f,.4f};
                if(kind==2){position.x+=sinf(frame*.1f);position.y+=frame*.01f;rotation.y+=frame*.04f;}
                if(kind==3)rotation.y+=frame*.017f;
                float hx=-.6f*scale.x,hz=.2f*scale.z;
                DlRenderPoseCache before=caches[kind];
                bool changed=dl_render_pose_cache_update(&caches[kind],dynamic,&position,&rotation,&scale,door,hx,hz);
                rebuilt[kind]+=changed;
                if(!changed)CHECK(memcmp(&before,&caches[kind],sizeof before)==0);
                DlVec3 sine={sinf(rotation.x),sinf(rotation.y),sinf(rotation.z)};
                DlVec3 cosine={cosf(rotation.x),cosf(rotation.y),cosf(rotation.z)};
                DlAnimMatrix view;
                dl_render_modelview(&view,&caches[kind].world,eye,camera,64,1024);
                for(int vertex=0;vertex<8;++vertex){
                    DlVec3 point={vertex&1?.7f:-.9f,vertex&2?1.6f:-.1f,vertex&4?.4f:-.3f};
                    DlVec3 expected=reference_point(point,position,scale,sine,cosine,door,hx,hz);
                    CHECK(distance(expected,dl_render_matrix_point(&caches[kind].world,point))<.00002f);
                    DlVec3 actual_view=dl_render_matrix_point(&view,(DlVec3){point.x*1024,point.y*1024,point.z*1024});
                    actual_view=(DlVec3){actual_view.x/64,actual_view.y/64,actual_view.z/64};
                    CHECK(distance(reference_camera(expected,eye,camera),actual_view)<.00003f);
                }
            }
        }
        CHECK(rebuilt[0]==1&&rebuilt[1]==4&&rebuilt[2]==120&&rebuilt[3]==120);
    }
    /* Lighting warmup can visit the alternate hinge state. Restoring the real
     * state must restore the matrix even if the authored transform is static. */
    DlRenderPoseCache door={0};DlVec3 p={4,0,-2},r={0,.6f,0},s={1,2,.3f};
    CHECK(dl_render_pose_cache_update(&door,false,&p,&r,&s,false,-.5f,0));
    DlAnimMatrix closed=door.world;
    CHECK(dl_render_pose_cache_update(&door,false,&p,&r,&s,true,-.5f,0));
    CHECK(memcmp(&closed,&door.world,sizeof closed)!=0);
    CHECK(dl_render_pose_cache_update(&door,false,&p,&r,&s,false,-.5f,0));
    CHECK(memcmp(&closed,&door.world,sizeof closed)==0);
    return 0;
}

int main(void){
    CHECK(parity()==0);CHECK(conventions()==0);CHECK(cached_instances()==0);
    printf("render transform: 38400 old-path comparisons passed; max point %.9g m, normal %.9g, view %.9g m; packed/fixed max %.9g m\n",
        max_point_error,max_normal_error,max_view_error,max_fixed_error);
    printf("render pose cache: 7680 moving-camera comparisons, two level resets, dynamic motion and hinge warmup passed; %u bytes/instance\n",(unsigned)sizeof(DlRenderPoseCache));
    return 0;
}
