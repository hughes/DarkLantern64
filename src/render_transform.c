#include "render_transform.h"
#include <math.h>

bool dl_render_pose_cache_update(DlRenderPoseCache *cache,bool dynamic,
    const DlVec3 *position,const DlVec3 *rotation,const DlVec3 *scale,
    bool door_open,float hinge_x,float hinge_z){
    if(cache->valid&&!dynamic&&cache->pose.door_open==door_open)return false;
    cache->pose=(DlRenderPose){*position,
        {sinf(rotation->x),sinf(rotation->y),sinf(rotation->z)},
        {cosf(rotation->x),cosf(rotation->y),cosf(rotation->z)},door_open};
    dl_render_instance_matrix(&cache->world,*position,*scale,
        cache->pose.sine,cache->pose.cosine,door_open,hinge_x,hinge_z);
    cache->valid=true;
    return true;
}

void dl_render_instance_matrix(DlAnimMatrix *out,DlVec3 position,DlVec3 scale,
    DlVec3 sine,DlVec3 cosine,bool door_open,float hinge_x,float hinge_z){
    /* Columns of Rz * Ry * Rx. */
    DlVec3 x={cosine.z*cosine.y,sine.z*cosine.y,-sine.y};
    DlVec3 y={cosine.z*sine.y*sine.x-sine.z*cosine.x,
        sine.z*sine.y*sine.x+cosine.z*cosine.x,cosine.y*sine.x};
    DlVec3 z={cosine.z*sine.y*cosine.x+sine.z*sine.x,
        sine.z*sine.y*cosine.x-cosine.z*sine.x,cosine.y*cosine.x};
    if(door_open){
        float dx=hinge_x-hinge_z,dz=hinge_z+hinge_x;
        position.x+=x.x*dx+z.x*dz;
        position.y+=x.y*dx+z.y*dz;
        position.z+=x.z*dx+z.z*dz;
        DlVec3 previous_x=x;
        x=(DlVec3){-z.x,-z.y,-z.z};z=previous_x;
    }
    *out=(DlAnimMatrix){{x.x*scale.x,y.x*scale.y,z.x*scale.z,position.x,
        x.y*scale.x,y.y*scale.y,z.y*scale.z,position.y,
        x.z*scale.x,y.z*scale.y,z.z*scale.z,position.z}};
}

void dl_render_affine_compose(DlAnimMatrix *out,const DlAnimMatrix *left,
    const DlAnimMatrix *right){
    DlAnimMatrix product;
    for(int row=0;row<3;++row){
        int r=row*4;
        for(int column=0;column<3;++column)
            product.m[r+column]=left->m[r]*right->m[column]+
                left->m[r+1]*right->m[4+column]+left->m[r+2]*right->m[8+column];
        product.m[r+3]=left->m[r]*right->m[3]+left->m[r+1]*right->m[7]+
            left->m[r+2]*right->m[11]+left->m[r+3];
    }
    *out=product;
}

DlVec3 dl_render_matrix_point(const DlAnimMatrix *matrix,DlVec3 point){
    return (DlVec3){matrix->m[0]*point.x+matrix->m[1]*point.y+matrix->m[2]*point.z+matrix->m[3],
        matrix->m[4]*point.x+matrix->m[5]*point.y+matrix->m[6]*point.z+matrix->m[7],
        matrix->m[8]*point.x+matrix->m[9]*point.y+matrix->m[10]*point.z+matrix->m[11]};
}

void dl_render_normal_matrix(DlAnimMatrix *out,DlVec3 scale,DlVec3 sine,
    DlVec3 cosine,bool door_open,const DlAnimMatrix *bone){
    DlAnimMatrix instance;
    dl_render_instance_matrix(&instance,(DlVec3){0},
        (DlVec3){1/scale.x,1/scale.y,1/scale.z},sine,cosine,door_open,0,0);
    if(!bone){*out=instance;return;}
    DlAnimMatrix result;
    for(int row=0;row<3;++row){
        int r=row*4;
        for(int column=0;column<3;++column)
            result.m[r+column]=instance.m[r]*bone->m[column]+
                instance.m[r+1]*bone->m[4+column]+instance.m[r+2]*bone->m[8+column];
        result.m[r+3]=0;
    }
    *out=result;
}

DlVec3 dl_render_matrix_normal(const DlAnimMatrix *matrix,DlVec3 normal){
    DlVec3 n={matrix->m[0]*normal.x+matrix->m[1]*normal.y+matrix->m[2]*normal.z,
        matrix->m[4]*normal.x+matrix->m[5]*normal.y+matrix->m[6]*normal.z,
        matrix->m[8]*normal.x+matrix->m[9]*normal.y+matrix->m[10]*normal.z};
    float length=sqrtf(n.x*n.x+n.y*n.y+n.z*n.z);
    if(length<=0.000001f)return (DlVec3){0,1,0};
    float inverse_length=1/length;
    return (DlVec3){n.x*inverse_length,n.y*inverse_length,n.z*inverse_length};
}

void dl_render_modelview(DlAnimMatrix *out,const DlAnimMatrix *world,
    DlVec3 eye,DlCameraBasis camera,float gpu_units_per_metre,float packed_units_per_metre){
    DlVec3 axes[3]={camera.right,camera.up,
        {-camera.forward.x,-camera.forward.y,-camera.forward.z}};
    DlVec3 relative={world->m[3]-eye.x,world->m[7]-eye.y,world->m[11]-eye.z};
    float scale=gpu_units_per_metre/packed_units_per_metre;
    DlAnimMatrix result;
    for(int row=0;row<3;++row){
        DlVec3 axis=axes[row];
        for(int column=0;column<3;++column)
            result.m[row*4+column]=(axis.x*world->m[column]+axis.y*world->m[4+column]+
                axis.z*world->m[8+column])*scale;
        result.m[row*4+3]=(axis.x*relative.x+axis.y*relative.y+axis.z*relative.z)*gpu_units_per_metre;
    }
    *out=result;
}

void dl_render_matrix_column_major(float out[4][4],const DlAnimMatrix *matrix){
    for(int column=0;column<4;++column){
        for(int row=0;row<3;++row)out[column][row]=matrix->m[row*4+column];
        out[column][3]=column==3?1:0;
    }
}
