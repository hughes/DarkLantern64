/* Private RSP backend. Kept beside render.c so both backends share exactly the
 * scene, pose, lighting, texture and HUD policy. No SDK or microcode patches. */
enum { GPU_FRAMES=3 };
static const float gpu_units=64.0f;
typedef struct {
    DlRenderBatches batches;
    uint16_t *normal_groups,*representatives;
    DlLightingNormalGroup *normal_data;
    int normal_count;
    float packed_units;
} GpuMesh;
typedef struct {
    GpuMesh *mesh;
    bool owns_mesh;
    T3DVertPacked *vertices[GPU_FRAMES][2];
    T3DMat4FP *matrices[GPU_FRAMES];
    rspq_block_t *commands;
    int color_state[GPU_FRAMES];
    bool color_fog[GPU_FRAMES];
} GpuModel;
static GpuModel gpu_models[MAX_MODELS];
static T3DViewport gpu_viewports[GPU_FRAMES];
static rspq_syncpoint_t gpu_fences[GPU_FRAMES];
static unsigned gpu_frame;
/* C union representation access: N64 is big-endian, so RGBA bytes are the
 * RSP's 32-bit color word. Word alignment avoids unaligned byte reconstruction. */
static union { DlLightingPair pairs[MAX_VERTICES];uint32_t words[MAX_VERTICES][2]; } gpu_colors;
#define gpu_group_colors gpu_colors.pairs
_Static_assert(sizeof(DlLightingColor)==4&&sizeof(DlLightingPair)==8,"RSP color word layout");
static DlAnimMatrix gpu_world_palette[DL_ANIMATION_MAX_BONES];
static float gpu_vertex_fog[MAX_VERTICES];
static uint64_t gpu_probe_ticks,gpu_normal_ticks,gpu_color_ticks;
static uint32_t gpu_lighting_frames;
static void gpu_report(void){
    debugf("DL64 rsp_lighting frames=%lu probe_ticks=%llu normal_ticks=%llu color_upload_ticks=%llu ticks_per_second=%lu\n",
        (unsigned long)gpu_lighting_frames,(unsigned long long)gpu_probe_ticks,
        (unsigned long long)gpu_normal_ticks,(unsigned long long)gpu_color_ticks,(unsigned long)TICKS_PER_SECOND);
    gpu_probe_ticks=gpu_normal_ticks=gpu_color_ticks=0;gpu_lighting_frames=0;
}

static uint32_t gpu_color(color_t c){return (uint32_t)c.r<<24|(uint32_t)c.g<<16|(uint32_t)c.b<<8|c.a;}
static color_t gpu_native(DlLightingColor c){return (color_t){c.r,c.g,c.b,c.a};}
static void gpu_release(void){
    for(int i=0;i<MAX_MODELS;++i){
        GpuModel *model=&gpu_models[i];
        if(model->commands)rspq_block_free(model->commands);
        for(int frame=0;frame<GPU_FRAMES;++frame){
            free(model->matrices[frame]);
            for(int side=0;side<2;++side)free(model->vertices[frame][side]);
        }
        if(model->owns_mesh){
            dl_render_batches_free(&model->mesh->batches);
            free(model->mesh->normal_groups);free(model->mesh->representatives);free(model->mesh->normal_data);free(model->mesh);
        }
    }
    memset(gpu_models,0,sizeof(gpu_models));
    memset(gpu_fences,0,sizeof(gpu_fences));gpu_frame=0;
}
static void gpu_prepare(const DlGame *g){
    unsigned bytes=0,batch_count=0,loads=0;
    for(int i=0;i<g->level->model_count;++i){
        const DlModelInstance *instance=&g->level->models[i];
        const DlMesh *mesh=&g->level->meshes[instance->mesh];
        GpuModel *model=&gpu_models[i];
        for(int j=0;j<i;++j)if(g->level->models[j].mesh==instance->mesh){model->mesh=gpu_models[j].mesh;break;}
        if(!model->mesh){
            model->mesh=calloc(1,sizeof(GpuMesh));model->owns_mesh=true;
            assertf(model->mesh,"Cannot allocate RSP mesh");
            GpuMesh *shared=model->mesh;
            assertf(dl_render_batches_build(mesh,&shared->batches),"Cannot batch RSP mesh");
            shared->packed_units=1024;
            float extent=0;
            for(int v=0;v<mesh->vertex_count;++v){
                DlVec3 p=mesh->vertices[v];extent=fmaxf(extent,fmaxf(fabsf(p.x),fmaxf(fabsf(p.y),fabsf(p.z))));
            }
            while(extent*shared->packed_units>32760)shared->packed_units*=0.5f;
            if(mesh->animation&&mesh->normals){
                shared->normal_groups=malloc(mesh->vertex_count*sizeof(uint16_t));
                shared->representatives=malloc(mesh->vertex_count*sizeof(uint16_t));
                assertf(shared->normal_groups&&shared->representatives,"Cannot allocate RSP normal groups");
                shared->normal_count=dl_lighting_build_groups(mesh,shared->normal_groups,shared->representatives,mesh->vertex_count);
                assertf(shared->normal_count>0,"Invalid RSP normal groups");
                shared->normal_data=malloc(shared->normal_count*sizeof(DlLightingNormalGroup));
                assertf(shared->normal_data&&dl_lighting_prepare_normals(mesh,shared->representatives,shared->normal_count,shared->normal_data),"Cannot prepare RSP normals");
                bytes+=mesh->vertex_count*sizeof(uint16_t)*2;
                bytes+=shared->normal_count*sizeof(DlLightingNormalGroup);
            }
            bytes+=sizeof(GpuMesh)+shared->batches.allocated_bytes;
        }
        const DlRenderBatches *batches=&model->mesh->batches;
        int bone_count=mesh->animation?mesh->animation->bone_count:1;
        unsigned vertex_bytes=batches->vertex_count/2*sizeof(T3DVertPacked);
        int16_t *packed_uv=NULL;
        if(mesh->uvs&&instance->texture>=0){
            assertf(instance->texture<g->level->texture_count,"Invalid RSP texture index");
            const DlTexture *texture=&g->level->textures[instance->texture];
            packed_uv=malloc(batches->vertex_count*2*sizeof(int16_t));
            assertf(packed_uv,"Cannot allocate RSP texture packing scratch");
            DlTexturePackError error;
            bool valid=dl_render_texture_pack_ex(mesh,batches,texture->width,texture->height,packed_uv,&error);
            assertf(valid,"RSP texture packing model=%d batch=%lu: %s; split triangles with excessive UV spans",i,(unsigned long)error.batch,dl_render_texture_pack_status_name(error.status));
        }
        for(int frame=0;frame<GPU_FRAMES;++frame){
            model->matrices[frame]=memalign(16,bone_count*sizeof(T3DMat4FP));
            assertf(model->matrices[frame],"Cannot allocate RSP palette");
            bytes+=bone_count*sizeof(T3DMat4FP);
            model->color_state[frame]=-1;
            for(int side=0;side<(instance->double_sided?2:1);++side){
                T3DVertPacked *packed=memalign(16,vertex_bytes);model->vertices[frame][side]=packed;
                assertf(packed,"Cannot allocate RSP vertices");memset(packed,0,vertex_bytes);bytes+=vertex_bytes;
                for(unsigned v=0;v<batches->vertex_count;++v){
                    unsigned source=batches->vertices[v].source_vertex;
                    DlVec3 point=mesh->vertices[source];
                    T3DVertPacked *pair=&packed[v/2];
                    int16_t *position=v&1?pair->posB:pair->posA;
                    position[0]=(int16_t)roundf(point.x*model->mesh->packed_units);
                    position[1]=(int16_t)roundf(point.y*model->mesh->packed_units);
                    position[2]=(int16_t)roundf(point.z*model->mesh->packed_units);
                    int16_t *uv=v&1?pair->stB:pair->stA;
                    if(packed_uv){uv[0]=packed_uv[v*2];uv[1]=packed_uv[v*2+1];}
                }
            }
        }
        free(packed_uv);
        /* Vertices and matrices are segment-relative: one immutable command
         * block safely serves all instances' buffered data. A vertex loaded
         * under an earlier bone matrix stays in the RSP cache for joint faces. */
        rspq_block_begin();
        for(unsigned b=0;b<batches->batch_count;++b){
            const DlRenderBatch *batch=&batches->batches[b];
            for(unsigned n=0;n<batch->group_count;++n){
                const DlRenderBatchGroup *group=&batches->groups[batch->group_offset+n];
                t3d_matrix_set(t3d_segment_address(T3D_SEGMENT_2,(void*)(group->bone*sizeof(T3DMat4FP))),false);
                t3d_vert_load(t3d_segment_address(T3D_SEGMENT_1,(void*)((batch->vertex_offset+group->vertex_start)/2*sizeof(T3DVertPacked))),group->vertex_start,group->vertex_count);
            }
            for(unsigned n=0;n<batch->index_count;n+=3){
                const uint8_t *idx=&batches->indices[batch->index_offset+n];
                t3d_tri_draw(idx[0],idx[1],idx[2]);
            }
            t3d_tri_sync();
        }
        model->commands=rspq_block_end();
        batch_count+=batches->batch_count;loads+=batches->vertex_count;
    }
    debugf("DL64 rsp_ready backend=tiny3d buffers=%d cache_vertices=70 batches=%u vertex_loads=%u data_bytes=%u units_per_meter=64\n",GPU_FRAMES,batch_count,loads,bytes);
}
static void gpu_actor_colors(const DlGame *g,int index,const ModelPose *pose){
    DlProfileMark mark=dl_profile_mark();
    const DlModelInstance *model=&g->level->models[index];
    const DlMesh *mesh=&g->level->meshes[model->mesh];
    const GpuMesh *shared=gpu_models[index].mesh;
    DlVec3 lower=pose->position,upper=lower;lower.y+=0.65f;upper.y+=1.35f;
    DlScalarLighting scalar={0};DlLightingColor body={0};
    bool night=g->level->environment.enabled;
    if(night){
        DlVec3 light=mul(add(dl_surface_light(g,lower,(DlVec3){0,1,0}),dl_surface_light(g,upper,(DlVec3){0,1,0})),0.5f);
        color_t color=night_color(g,model,light);body=(DlLightingColor){color.r,color.g,color.b,color.a};
    }else dl_lighting_scalar_prepare(g,model,0.5f*(dl_visibility(g,lower)+dl_visibility(g,upper)),&scalar);
    gpu_probe_ticks+=animation_elapsed_ticks(mark);mark=dl_profile_mark();
    DlAnimMatrix normal_palette[DL_ANIMATION_MAX_BONES];
    for(int b=0;b<mesh->animation->bone_count;++b)
        dl_render_normal_matrix(&normal_palette[b],model->scale,pose->sine,pose->cosine,pose->door_open,&animation_skin[b]);
    if(night)dl_lighting_night_groups(body,shared->normal_data,shared->normal_count,normal_palette,mesh->animation->bone_count,model->scale,model->double_sided,gpu_group_colors);
    else dl_lighting_scalar_groups(&scalar,shared->normal_data,shared->normal_count,normal_palette,mesh->animation->bone_count,model->scale,model->double_sided,gpu_group_colors);
    gpu_normal_ticks+=animation_elapsed_ticks(mark);
}
static void gpu_update_colors(const DlGame *g,int index,unsigned frame,bool grouped,float highlight,bool fog,const Camera *camera){
    DlProfileMark mark=dl_profile_mark();
    const DlModelInstance *instance=&g->level->models[index];
    const DlMesh *mesh=&g->level->meshes[instance->mesh];
    const ModelCache *cache=&models[index];GpuModel *model=&gpu_models[index];
    const DlRenderBatches *batches=&model->mesh->batches;
    if(fog){
        /* Preserve scalar scenes' fog-before-texture and 80% cap. Only models
         * intersecting the fog range need this compatibility work. A future
         * RSP RGB-fog vertex effect can remove these distant depth queries. */
        for(int v=0;v<mesh->vertex_count;++v){
            DlVec3 p=grouped?dl_render_matrix_point(&gpu_world_palette[mesh->animation->vertex_bones[v]],mesh->vertices[v]):world_vertices[cache->vertex_start+v];
            float depth=dot(sub(p,camera->eye),camera->forward);
            gpu_vertex_fog[v]=g->level->environment.enabled?
                clampf((depth-g->level->environment.fog_near)/(g->level->environment.fog_far-g->level->environment.fog_near),0,1):clampf((depth-8)/42,0,0.80f);
        }
    }
    for(int side=0;side<(instance->double_sided?2:1);++side){
        T3DVertPacked *packed=model->vertices[frame][side];
        if(grouped&&highlight==0&&!fog){
            /* Avoid unpacking/repacking channels for every UV seam. */
            for(unsigned pair=0;pair<batches->vertex_count/2;++pair){
                unsigned a=batches->vertices[pair*2].source_vertex,b=batches->vertices[pair*2+1].source_vertex;
                packed[pair].rgbaA=gpu_colors.words[model->mesh->normal_groups[a]][side];
                packed[pair].rgbaB=gpu_colors.words[model->mesh->normal_groups[b]][side];
            }
            data_cache_hit_writeback(packed,batches->vertex_count/2*sizeof(T3DVertPacked));continue;
        }
        for(unsigned v=0;v<batches->vertex_count;++v){
            DlRenderVertexRef ref=batches->vertices[v];color_t color;
            if(grouped){
                DlLightingPair pair=gpu_group_colors[model->mesh->normal_groups[ref.source_vertex]];
                color=gpu_native(side?pair.back:pair.front);
            }else if(g->level->environment.enabled){
                const NightColors *colors=&night_colors[g->door_open][cache->triangle_start+ref.triangle_index];
                int corner=0;while(corner<2&&mesh->indices[ref.triangle_index*3+corner]!=ref.source_vertex)++corner;
                color=side?colors->back[corner]:colors->front[corner];
            }else if(mesh->normals){
                VertexColors pair=smooth_colors[cache->smooth_start+ref.source_vertex];color=side?pair.back:pair.front;
            }else{
                FaceCache face=faces[cache->triangle_start+ref.triangle_index];color=side?face.back:face.front;
            }
            if(highlight>0){
                color.r=(uint8_t)(int)(color.r*(1-highlight)+255*highlight);
                color.g=(uint8_t)(int)(color.g*(1-highlight)+255*highlight);
                color.b=(uint8_t)(int)(color.b*(1-highlight)+255*highlight);
            }
            if(fog){
                float f=gpu_vertex_fog[ref.source_vertex];
                if(g->level->environment.enabled)color.a=(uint8_t)(int)(255*(1-f));
                else{color.r=(uint8_t)(int)(color.r*(1-f)+12*f);color.g=(uint8_t)(int)(color.g*(1-f)+15*f);color.b=(uint8_t)(int)(color.b*(1-f)+22*f);}
            }
            if(v&1)packed[v/2].rgbaB=gpu_color(color);else packed[v/2].rgbaA=gpu_color(color);
        }
        data_cache_hit_writeback(packed,batches->vertex_count/2*sizeof(T3DVertPacked));
    }
    model->color_state[frame]=g->door_open;
    model->color_fog[frame]=fog;
    gpu_color_ticks+=animation_elapsed_ticks(mark);
}
static void geometry_gpu(const DlGame *g){
    if(cached_level!=g->level){DlProfileMark mark=dl_profile_mark();build_cache(g);dl_profile_record(DL_PROFILE_CACHE,mark);}
    unsigned frame=gpu_frame++%GPU_FRAMES;
    DlProfileMark mark=dl_profile_mark();
    if(gpu_fences[frame])rspq_syncpoint_wait(gpu_fences[frame]);
    dl_profile_record(DL_PROFILE_DISPLAY_WAIT,mark);
    Camera camera=make_camera(g);DlCameraBasis basis={camera.right,camera.up,camera.forward};
    bool lighting_changed=cached_door!=g->door_open;
    ++animation_frames;visible_models=transformed_vertices=0;submitted_triangles=texture_uploads=0;
    ++gpu_lighting_frames;
    unsigned animated=0,drawn=0,full=0,overlays=0;int bound_texture=-2;
    mark=dl_profile_mark();
    t3d_frame_start();t3d_viewport_attach(&gpu_viewports[frame]);
    rdpq_mode_antialias(AA_NONE);rdpq_mode_dithering(DITHER_NONE_NONE);
    const uint8_t ambient[]={255,255,255,255};t3d_light_set_ambient(ambient);t3d_light_set_count(0);
    /* Night scenes retain fog after texture shading. Scalar scenes historically
     * fog the vertex RGB before texture modulation (see color compatibility). */
    t3d_fog_set_enabled(false);
    if(g->level->environment.enabled){
        DlVec3 fog=g->level->environment.fog_color;
        rdpq_set_fog_color(rgb((int)(fog.x*255),(int)(fog.y*255),(int)(fog.z*255)));
        rdpq_mode_fog(RDPQ_FOG_STANDARD);
    }
    dl_profile_record(DL_PROFILE_RENDER_SETUP,mark);
    for(int i=0;i<g->level->model_count;++i){
        const DlModelInstance *instance=&g->level->models[i];const DlMesh *mesh=&g->level->meshes[instance->mesh];
        ModelCache *cache=&models[i];GpuModel *model=&gpu_models[i];
        bool dynamic=instance->role==DL_MODEL_GUARD||instance->role==DL_MODEL_OBJECTIVE||mesh->animation;
        mark=dl_profile_mark();ModelPose pose=update_model_pose(g,i);
        DlVec3 rel=sub(cache->bounds_center,camera.eye);
        DlVec3 center={dot(rel,camera.right),dot(rel,camera.up),dot(rel,camera.forward)};
#ifdef DL_DISABLE_MODEL_CULL
        cache->visible=!(instance->role==DL_MODEL_OBJECTIVE&&g->complete);
#else
        cache->visible=!(instance->role==DL_MODEL_OBJECTIVE&&g->complete)&&dl_render_sphere_visible(&frustum,center,cache->bounds_radius);
#endif
        bool grouped=mesh->animation&&mesh->normals&&instance->role==DL_MODEL_GUARD;
        if(cache->visible){
            ++visible_models;transformed_vertices+=mesh->vertex_count;
            if(mesh->animation){
                DlProfileMark animation_mark=dl_profile_mark();sample_model_animation(g,i);
                uint32_t ticks=animation_elapsed_ticks(animation_mark);animation_sample_ticks+=ticks;
                if(ticks>animation_sample_max)animation_sample_max=ticks;
                ++animation_poses;++animated;
                const DlEnemy *enemy=model_enemy(g,instance);
                if(enemy&&(enemy->sees_player||enemy->state==DL_INVESTIGATE||enemy->state==DL_CHASE))++overlays;
            }
            DlAnimMatrix world;dl_render_instance_matrix(&world,pose.position,instance->scale,pose.sine,pose.cosine,pose.door_open,cache->hinge_x,cache->hinge_z);
            int count=mesh->animation?mesh->animation->bone_count:1;
            for(int b=0;b<count;++b){
                DlAnimMatrix joint=world,view;T3DMat4 matrix;
                if(mesh->animation)dl_render_affine_compose(&joint,&world,&animation_skin[b]);
                gpu_world_palette[b]=joint;
                dl_render_modelview(&view,&joint,camera.eye,basis,gpu_units,model->mesh->packed_units);
                for(int component=0;component<12;++component)
                    assertf(view.m[component]>-32768&&view.m[component]<32768,
                        "RSP modelview exceeds 16.16 range (model %d); split oversized geometry",i);
                dl_render_matrix_column_major(matrix.m,&view);t3d_mat4_to_fixed(&model->matrices[frame][b],&matrix);
            }
            data_cache_hit_writeback(model->matrices[frame],count*sizeof(T3DMat4FP));
        }
        /* Small dynamic flat props retain the reference lighting path. Smooth
         * guards do no CPU vertex deformation or per-face cross products. */
        if(!grouped&&((dynamic&&cache->visible)||(lighting_changed&&instance->role==DL_MODEL_DOOR)))
            transform_model_vertices(g,i,&pose);
        dl_profile_record(DL_PROFILE_TRANSFORMS,mark);
        mark=dl_profile_mark();
        if(!grouped&&((dynamic&&cache->visible)||(!dynamic&&lighting_changed)))
            light_model(g,i,dynamic||!g->level->environment.enabled);
        if(!cache->visible){dl_profile_record(DL_PROFILE_LIGHTING,mark);continue;}
        if(grouped)gpu_actor_colors(g,i,&pose);
        float highlight=instance->loot_highlight?dl_loot_highlight_lift(g->elapsed,sqrtf(dot(rel,rel)),(float)i*0.73f):0;
        bool fog=center.z+cache->bounds_radius>(g->level->environment.enabled?g->level->environment.fog_near:8);
        if(dynamic||instance->loot_highlight||fog||model->color_fog[frame]||model->color_state[frame]!=(int)g->door_open)
            gpu_update_colors(g,i,frame,grouped,highlight,fog,&camera);
        dl_profile_record(DL_PROFILE_LIGHTING,mark);
        mark=dl_profile_mark();
        int texture=mesh->uvs&&instance->texture>=0&&instance->texture<g->level->texture_count?instance->texture:-1;
        if(texture!=bound_texture){
            rdpq_mode_combiner(texture>=0?RDPQ_COMBINER_TEX_SHADE:RDPQ_COMBINER_SHADE);
            if(texture>=0){
                rdpq_sprite_upload(TILE0,textures[texture],&(rdpq_texparms_t){.s.repeats=REPEAT_INFINITE,.t.repeats=REPEAT_INFINITE});
                rdpq_mode_filter(FILTER_BILINEAR);++texture_uploads;
            }
            bound_texture=texture;
        }
        t3d_segment_set(T3D_SEGMENT_2,model->matrices[frame]);
        for(int side=0;side<(instance->double_sided?2:1);++side){
            t3d_state_set_drawflags(T3D_FLAG_SHADED|T3D_FLAG_DEPTH|(texture>=0?T3D_FLAG_TEXTURED:0)|(side?T3D_FLAG_CULL_FRONT:T3D_FLAG_CULL_BACK));
            t3d_segment_set(T3D_SEGMENT_1,model->vertices[frame][side]);rspq_block_run(model->commands);
        }
        submitted_triangles+=mesh->index_count/3;
        if(mesh->animation&&instance->role==DL_MODEL_GUARD)++drawn;
        dl_profile_record(DL_PROFILE_TRIANGLES,mark);
    }
    cached_door=g->door_open;
    mark=dl_profile_mark();t3d_tri_sync();gpu_fences[frame]=rspq_syncpoint_new();
    rdpq_set_scissor(0,0,SCREEN_W,SCREEN_H);rdpq_set_mode_fill(rgb(0,0,0));
    dl_profile_record(DL_PROFILE_TRIANGLES,mark);
    /* RSP submission counts are candidates, not post-clip/pixel counters.
     * Visibility is independently checked by framebuffer captures. */
    dl_profile_workload(animated,full,drawn,submitted_triangles,overlays);
}
