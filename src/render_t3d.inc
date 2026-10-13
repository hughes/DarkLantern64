/* Private RSP backend. Scene-bank addresses are never resource identities.
 * Only current and candidate working views retain packed geometry. */
enum { GPU_FRAMES=3, GPU_PACK_VERTICES=32, GPU_COPY_BYTES=1024, GPU_COMMAND_INDICES=96 };
static const float gpu_units=64.0f;
typedef struct {
    uint64_t stream_key;
    const void *vertices,*indices,*uvs,*normals,*animation;
    int vertex_count,index_count;
    bool has_uvs,has_normals;
} GpuMeshKey;
typedef struct {
    GpuMeshKey mesh;
    int texture_width,texture_height;
    bool double_sided;
} GpuKey;
typedef struct {
    DlRenderBatches batches;
    uint16_t *normal_groups,*representatives;
    DlLightingNormalGroup *normal_data;
    int normal_count;
    float packed_units;
    GpuMeshKey key;
    unsigned references;
    DlVec3 minimum,maximum;
    bool ready,accounted;
} GpuMesh;
typedef struct {
    GpuMesh *mesh;
    T3DVertPacked *vertices[GPU_FRAMES][2];
    T3DMat4FP *matrices[GPU_FRAMES];
    rspq_block_t **commands;
    unsigned command_count,commands_written;
    int color_state[GPU_FRAMES];
    bool color_fog[GPU_FRAMES];
    DlRenderPoseCache transform;
} GpuModel;
typedef struct {
    GpuModel model;
    GpuKey key;
    bool allocated,ready,current,pending;
} GpuResource;
static GpuResource gpu_resources[MAX_MODELS];
static GpuModel *gpu_models[MAX_MODELS],*gpu_pending_models[MAX_MODELS];
static unsigned gpu_model_count,gpu_pending_count;
static const DlLevel *gpu_pending_level;
static bool gpu_preparing,gpu_initial_prepare;
static unsigned gpu_prepare_index,gpu_reused,gpu_built,gpu_step_calls;
static uint32_t gpu_step_max;
static size_t gpu_peak_bytes;
static T3DViewport gpu_viewports[GPU_FRAMES];
static rspq_syncpoint_t gpu_fences[GPU_FRAMES];
static unsigned gpu_frame;
static union { DlLightingPair pairs[MAX_MESH_VERTICES];uint32_t words[MAX_MESH_VERTICES][2]; } gpu_colors;
#define gpu_group_colors gpu_colors.pairs
_Static_assert(sizeof(DlLightingColor)==4&&sizeof(DlLightingPair)==8,"RSP color word layout");
static DlAnimMatrix gpu_world_palette[DL_ANIMATION_MAX_BONES];
static float gpu_vertex_fog[MAX_MESH_VERTICES];
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
static void gpu_update_pose(const DlGame *g,int index,bool dynamic){
    const DlModelInstance *instance=&g->level->models[index];
    DlRenderPoseCache *transform=&gpu_models[index]->transform;
    const DlVec3 *position=&instance->position,*rotation=&instance->rotation;
    DlVec3 moving_position,moving_rotation;
    if(dynamic){model_placement(g,index,&moving_position,&moving_rotation);position=&moving_position;rotation=&moving_rotation;}
    ModelCache *cache=&models[index];
    if(dl_render_pose_cache_update(transform,dynamic,position,rotation,&instance->scale,
        dl_game_model_open(g,instance),cache->hinge_x,cache->hinge_z))
        cache->bounds_center=transform_scaled_point(cache->scaled_center,cache,&transform->pose);
}
static GpuKey gpu_key(const DlGame *g,unsigned index){
    const DlModelInstance *instance=&g->level->models[index];
    assertf(instance->mesh<g->level->mesh_count,"Invalid staged RSP mesh index");
    const DlMesh *mesh=&g->level->meshes[instance->mesh];
    assertf(mesh->vertex_count>0&&mesh->vertex_count<=MAX_MESH_VERTICES&&mesh->index_count>=0&&mesh->index_count%3==0,
        "Invalid staged RSP geometry");
    GpuKey key={.mesh={.stream_key=mesh->stream_key,.vertex_count=mesh->vertex_count,.index_count=mesh->index_count,
        .animation=mesh->animation,.has_uvs=mesh->uvs!=NULL,.has_normals=mesh->normals!=NULL},.double_sided=instance->double_sided};
    if(!mesh->stream_key){key.mesh.vertices=mesh->vertices;key.mesh.indices=mesh->indices;key.mesh.uvs=mesh->uvs;key.mesh.normals=mesh->normals;}
    if(mesh->uvs&&instance->texture>=0){
        assertf(instance->texture<g->level->texture_count,"Invalid staged RSP texture index");
        key.texture_width=g->level->textures[instance->texture].width;
        key.texture_height=g->level->textures[instance->texture].height;
    }
    return key;
}
static bool gpu_mesh_key_equal(const GpuMeshKey *a,const GpuMeshKey *b){
    return a->stream_key==b->stream_key&&a->vertices==b->vertices&&a->indices==b->indices&&a->uvs==b->uvs&&
        a->normals==b->normals&&a->animation==b->animation&&a->vertex_count==b->vertex_count&&
        a->index_count==b->index_count&&a->has_uvs==b->has_uvs&&a->has_normals==b->has_normals;
}
static bool gpu_key_equal(const GpuKey *a,const GpuKey *b){
    return gpu_mesh_key_equal(&a->mesh,&b->mesh)&&a->texture_width==b->texture_width&&
        a->texture_height==b->texture_height&&a->double_sided==b->double_sided;
}
static void gpu_free_resource(GpuResource *entry){
    GpuModel *model=&entry->model;
    for(unsigned i=0;i<model->commands_written;++i)rspq_block_free(model->commands[i]);
    free(model->commands);
    for(unsigned frame=0;frame<GPU_FRAMES;++frame){
        free(model->matrices[frame]);
        for(unsigned side=0;side<2;++side)free(model->vertices[frame][side]);
    }
    if(model->mesh&&!--model->mesh->references){
        GpuMesh *shared=model->mesh;
        dl_render_batches_free(&shared->batches);free(shared->normal_groups);free(shared->representatives);
        free(shared->normal_data);free(shared);
    }
    memset(entry,0,sizeof *entry);
}
/* One job owns its scratch; its candidate mesh remains immutable until commit.
 * The current scene's CPU model/lighting arrays are never touched by these jobs. */
static struct {
    unsigned stage,extent_vertex,uv_batch,allocation,pack_vertex,copy_buffer,copy_offset,command_batch,command_index;
    float extent;
    int16_t *uvs;
} gpu_job;
static void gpu_cancel_pending(void){
    free(gpu_job.uvs);memset(&gpu_job,0,sizeof gpu_job);
    for(unsigned i=0;i<MAX_MODELS;++i){
        gpu_resources[i].pending=false;
        if(gpu_resources[i].allocated&&!gpu_resources[i].current)gpu_free_resource(&gpu_resources[i]);
    }
    memset(gpu_pending_models,0,sizeof gpu_pending_models);gpu_pending_count=0;gpu_pending_level=NULL;gpu_preparing=false;
}
static void gpu_wait_fences(void){
    /* RSP completion is sufficient for packed vertices, palettes and command
     * blocks. RDP texture ownership is separately handled by render.c. */
    for(unsigned frame=0;frame<GPU_FRAMES;++frame)
        if(gpu_fences[frame])rspq_syncpoint_wait(gpu_fences[frame]);
}
static void gpu_release(void){
    gpu_wait_fences();gpu_cancel_pending();
    for(unsigned i=0;i<MAX_MODELS;++i)if(gpu_resources[i].allocated)gpu_free_resource(&gpu_resources[i]);
    memset(gpu_models,0,sizeof gpu_models);gpu_model_count=0;
    memset(gpu_fences,0,sizeof gpu_fences);gpu_frame=0;gpu_peak_bytes=0;
}
static size_t gpu_data_bytes(void){
    size_t bytes=0;
    for(unsigned i=0;i<MAX_MODELS;++i)if(gpu_resources[i].model.mesh)gpu_resources[i].model.mesh->accounted=false;
    for(unsigned i=0;i<MAX_MODELS;++i){
        const GpuResource *entry=&gpu_resources[i];if(!entry->allocated)continue;
        const GpuModel *model=&entry->model;GpuMesh *shared=model->mesh;
        if(shared){
            if(!shared->accounted)bytes+=sizeof *shared+shared->batches.allocated_bytes+
                (shared->normal_groups?entry->key.mesh.vertex_count*sizeof(uint16_t)*2:0)+shared->normal_count*sizeof(DlLightingNormalGroup);
            shared->accounted=true;
            unsigned vertex_bytes=shared->batches.vertex_count/2*sizeof(T3DVertPacked);
            unsigned bones=entry->key.mesh.animation?((const DlAnimationAsset*)entry->key.mesh.animation)->bone_count:1;
            for(unsigned frame=0;frame<GPU_FRAMES;++frame){
                if(model->matrices[frame])bytes+=bones*sizeof(T3DMat4FP);
                for(unsigned side=0;side<2;++side)if(model->vertices[frame][side])bytes+=vertex_bytes;
            }
        }
        if(model->commands)bytes+=model->command_count*sizeof *model->commands;
    }
    return bytes;
}
static void gpu_prepare_begin(const DlGame *g){
    assertf(!gpu_preparing&&g->level->model_count>=0&&g->level->model_count<=MAX_MODELS,"Invalid overlapping RSP preparation");
    gpu_pending_level=g->level;gpu_pending_count=(unsigned)g->level->model_count;
    gpu_prepare_index=gpu_reused=gpu_built=gpu_step_calls=0;gpu_step_max=0;gpu_preparing=true;
    memset(&gpu_job,0,sizeof gpu_job);memset(gpu_pending_models,0,sizeof gpu_pending_models);
    for(unsigned i=0;i<MAX_MODELS;++i)gpu_resources[i].pending=false;
    for(unsigned i=0;i<gpu_pending_count;++i){
        GpuKey key=gpu_key(g,i);
        for(unsigned j=0;j<MAX_MODELS;++j)if(gpu_resources[j].allocated&&gpu_resources[j].ready&&
            !gpu_resources[j].pending&&gpu_key_equal(&key,&gpu_resources[j].key)){
            gpu_resources[j].pending=true;gpu_pending_models[i]=&gpu_resources[j].model;++gpu_reused;break;
        }
        if(gpu_pending_models[i])continue;
        GpuResource *entry=NULL;
        for(unsigned j=0;j<MAX_MODELS&&!entry;++j)if(!gpu_resources[j].allocated)entry=&gpu_resources[j];
        assertf(entry,"RSP current/candidate resource union exceeds %d models",MAX_MODELS);
        entry->allocated=entry->pending=true;entry->key=key;gpu_pending_models[i]=&entry->model;++gpu_built;
    }
    unsigned union_vertices=0;
    for(unsigned i=0;i<MAX_MODELS;++i)if(gpu_resources[i].allocated)union_vertices+=(unsigned)gpu_resources[i].key.mesh.vertex_count;
    assertf(union_vertices<=MAX_VERTICES,"RSP current/candidate vertex union exceeds %d",MAX_VERTICES);
}
static GpuResource *gpu_pending_resource(unsigned index){
    GpuModel *model=gpu_pending_models[index];
    for(unsigned i=0;i<MAX_MODELS;++i)if(&gpu_resources[i].model==model)return &gpu_resources[i];
    assertf(false,"Invalid pending RSP resource");return NULL;
}
static bool gpu_pending_bounds(unsigned index,DlVec3 *minimum,DlVec3 *maximum){
    if(index>=gpu_pending_count)return false;
    GpuResource *resource=gpu_pending_resource(index);
    if(!resource->ready||!resource->model.mesh||!resource->model.mesh->ready)return false;
    *minimum=resource->model.mesh->minimum;*maximum=resource->model.mesh->maximum;return true;
}
static void gpu_job_quantum(const DlGame *g,GpuResource *entry,unsigned index){
    GpuModel *model=&entry->model;
    const DlMesh *mesh=&g->level->meshes[g->level->models[index].mesh];
    if(gpu_job.stage==0){
        for(unsigned i=0;i<MAX_MODELS;++i){
            GpuMesh *shared=gpu_resources[i].model.mesh;
            if(gpu_resources[i].allocated&&shared&&shared->ready&&gpu_mesh_key_equal(&shared->key,&entry->key.mesh)){
                model->mesh=shared;++shared->references;gpu_job.stage=4;return;
            }
        }
        model->mesh=calloc(1,sizeof *model->mesh);assertf(model->mesh,"Cannot allocate staged RSP mesh");
        model->mesh->references=1;model->mesh->key=entry->key.mesh;
        assertf(dl_render_batches_build(mesh,&model->mesh->batches),"Cannot batch staged RSP mesh");
        gpu_job.stage=1;return;
    }
    GpuMesh *shared=model->mesh;const DlRenderBatches *batches=&shared->batches;
    unsigned vertex_bytes=batches->vertex_count/2*sizeof(T3DVertPacked);
    unsigned sides=entry->key.double_sided?2:1;
    if(gpu_job.stage==1){
        if(mesh->animation&&mesh->normals){
            shared->normal_groups=malloc(mesh->vertex_count*sizeof(uint16_t));
            shared->representatives=malloc(mesh->vertex_count*sizeof(uint16_t));
            assertf(shared->normal_groups&&shared->representatives,"Cannot allocate staged RSP normals");
            shared->normal_count=dl_lighting_build_groups(mesh,shared->normal_groups,shared->representatives,mesh->vertex_count);
            assertf(shared->normal_count>0,"Invalid staged RSP normal groups");
        }
        gpu_job.stage=2;return;
    }
    if(gpu_job.stage==2){
        if(shared->normal_count){
            shared->normal_data=malloc(shared->normal_count*sizeof(DlLightingNormalGroup));
            assertf(shared->normal_data&&dl_lighting_prepare_normals(mesh,shared->representatives,shared->normal_count,shared->normal_data),
                "Cannot prepare staged RSP normals");
        }
        gpu_job.stage=3;return;
    }
    if(gpu_job.stage==3){
        unsigned end=gpu_job.extent_vertex+GPU_PACK_VERTICES;
        if(end>(unsigned)mesh->vertex_count)end=(unsigned)mesh->vertex_count;
        for(;gpu_job.extent_vertex<end;++gpu_job.extent_vertex){DlVec3 p=mesh->vertices[gpu_job.extent_vertex];
            assertf(isfinite(p.x)&&isfinite(p.y)&&isfinite(p.z),"Nonfinite staged RSP position");
            if(!gpu_job.extent_vertex)shared->minimum=shared->maximum=p;
            if(p.x<shared->minimum.x)shared->minimum.x=p.x;
            if(p.y<shared->minimum.y)shared->minimum.y=p.y;
            if(p.z<shared->minimum.z)shared->minimum.z=p.z;
            if(p.x>shared->maximum.x)shared->maximum.x=p.x;
            if(p.y>shared->maximum.y)shared->maximum.y=p.y;
            if(p.z>shared->maximum.z)shared->maximum.z=p.z;
            float x=fabsf(p.x),y=fabsf(p.y),z=fabsf(p.z);
            if(x>gpu_job.extent)gpu_job.extent=x;
            if(y>gpu_job.extent)gpu_job.extent=y;
            if(z>gpu_job.extent)gpu_job.extent=z;
        }
        if(end<(unsigned)mesh->vertex_count)return;
        assertf(isfinite(gpu_job.extent),"Nonfinite staged RSP position");
        shared->packed_units=1024;while(gpu_job.extent*shared->packed_units>32760)shared->packed_units*=.5f;
        shared->ready=true;gpu_job.stage=4;return;
    }
    if(gpu_job.stage==4){
        if(entry->key.texture_width){
            if(!gpu_job.uvs){gpu_job.uvs=malloc(batches->vertex_count*2*sizeof(int16_t));assertf(gpu_job.uvs,"Cannot allocate staged RSP UV scratch");}
            if(gpu_job.uv_batch<batches->batch_count){
                DlRenderBatch batch=batches->batches[gpu_job.uv_batch];unsigned offset=batch.vertex_offset;batch.vertex_offset=0;
                DlRenderBatches one={.batches=&batch,.vertices=batches->vertices+offset,.batch_count=1,.vertex_count=batch.vertex_count};
                DlTexturePackError error;
                bool valid=dl_render_texture_pack_ex(mesh,&one,entry->key.texture_width,entry->key.texture_height,gpu_job.uvs+offset*2,&error);
                assertf(valid,"RSP texture packing model=%u batch=%u: %s; split excessive UV spans",index,gpu_job.uv_batch,dl_render_texture_pack_status_name(error.status));
                ++gpu_job.uv_batch;return;
            }
        }
        gpu_job.stage=5;return;
    }
    if(gpu_job.stage==5){
        unsigned frame=gpu_job.allocation/(sides+1),part=gpu_job.allocation%(sides+1);
        if(!part){
            unsigned bones=mesh->animation?mesh->animation->bone_count:1;
            model->matrices[frame]=memalign(16,bones*sizeof(T3DMat4FP));assertf(model->matrices[frame],"Cannot allocate staged RSP palette");
            model->color_state[frame]=-1;
        }else{
            model->vertices[frame][part-1]=memalign(16,vertex_bytes);assertf(model->vertices[frame][part-1],"Cannot allocate staged RSP vertices");
        }
        if(++gpu_job.allocation==GPU_FRAMES*(sides+1))gpu_job.stage=6;
        return;
    }
    if(gpu_job.stage==6){
        unsigned end=gpu_job.pack_vertex+GPU_PACK_VERTICES;if(end>batches->vertex_count)end=batches->vertex_count;
        for(;gpu_job.pack_vertex<end;++gpu_job.pack_vertex){
            unsigned v=gpu_job.pack_vertex;T3DVertPacked *pair=&model->vertices[0][0][v/2];
            if(!(v&1))memset(pair,0,sizeof *pair);
            DlVec3 p=mesh->vertices[batches->vertices[v].source_vertex];int16_t *position=v&1?pair->posB:pair->posA;
            position[0]=(int16_t)roundf(p.x*shared->packed_units);position[1]=(int16_t)roundf(p.y*shared->packed_units);position[2]=(int16_t)roundf(p.z*shared->packed_units);
            if(gpu_job.uvs){int16_t *uv=v&1?pair->stB:pair->stA;uv[0]=gpu_job.uvs[v*2];uv[1]=gpu_job.uvs[v*2+1];}
        }
        if(end==batches->vertex_count){gpu_job.stage=7;gpu_job.copy_buffer=1;}return;
    }
    if(gpu_job.stage==7){
        if(gpu_job.copy_buffer<GPU_FRAMES*sides){
            unsigned frame=gpu_job.copy_buffer/sides,side=gpu_job.copy_buffer%sides;
            unsigned bytes=vertex_bytes-gpu_job.copy_offset;if(bytes>GPU_COPY_BYTES)bytes=GPU_COPY_BYTES;
            memcpy((uint8_t*)model->vertices[frame][side]+gpu_job.copy_offset,(uint8_t*)model->vertices[0][0]+gpu_job.copy_offset,bytes);
            gpu_job.copy_offset+=bytes;if(gpu_job.copy_offset==vertex_bytes){gpu_job.copy_offset=0;++gpu_job.copy_buffer;}return;
        }
        for(unsigned b=0;b<batches->batch_count;++b)model->command_count+=(batches->batches[b].index_count+GPU_COMMAND_INDICES-1)/GPU_COMMAND_INDICES;
        model->commands=calloc(model->command_count,sizeof *model->commands);assertf(model->commands||!model->command_count,"Cannot allocate staged RSP command table");
        gpu_job.stage=8;return;
    }
    if(gpu_job.stage==8){
        if(gpu_job.command_batch<batches->batch_count){
            const DlRenderBatch *batch=&batches->batches[gpu_job.command_batch];
            unsigned end=gpu_job.command_index+GPU_COMMAND_INDICES;if(end>batch->index_count)end=batch->index_count;
            rspq_block_begin();
            if(!gpu_job.command_index)for(unsigned n=0;n<batch->group_count;++n){
                const DlRenderBatchGroup *group=&batches->groups[batch->group_offset+n];
                t3d_matrix_set(t3d_segment_address(T3D_SEGMENT_2,(void*)(group->bone*sizeof(T3DMat4FP))),false);
                t3d_vert_load(t3d_segment_address(T3D_SEGMENT_1,(void*)((batch->vertex_offset+group->vertex_start)/2*sizeof(T3DVertPacked))),group->vertex_start,group->vertex_count);
            }
            for(unsigned n=gpu_job.command_index;n<end;n+=3){const uint8_t *idx=&batches->indices[batch->index_offset+n];t3d_tri_draw(idx[0],idx[1],idx[2]);}
            t3d_tri_sync();model->commands[model->commands_written++]=rspq_block_end();
            gpu_job.command_index=end;if(end==batch->index_count){gpu_job.command_index=0;++gpu_job.command_batch;}return;
        }
        free(gpu_job.uvs);memset(&gpu_job,0,sizeof gpu_job);entry->ready=true;
    }
}
static bool gpu_prepare_step(const DlGame *g,uint32_t tick_budget){
    assertf(gpu_preparing&&g->level==gpu_pending_level,"Wrong staged RSP scene");
    uint32_t start=TICKS_READ();
    do{
        while(gpu_prepare_index<gpu_pending_count&&gpu_pending_resource(gpu_prepare_index)->ready)++gpu_prepare_index;
        if(gpu_prepare_index==gpu_pending_count)break;
        /* The ordinary frame loop primes music before each bounded job.
         * Synchronous initial loading still needs its own polling. */
        if(gpu_initial_prepare)prepare_audio(g);
        gpu_job_quantum(g,gpu_pending_resource(gpu_prepare_index),gpu_prepare_index);
    }while((uint32_t)(TICKS_READ()-start)<tick_budget);
    while(gpu_prepare_index<gpu_pending_count&&gpu_pending_resource(gpu_prepare_index)->ready)++gpu_prepare_index;
    uint32_t elapsed=TICKS_READ()-start;if(elapsed>gpu_step_max)gpu_step_max=elapsed;++gpu_step_calls;
    return gpu_prepare_index==gpu_pending_count;
}
static void gpu_prepare_commit(const DlGame *g){
    assertf(gpu_preparing&&g->level==gpu_pending_level&&gpu_prepare_index==gpu_pending_count,"Incomplete staged RSP publication");
    size_t union_bytes=gpu_data_bytes();if(union_bytes>gpu_peak_bytes)gpu_peak_bytes=union_bytes;
    gpu_wait_fences();
    for(unsigned i=0;i<MAX_MODELS;++i)gpu_resources[i].current=false;
    memcpy(gpu_models,gpu_pending_models,sizeof gpu_models);gpu_model_count=gpu_pending_count;
    unsigned retired=0,batches=0,loads=0;
    for(unsigned i=0;i<MAX_MODELS;++i){GpuResource *entry=&gpu_resources[i];
        if(entry->pending){entry->current=true;entry->pending=false;entry->model.transform.valid=false;
            for(unsigned frame=0;frame<GPU_FRAMES;++frame){entry->model.color_state[frame]=-1;entry->model.color_fog[frame]=false;}}
        else if(entry->allocated){gpu_free_resource(entry);++retired;}
    }
    for(unsigned i=0;i<gpu_model_count;++i){const DlModelInstance *instance=&g->level->models[i];const DlMesh *mesh=&g->level->meshes[instance->mesh];
        gpu_update_pose(g,(int)i,instance->role==DL_MODEL_GUARD||instance->role==DL_MODEL_OBJECTIVE||mesh->animation);
        batches+=gpu_models[i]->mesh->batches.batch_count;loads+=gpu_models[i]->mesh->batches.vertex_count;}
    size_t bytes=gpu_data_bytes();
    debugf("DL64 rsp_cache reused=%u built=%u retired=%u models=%u data_bytes=%lu peak_data_bytes=%lu step_calls=%u max_step_ticks=%lu ticks_per_second=%lu scope=packed_data_excluding_rsp_blocks\n",
        gpu_reused,gpu_built,retired,gpu_model_count,(unsigned long)bytes,(unsigned long)gpu_peak_bytes,gpu_step_calls,(unsigned long)gpu_step_max,(unsigned long)TICKS_PER_SECOND);
    debugf("DL64 rsp_ready backend=tiny3d buffers=%d cache_vertices=70 batches=%u vertex_loads=%u data_bytes=%lu units_per_meter=64 pose_cache_bytes=%u\n",
        GPU_FRAMES,batches,loads,(unsigned long)bytes,(unsigned)(sizeof(DlRenderPoseCache)*MAX_MODELS));
    memset(gpu_pending_models,0,sizeof gpu_pending_models);gpu_pending_count=0;gpu_pending_level=NULL;gpu_preparing=false;
}
static void gpu_prepare(const DlGame *g){
    gpu_initial_prepare=true;
    gpu_prepare_begin(g);while(!gpu_prepare_step(g,TICKS_PER_SECOND)){}gpu_prepare_commit(g);
    gpu_initial_prepare=false;
}
static void gpu_actor_colors(const DlGame *g,int index,const ModelPose *pose){
    DlProfileMark mark=dl_profile_mark();
    const DlModelInstance *model=&g->level->models[index];
    const DlMesh *mesh=&g->level->meshes[model->mesh];
    const GpuMesh *shared=gpu_models[index]->mesh;
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
    const ModelCache *cache=&models[index];GpuModel *model=gpu_models[index];
    const DlRenderBatches *batches=&model->mesh->batches;
    if(fog){
        /* Preserve scalar scenes' fog-before-texture and 80% cap. Only models
         * intersecting the fog range need this compatibility work. A future
         * RSP RGB-fog vertex effect can remove these distant depth queries. */
        for(int v=0;v<mesh->vertex_count;++v){
            DlVec3 p=grouped?dl_render_matrix_point(&gpu_world_palette[mesh->animation->vertex_bones[v]],mesh->vertices[v]):
                mesh->stream_key?add(mesh->vertices[v],instance->position):world_vertices[cache->vertex_start+v];
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
                int corner=0;while(corner<2&&mesh->indices[ref.triangle_index*3+corner]!=ref.source_vertex)++corner;
                if(inline_lighting(g,index)){
                    const uint8_t *rgba=instance->baked_colors[g->door_open]+ref.triangle_index*24+side*12+corner*4;
                    color=(color_t){rgba[0],rgba[1],rgba[2],rgba[3]};
                }else{
                    const NightColors *colors=&night_colors[g->door_open][cache->triangle_start+ref.triangle_index];
                    color=side?colors->back[corner]:colors->front[corner];
                }
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
    model->color_state[frame]=(int)dl_game_lighting_revision(g);
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
    bool lighting_changed=cached_door!=dl_game_lighting_revision(g);
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
        ModelCache *cache=&models[i];GpuModel *model=gpu_models[i];
        bool dynamic=instance->role==DL_MODEL_GUARD||instance->role==DL_MODEL_OBJECTIVE||mesh->animation;
        mark=dl_profile_mark();gpu_update_pose(g,i,dynamic);
        const ModelPose *pose=&model->transform.pose;
        DlVec3 rel=sub(cache->bounds_center,camera.eye);
        DlVec3 center={dot(rel,camera.right),dot(rel,camera.up),dot(rel,camera.forward)};
#ifdef DL_DISABLE_MODEL_CULL
        cache->visible=dl_game_model_visible(g,instance);
#else
        cache->visible=dl_game_model_visible(g,instance)&&dl_render_sphere_visible(&frustum,center,cache->bounds_radius);
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
            const DlAnimMatrix *world=&model->transform.world;
            int count=mesh->animation?mesh->animation->bone_count:1;
            for(int b=0;b<count;++b){
                DlAnimMatrix joint=*world,view;T3DMat4 matrix;
                if(mesh->animation)dl_render_affine_compose(&joint,world,&animation_skin[b]);
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
            transform_model_vertices(g,i,pose);
        dl_profile_record(DL_PROFILE_TRANSFORMS,mark);
        mark=dl_profile_mark();
        if(!grouped&&((dynamic&&cache->visible)||(!dynamic&&lighting_changed)))
            light_model(g,i,dynamic||!g->level->environment.enabled||multiple_doors);
        if(!cache->visible){dl_profile_record(DL_PROFILE_LIGHTING,mark);continue;}
        if(grouped)gpu_actor_colors(g,i,pose);
        float highlight=instance->loot_highlight?dl_loot_highlight_lift(g->elapsed,sqrtf(dot(rel,rel)),(float)i*0.73f):0;
        bool fog=center.z+cache->bounds_radius>(g->level->environment.enabled?g->level->environment.fog_near:8);
        if(dynamic||instance->loot_highlight||fog||model->color_fog[frame]||model->color_state[frame]!=(int)dl_game_lighting_revision(g))
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
            t3d_segment_set(T3D_SEGMENT_1,model->vertices[frame][side]);
            for(unsigned command=0;command<model->command_count;++command)rspq_block_run(model->commands[command]);
        }
        submitted_triangles+=mesh->index_count/3;
        if(mesh->animation&&instance->role==DL_MODEL_GUARD)++drawn;
        dl_profile_record(DL_PROFILE_TRIANGLES,mark);
    }
    cached_door=dl_game_lighting_revision(g);
    mark=dl_profile_mark();t3d_tri_sync();gpu_fences[frame]=rspq_syncpoint_new();
    rdpq_set_scissor(0,0,SCREEN_W,SCREEN_H);rdpq_set_mode_fill(rgb(0,0,0));
    dl_profile_record(DL_PROFILE_TRIANGLES,mark);
    /* RSP submission counts are candidates, not post-clip/pixel counters.
     * Visibility is independently checked by framebuffer captures. */
    dl_profile_workload(animated,full,drawn,submitted_triangles,overlays);
}
