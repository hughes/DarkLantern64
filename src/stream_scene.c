#include "stream_scene.h"
#include <math.h>
#include <string.h>
static const float approach_distance=5.f;

static bool valid(const DlStreamScene *scene){
    return scene&&scene->bootstrap&&scene->bootstrap->stream&&scene->mission&&scene->mission->directory;
}
static DlVec3 vec(DlMissionVec3 p){return (DlVec3){p.x,p.y,p.z};}
static bool finite_vec(DlVec3 p){return isfinite(p.x)&&isfinite(p.y)&&isfinite(p.z);}
static bool contains(const DlMissionRegion *region,DlVec3 p){
    return p.x>=region->minimum.x&&p.x<=region->maximum.x&&
        p.y>=region->minimum.y&&p.y<=region->maximum.y&&p.z>=region->minimum.z&&p.z<=region->maximum.z;
}
static bool contains_bounds(DlVec3 minimum,DlVec3 maximum,DlVec3 p){
    return p.x>=minimum.x&&p.x<=maximum.x&&p.y>=minimum.y&&p.y<=maximum.y&&p.z>=minimum.z&&p.z<=maximum.z;
}
static bool ordered_bounds(DlVec3 minimum,DlVec3 maximum){
    return finite_vec(minimum)&&finite_vec(maximum)&&minimum.x<maximum.x&&minimum.y<maximum.y&&minimum.z<maximum.z;
}
static unsigned mask_count(uint32_t mask){unsigned count=0;while(mask){mask&=mask-1;++count;}return count;}
static uint32_t all_regions(uint32_t count){return count==32?UINT32_MAX:(UINT32_C(1)<<count)-1u;}
static bool region_contains(const DlStreamScene *scene,uint32_t id,DlVec3 position){
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    if(definition->policy==DL_STREAM_POLICY_AUTHORED){
        const DlStreamPolicyRegion *row=&definition->policy_regions[id-1];
        return contains_bounds(row->activation_minimum,row->activation_maximum,position);
    }
    DlMissionRegion region;
    return dl_mission_region(scene->mission,id,&region)==DL_MISSION_OK&&contains(&region,position);
}
static bool bank_resident(const DlStreamSceneBank *bank,uint32_t id){
    for(uint32_t i=0;i<bank->resident_count;++i)if(bank->resident_ids[i]==id)return true;
    return false;
}
static uint32_t locate(const DlStreamScene *scene,const DlStreamSceneBank *bank,DlVec3 p){
    if(!finite_vec(p))return 0;
    uint32_t first=0;
    for(uint32_t i=1;i<=scene->mission->region_count;++i){
        if(region_contains(scene,i,p)){if(bank_resident(bank,i))return i;if(!first)first=i;}
    }
    return first;
}
static int object_index(const DlStreamScene *scene,uint32_t entity_id){
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    for(int i=0;i<scene->bootstrap->object_count;++i)
        if(definition->object_entity_ids[i]==entity_id)return i;
    return -1;
}
static const DlStreamMaterial *material(const DlStreamScene *scene,uint32_t id){
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    if(id&&id<=definition->material_count&&definition->materials[id-1].id==id)return &definition->materials[id-1];
    for(uint32_t i=0;i<definition->material_count;++i)if(definition->materials[i].id==id)return &definition->materials[i];
    return NULL;
}
static bool links_valid(const DlMission *mission,const uint32_t *links,int count){
    if(count&&!links)return false;
    for(int i=0;i<count;++i){
        DlMissionEntity entity;
        if(dl_mission_entity(mission,links[i],&entity)!=DL_MISSION_OK)return false;
        for(int j=0;j<i;++j)if(links[j]==links[i])return false;
    }
    return true;
}
DlStreamSceneStatus dl_stream_scene_init(DlStreamScene *scene,const DlLevel *bootstrap,const DlMission *mission){
    if(!scene||!bootstrap||!bootstrap->stream||!mission||!mission->directory)return DL_STREAM_SCENE_ARGUMENT;
    const DlStreamDefinition *definition=bootstrap->stream;
    if(!definition->package_path||!*definition->package_path||definition->directory_bytes!=mission->directory_bytes||
        definition->file_bytes!=mission->file_bytes||definition->region_count!=mission->region_count||
        !definition->max_region_bytes||bootstrap->collider_count!=0||bootstrap->floor_count!=0)return DL_STREAM_SCENE_ARGUMENT;
    if(mission->region_count>DL_STREAM_SCENE_MAX_REGIONS||mission->entity_count>DL_STREAM_SCENE_MAX_ENTITIES||
        bootstrap->model_count<0||bootstrap->model_count>DL_MAX_MODELS||
        bootstrap->mesh_count<0||bootstrap->mesh_count>DL_MAX_MODELS||
        bootstrap->object_count<0||bootstrap->object_count>DL_MAX_OBJECTS||
        bootstrap->enemy_count<0||bootstrap->enemy_count>DL_MAX_ENEMIES||
        bootstrap->texture_count<0||bootstrap->texture_count>64||
        bootstrap->light_count<0||bootstrap->light_count>16||
        definition->material_count>DL_STREAM_SCENE_MAX_MATERIALS)return DL_STREAM_SCENE_CAPACITY;
    if(definition->policy!=DL_STREAM_POLICY_LEGACY&&definition->policy!=DL_STREAM_POLICY_AUTHORED)return DL_STREAM_SCENE_ARGUMENT;
    if(definition->max_resident_regions>DL_STREAM_SCENE_MAX_RESIDENT_REGIONS)return DL_STREAM_SCENE_CAPACITY;
    if(definition->policy==DL_STREAM_POLICY_AUTHORED){
        if(!definition->policy_regions||!definition->max_resident_regions||definition->keep_neighbors_resident||
            !definition->transitions||!definition->transition_count||definition->transition_count>256)return DL_STREAM_SCENE_ARGUMENT;
        uint32_t allowed=all_regions(mission->region_count);
        for(uint32_t id=1;id<=mission->region_count;++id){
            const DlStreamPolicyRegion *row=&definition->policy_regions[id-1];
            if(!ordered_bounds(row->activation_minimum,row->activation_maximum)||
                !(row->working_mask&(UINT32_C(1)<<(id-1)))||(row->working_mask&~allowed)||
                mask_count(row->working_mask)>definition->max_resident_regions)return DL_STREAM_SCENE_ARGUMENT;
        }
        for(uint32_t i=0;i<definition->transition_count;++i){
            const DlStreamTransition *gate=&definition->transitions[i];
            if(!gate->from_region||gate->from_region>mission->region_count||!gate->to_region||
                gate->to_region>mission->region_count||gate->from_region==gate->to_region||
                !ordered_bounds(gate->minimum,gate->maximum))return DL_STREAM_SCENE_ARGUMENT;
            if(gate->door_entity_id){DlMissionEntity door;
                if(dl_mission_entity(mission,gate->door_entity_id,&door)!=DL_MISSION_OK||door.kind!=DL_MISSION_ENTITY_DOOR||
                    (door.home_region_id!=gate->from_region&&door.home_region_id!=gate->to_region)||!definition->object_entity_ids||
                    object_index(&(DlStreamScene){.bootstrap=bootstrap},door.id)<0)return DL_STREAM_SCENE_ARGUMENT;}
        }
    }
    if((bootstrap->model_count&&!bootstrap->models)||(bootstrap->mesh_count&&!bootstrap->meshes)||
        (bootstrap->object_count&&!bootstrap->objects)||(bootstrap->enemy_count&&!bootstrap->enemies)||
        (bootstrap->light_count&&!bootstrap->lights)||(bootstrap->texture_count&&!bootstrap->textures)||
        (definition->material_count&&!definition->materials)||
        !links_valid(mission,definition->model_entity_ids,bootstrap->model_count)||
        !links_valid(mission,definition->object_entity_ids,bootstrap->object_count)||
        !links_valid(mission,definition->enemy_entity_ids,bootstrap->enemy_count))return DL_STREAM_SCENE_ARGUMENT;
    for(uint32_t i=1;i<=mission->region_count;++i){
        DlMissionRegion region;
        if(dl_mission_region(mission,i,&region)!=DL_MISSION_OK||region.decoded_bytes>definition->max_region_bytes)
            return DL_STREAM_SCENE_ARGUMENT;
        DlVec3 half={(region.maximum.x-region.minimum.x)*0.5f,
            (region.maximum.y-region.minimum.y)*0.5f,(region.maximum.z-region.minimum.z)*0.5f};
        if(!finite_vec(half))return DL_STREAM_SCENE_CORRUPT;
    }
    for(uint32_t i=0;i<definition->material_count;++i){
        if(!definition->materials[i].id||definition->materials[i].texture_id>bootstrap->texture_count)
            return DL_STREAM_SCENE_ARGUMENT;
        for(uint32_t j=0;j<i;++j)if(definition->materials[j].id==definition->materials[i].id)
            return DL_STREAM_SCENE_ARGUMENT;
    }
    for(int i=0;i<bootstrap->object_count;++i){
        DlMissionEntity entity;dl_mission_entity(mission,definition->object_entity_ids[i],&entity);
        DlMissionEntityKind expected=bootstrap->objects[i].kind==DL_OBJECT_DOOR?DL_MISSION_ENTITY_DOOR:
            bootstrap->objects[i].kind==DL_OBJECT_LOOT?DL_MISSION_ENTITY_LOOT:
            bootstrap->objects[i].kind==DL_OBJECT_OBJECTIVE?DL_MISSION_ENTITY_OBJECTIVE:DL_MISSION_ENTITY_GENERIC;
        if(entity.kind!=expected||!bootstrap->objects[i].id)return DL_STREAM_SCENE_ARGUMENT;
    }
    for(int i=0;i<bootstrap->enemy_count;++i){
        DlMissionEntity entity;dl_mission_entity(mission,definition->enemy_entity_ids[i],&entity);
        if(entity.kind!=DL_MISSION_ENTITY_GUARD)return DL_STREAM_SCENE_ARGUMENT;
    }
    for(int i=0;i<bootstrap->model_count;++i){
        const DlModelInstance *model=&bootstrap->models[i];
        DlMissionEntity entity;dl_mission_entity(mission,definition->model_entity_ids[i],&entity);
        if(model->role<DL_MODEL_STATIC||model->role>DL_MODEL_LOOT||model->mesh>=bootstrap->mesh_count||
            !finite_vec(model->position)||!finite_vec(model->rotation)||
            !finite_vec(model->scale))return DL_STREAM_SCENE_ARGUMENT;
        DlMissionEntityKind expected=model->role==DL_MODEL_GUARD?DL_MISSION_ENTITY_GUARD:
            model->role==DL_MODEL_DOOR?DL_MISSION_ENTITY_DOOR:model->role==DL_MODEL_LOOT?DL_MISSION_ENTITY_LOOT:
            model->role==DL_MODEL_OBJECTIVE?DL_MISSION_ENTITY_OBJECTIVE:DL_MISSION_ENTITY_GENERIC;
        if(entity.kind!=expected)return DL_STREAM_SCENE_ARGUMENT;
        if(model->role==DL_MODEL_GUARD&&(model->enemy_index<0||model->enemy_index>=bootstrap->enemy_count||
            definition->enemy_entity_ids[model->enemy_index]!=entity.id))return DL_STREAM_SCENE_ARGUMENT;
        if(model->role==DL_MODEL_DOOR||model->role==DL_MODEL_CONTROL||model->role==DL_MODEL_LOOT||model->role==DL_MODEL_OBJECTIVE){
            int object=-1;
            for(int j=0;j<bootstrap->object_count;++j)if(definition->object_entity_ids[j]==entity.id)object=j;
            if(object<0||!model->id||strcmp(model->id,bootstrap->objects[object].id))return DL_STREAM_SCENE_ARGUMENT;
        }
        const DlMesh *mesh=&bootstrap->meshes[model->mesh];
        if(mesh->vertex_count<=0||mesh->vertex_count>DL_MAX_MESH_VERTICES||mesh->index_count<=0||
            mesh->index_count%3||!mesh->vertices||!mesh->indices)return DL_STREAM_SCENE_ARGUMENT;
        for(int v=0;v<mesh->vertex_count;++v)if(!finite_vec(mesh->vertices[v]))return DL_STREAM_SCENE_ARGUMENT;
        for(int n=0;n<mesh->index_count;++n)if(mesh->indices[n]>=mesh->vertex_count)return DL_STREAM_SCENE_ARGUMENT;
    }
    memset(scene,0,sizeof *scene);scene->bootstrap=bootstrap;scene->mission=mission;
    /* Init deliberately has no working scene: refresh establishes barriers and
     * valid geometry before the caller starts gameplay. */
    return DL_STREAM_SCENE_OK;
}
static DlStreamSceneStatus add_collider(DlStreamSceneBank *bank,DlCollider box,int object){
    if(bank->level.collider_count+bank->level.floor_count>=DL_STREAM_SCENE_MAX_COLLIDERS)return DL_STREAM_SCENE_CAPACITY;
    int index=bank->level.collider_count++;
    bank->colliders[index]=box;bank->collider_objects[index]=(int16_t)object;
    dl_query_cache_box(&bank->query_cache.boxes[index],&bank->colliders[index]);
    bank->query_cache.collider_count=bank->level.collider_count;return DL_STREAM_SCENE_OK;
}
static int triangle_group(DlStreamSceneBank *bank,uint32_t region,const DlMissionTriangle *triangle){
    int group=bank->last_group;
    if(group>=0&&group<bank->group_count&&bank->groups[group].region_id==region&&
        bank->groups[group].material_id==triangle->material_id&&bank->groups[group].attribute_flags==triangle->attribute_flags)return group;
    group=0;
    while(group<bank->group_count&&(bank->groups[group].region_id!=region||
        bank->groups[group].material_id!=triangle->material_id||bank->groups[group].attribute_flags!=triangle->attribute_flags))++group;
    bank->last_group=group;return group;
}
static DlStreamSceneStatus write_triangle(DlStreamSceneBank *bank,DlStreamSceneGroup *target,const DlMissionTriangle *triangle){
    if(target->written_triangles>=target->triangle_count)return DL_STREAM_SCENE_CORRUPT;
    int local=target->written_triangles++*3;
    if(target->baked)for(int state=0;state<2;++state)
        memcpy(bank->baked_colors[state][target->first_vertex/3+local/3],triangle->baked_colors[state],24);
    for(int v=0;v<3;++v){
        bank->vertices[target->first_vertex+local+v]=vec(triangle->positions[v]);
        bank->indices[target->first_vertex+local+v]=(uint16_t)(local+v);
        bank->uvs[target->first_vertex+local+v]=(DlVec2){triangle->uvs[v*2],triangle->uvs[v*2+1]};
        bank->normals[target->first_vertex+local+v]=(DlNormal){triangle->normals[v*3],triangle->normals[v*3+1],triangle->normals[v*3+2]};
    }
    return DL_STREAM_SCENE_OK;
}
static DlStreamSceneStatus scan_record(DlStreamScene *scene,DlStreamSceneBank *bank,
    const DlMissionChunk *chunk,uint32_t region_id,uint32_t kind,uint32_t index,bool *entities){
    if(kind==DL_MISSION_SECTION_ENTITIES){
        uint32_t id;
        if(dl_mission_entity_id(chunk,scene->mission,region_id,index,&id)!=DL_MISSION_OK||entities[id-1])return DL_STREAM_SCENE_CORRUPT;
        entities[id-1]=true;
    }else if(kind==DL_MISSION_SECTION_TRIANGLES){
        DlMissionTriangle triangle;
        if(dl_mission_triangle(chunk,index,&triangle)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
        const DlStreamMaterial *source=material(scene,triangle.material_id);
        if(!source||(source->texture_id&&!(triangle.attribute_flags&DL_MISSION_TRIANGLE_UV)))return DL_STREAM_SCENE_CORRUPT;
        for(int v=0;v<3;++v){DlVec3 world={triangle.positions[v].x+chunk->origin.x,triangle.positions[v].y+chunk->origin.y,triangle.positions[v].z+chunk->origin.z};
            if(!finite_vec(world))return DL_STREAM_SCENE_CORRUPT;}
        int group=triangle_group(bank,region_id,&triangle);
        if(group==bank->group_count){
            if(group>=DL_STREAM_SCENE_MAX_GROUPS)return DL_STREAM_SCENE_CAPACITY;
            bank->groups[group]=(DlStreamSceneGroup){.region_id=region_id,.material_id=triangle.material_id,
                .first_vertex=bank->triangle_count*3,.origin=vec(chunk->origin),
                .attribute_flags=triangle.attribute_flags,.baked=chunk->version==4};++bank->group_count;
        }
        if(group!=bank->group_count-1)scene->assembly.contiguous_groups=false;
        ++bank->groups[group].triangle_count;++bank->triangle_count;
        /* Cooked groups normally arrive contiguously. Write the exact decoded
         * record while it is validated, avoiding a second decode/copy pass.
         * Borrowed chunks are immutable for the complete assembly lifetime.
         * Interleaved groups retain the general offset/fill path below. */
        if(scene->assembly.contiguous_groups)return write_triangle(bank,&bank->groups[group],&triangle);
    }else if(kind==DL_MISSION_SECTION_COLLIDERS){
        DlMissionCollider collider;if(dl_mission_collider(chunk,scene->mission,index,&collider)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
        DlCollider box={.center={collider.center.x+chunk->origin.x,collider.center.y+chunk->origin.y,collider.center.z+chunk->origin.z},
            .half_size=vec(collider.half_size),.yaw=collider.yaw};
        if(!finite_vec(box.center))return DL_STREAM_SCENE_CORRUPT;
        int object=-1;
        if(collider.entity_id){DlMissionEntity entity;dl_mission_entity(scene->mission,collider.entity_id,&entity);
            if(entity.kind==DL_MISSION_ENTITY_DOOR){object=object_index(scene,entity.id);if(object<0)return DL_STREAM_SCENE_CORRUPT;box.door=true;}}
        return add_collider(bank,box,object);
    }else if(kind==DL_MISSION_SECTION_FLOORS){
        DlMissionFloorTriangle floor;if(dl_mission_floor(chunk,index,&floor)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
        DlFloorTriangle value;
        for(int vertex=0;vertex<3;++vertex){value.vertices[vertex]=(DlVec3){floor.vertices[vertex].x+chunk->origin.x,
            floor.vertices[vertex].y+chunk->origin.y,floor.vertices[vertex].z+chunk->origin.z};
            if(!finite_vec(value.vertices[vertex]))return DL_STREAM_SCENE_CORRUPT;}
        int index=bank->level.floor_count;
        if(!dl_query_cache_floor(&bank->query_cache.floors[index],&value))return DL_STREAM_SCENE_CORRUPT;
        bank->floors[bank->level.floor_count++]=value;bank->query_cache.floor_count=bank->level.floor_count;
    }
    return DL_STREAM_SCENE_OK;
}
static DlStreamSceneStatus fill_record(DlStreamSceneBank *bank,const DlMissionChunk *chunk,uint32_t region_id,uint32_t index){
    DlMissionTriangle triangle;if(dl_mission_triangle(chunk,index,&triangle)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
    int group=triangle_group(bank,region_id,&triangle);
    if(group==bank->group_count)return DL_STREAM_SCENE_CORRUPT;
    return write_triangle(bank,&bank->groups[group],&triangle);
}
static void assembly_release(DlStreamScene *scene){
    DlStreamSceneAssembly *job=&scene->assembly;
    for(size_t i=0;i<job->borrowed;++i)dl_world_stream_release(job->stream,job->handles[i]);
    memset(job,0,sizeof *job);
}
void dl_stream_scene_prepare_cancel(DlStreamScene *scene){
    if(!scene)return;
    assembly_release(scene);scene->pending_ready=false;
}
static bool prepare_arguments(const DlStreamScene *scene,const DlWorldStream *stream,const DlGame *game){
    return valid(scene)&&stream&&stream->mission==scene->mission&&stream->slots&&
        (!game||game->enemy_count==scene->bootstrap->enemy_count);
}
static uint32_t physical_resident_mask(const DlWorldStream *stream){
    uint32_t result=0;
    for(size_t i=0;i<stream->slot_count;++i){const DlRegionSlot *slot=&stream->slots[i];
        if(slot->state==DL_STREAM_RESIDENT&&slot->region_id&&slot->region_id<=32)
            result|=UINT32_C(1)<<(slot->region_id-1);
    }
    return result;
}
DlStreamSceneStatus dl_stream_scene_prepare_begin(DlStreamScene *scene,DlWorldStream *stream,const DlGame *game){
    if(!prepare_arguments(scene,stream,game))return DL_STREAM_SCENE_ARGUMENT;
    return dl_stream_scene_prepare_begin_mask(scene,stream,game,physical_resident_mask(stream));
}
DlStreamSceneStatus dl_stream_scene_prepare_begin_mask(DlStreamScene *scene,DlWorldStream *stream,const DlGame *game,uint32_t resident_mask){
    if(!prepare_arguments(scene,stream,game))return DL_STREAM_SCENE_ARGUMENT;
    if(resident_mask&~all_regions(scene->mission->region_count))return DL_STREAM_SCENE_ARGUMENT;
    if(resident_mask&~physical_resident_mask(stream))return DL_STREAM_SCENE_NOT_READY;
    if(scene->revision==UINT32_MAX)return DL_STREAM_SCENE_CAPACITY;
    if(scene->assembly.active||scene->pending_ready)return DL_STREAM_SCENE_NOT_READY;
    DlStreamSceneBank *bank=&scene->banks[scene->active_bank^1u];
    /* Large geometry arrays are overwritten only where the new counts expose
     * them. Clearing an entire bank here would defeat the incremental API. */
    bank->level=*scene->bootstrap;bank->group_count=bank->triangle_count=bank->last_group=0;bank->resident_count=0;
    bank->level.meshes=bank->meshes;bank->level.models=bank->models;bank->level.model_count=0;
    bank->level.colliders=bank->colliders;bank->level.collider_count=0;bank->level.collider_objects=bank->collider_objects;
    bank->level.floors=bank->floors;bank->level.floor_count=0;bank->level.baked_lighting=NULL;
    bank->query_cache.collider_source=bank->colliders;bank->query_cache.floor_source=bank->floors;
    bank->query_cache.collider_count=bank->query_cache.floor_count=0;bank->query_cache.index_ready=false;bank->level.query_cache=&bank->query_cache;
    if(scene->bootstrap->mesh_count)memcpy(bank->meshes,scene->bootstrap->meshes,(size_t)scene->bootstrap->mesh_count*sizeof(DlMesh));
    DlStreamSceneAssembly *job=&scene->assembly;memset(job,0,sizeof *job);job->stream=stream;job->active=true;job->has_game=game!=NULL;
    job->contiguous_groups=true;
    if(game)for(int i=0;i<game->enemy_count;++i)job->enemy_positions[i]=game->enemies[i].position;
    DlStreamSceneStatus result=DL_STREAM_SCENE_OK;
    for(size_t i=0;i<stream->slot_count;++i){
        const DlRegionSlot *slot=&stream->slots[i];if(slot->state!=DL_STREAM_RESIDENT||!slot->region_id||slot->region_id>32||
            !(resident_mask&(UINT32_C(1)<<(slot->region_id-1))))continue;
        if(job->borrowed==DL_STREAM_SCENE_MAX_RESIDENT_REGIONS){result=DL_STREAM_SCENE_CAPACITY;break;}
        job->handles[job->borrowed]=(DlRegionHandle){(uint32_t)i,slot->generation};
        if(dl_world_stream_borrow(stream,job->handles[job->borrowed],&job->chunks[job->borrowed])!=DL_STREAM_OK){result=DL_STREAM_SCENE_NOT_READY;break;}
        bank->resident_ids[job->borrowed]=slot->region_id;job->resident_mask|=UINT32_C(1)<<(slot->region_id-1);++job->borrowed;
    }
    if(result!=DL_STREAM_SCENE_OK){dl_stream_scene_prepare_cancel(scene);return result;}
    bank->resident_count=(uint32_t)job->borrowed;return DL_STREAM_SCENE_OK;
}
enum { ASSEMBLY_SCAN,ASSEMBLY_COVERAGE,ASSEMBLY_OFFSETS,ASSEMBLY_FILL,ASSEMBLY_BOOTSTRAP,ASSEMBLY_GROUPS,ASSEMBLY_BARRIERS,ASSEMBLY_QUERY,ASSEMBLY_DONE };
static DlStreamSceneStatus assembly_record(DlStreamScene *scene){
    static const DlMissionSectionKind kinds[]={DL_MISSION_SECTION_ENTITIES,DL_MISSION_SECTION_TRIANGLES,
        DL_MISSION_SECTION_COLLIDERS,DL_MISSION_SECTION_FLOORS};
    DlStreamSceneAssembly *job=&scene->assembly;DlStreamSceneBank *bank=&scene->banks[scene->active_bank^1u];
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    /* Empty sections and phase boundaries consume no records, but this loop
     * is bounded by the five borrowed chunks and fixed section count. */
    for(;;){
        if(job->phase==ASSEMBLY_SCAN){
            if(job->chunk==job->borrowed){job->phase=ASSEMBLY_COVERAGE;job->entity=1;continue;}
            if(job->section==4){++job->chunk;job->section=job->record=0;continue;}
            const DlMissionChunk *chunk=job->chunks[job->chunk];DlMissionSection section;
            DlMissionSectionKind kind=kinds[job->section];
            if(dl_mission_section(chunk,kind,&section)!=DL_MISSION_OK||job->record==section.count){++job->section;job->record=0;continue;}
            if(!job->record){
                if(kind==DL_MISSION_SECTION_ENTITIES&&section.count>scene->mission->entity_count)return DL_STREAM_SCENE_CORRUPT;
                if(kind==DL_MISSION_SECTION_TRIANGLES&&section.count>(uint32_t)(DL_STREAM_SCENE_MAX_TRIANGLES-bank->triangle_count))return DL_STREAM_SCENE_CAPACITY;
                if(kind==DL_MISSION_SECTION_COLLIDERS&&section.count>(uint32_t)(DL_STREAM_SCENE_MAX_COLLIDERS-bank->level.collider_count-bank->level.floor_count))return DL_STREAM_SCENE_CAPACITY;
                if(kind==DL_MISSION_SECTION_FLOORS&&(section.count>(uint32_t)(DL_STREAM_SCENE_MAX_FLOORS-bank->level.floor_count)||
                    section.count>(uint32_t)(DL_STREAM_SCENE_MAX_COLLIDERS-bank->level.collider_count-bank->level.floor_count)))return DL_STREAM_SCENE_CAPACITY;
            }
            return scan_record(scene,bank,chunk,bank->resident_ids[job->chunk],kind,job->record++,job->entities);
        }
        if(job->phase==ASSEMBLY_COVERAGE){
            if(job->entity>scene->mission->entity_count){job->phase=ASSEMBLY_OFFSETS;job->index=0;continue;}
            DlMissionEntity entity;uint32_t id=job->entity++;
            if(dl_mission_entity(scene->mission,id,&entity)!=DL_MISSION_OK||
                (bank_resident(bank,entity.home_region_id)&&!job->entities[id-1]))return DL_STREAM_SCENE_CORRUPT;
            return DL_STREAM_SCENE_OK;
        }
        if(job->phase==ASSEMBLY_OFFSETS){
            if(job->index==(uint32_t)bank->group_count){
                job->phase=job->contiguous_groups?ASSEMBLY_BOOTSTRAP:ASSEMBLY_FILL;job->chunk=job->record=job->index=0;
                job->instanced_vertices=job->vertex;job->instanced_triangles=bank->triangle_count;continue;
            }
            DlStreamSceneGroup *group=&bank->groups[job->index++];group->first_vertex=job->vertex;job->vertex+=group->triangle_count*3;
            if(!job->contiguous_groups)group->written_triangles=0;
            return DL_STREAM_SCENE_OK;
        }
        if(job->phase==ASSEMBLY_FILL){
            if(job->chunk==job->borrowed){job->phase=ASSEMBLY_BOOTSTRAP;job->index=0;
                job->instanced_vertices=job->vertex;job->instanced_triangles=bank->triangle_count;continue;}
            const DlMissionChunk *chunk=job->chunks[job->chunk];DlMissionSection section;
            if(dl_mission_section(chunk,DL_MISSION_SECTION_TRIANGLES,&section)!=DL_MISSION_OK||job->record==section.count){++job->chunk;job->record=0;continue;}
            return fill_record(bank,chunk,bank->resident_ids[job->chunk],job->record++);
        }
        if(job->phase==ASSEMBLY_BOOTSTRAP){
            if(job->index==(uint32_t)scene->bootstrap->model_count){job->phase=ASSEMBLY_GROUPS;job->index=0;continue;}
            uint32_t index=job->index++;const DlModelInstance *model=&scene->bootstrap->models[index];DlMissionEntity entity;
            if(dl_mission_entity(scene->mission,definition->model_entity_ids[index],&entity)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
            uint32_t region=entity.home_region_id;
            if(model->role==DL_MODEL_GUARD&&job->has_game)region=locate(scene,bank,job->enemy_positions[model->enemy_index]);
            /* Small shared door leaves preserve their global visual state. */
            if(model->role!=DL_MODEL_DOOR&&!bank_resident(bank,region))return DL_STREAM_SCENE_OK;
            if(bank->level.model_count+bank->group_count>=DL_MAX_MODELS)return DL_STREAM_SCENE_CAPACITY;
            const DlMesh *mesh=&scene->bootstrap->meshes[model->mesh];
            if(mesh->vertex_count>DL_MAX_SCENE_VERTICES-job->instanced_vertices||
                mesh->index_count/3>DL_MAX_SCENE_TRIANGLES-job->instanced_triangles)return DL_STREAM_SCENE_CAPACITY;
            job->instanced_vertices+=mesh->vertex_count;job->instanced_triangles+=mesh->index_count/3;
            bank->models[bank->level.model_count++]=*model;return DL_STREAM_SCENE_OK;
        }
        if(job->phase==ASSEMBLY_GROUPS){
            if(job->index==(uint32_t)bank->group_count){job->phase=ASSEMBLY_BARRIERS;job->index=0;continue;}
            const DlStreamSceneGroup *group=&bank->groups[job->index++];const DlStreamMaterial *source=material(scene,group->material_id);
            if(!source||group->written_triangles!=group->triangle_count)return DL_STREAM_SCENE_CORRUPT;
            int mesh=bank->level.mesh_count++;
            bank->meshes[mesh]=(DlMesh){.vertices=bank->vertices+group->first_vertex,.vertex_count=group->triangle_count*3,
                .indices=bank->indices+group->first_vertex,.index_count=group->triangle_count*3,
                .uvs=group->attribute_flags&DL_MISSION_TRIANGLE_UV?bank->uvs+group->first_vertex:NULL,
                .normals=group->attribute_flags&DL_MISSION_TRIANGLE_NORMAL?bank->normals+group->first_vertex:NULL,
                .stream_key=((uint64_t)group->region_id<<40)|((uint64_t)group->material_id<<8)|group->attribute_flags};
            DlModelInstance *model=&bank->models[bank->level.model_count++];
            *model=(DlModelInstance){.mesh=(uint16_t)mesh,.position=group->origin,.scale={1,1,1},.role=DL_MODEL_STATIC,
                .texture=source->texture_id?(int16_t)(source->texture_id-1):-1,.double_sided=source->double_sided,.enemy_index=-1};
            memcpy(model->color,source->color,4);
            if(group->baked)for(int state=0;state<2;++state)model->baked_colors[state]=bank->baked_colors[state][group->first_vertex/3];
            return DL_STREAM_SCENE_OK;
        }
        if(job->phase==ASSEMBLY_BARRIERS){
            if(definition->policy==DL_STREAM_POLICY_AUTHORED){
                if(job->index==definition->transition_count){dl_query_cache_index_begin(&bank->query_cache);job->phase=ASSEMBLY_QUERY;continue;}
                const DlStreamTransition *gate=&definition->transitions[job->index++];
                if(!bank_resident(bank,gate->from_region)||
                    (job->resident_mask&definition->policy_regions[gate->to_region-1].working_mask)==definition->policy_regions[gate->to_region-1].working_mask)return DL_STREAM_SCENE_OK;
                DlCollider barrier={.center={(gate->minimum.x+gate->maximum.x)*.5f,(gate->minimum.y+gate->maximum.y)*.5f,(gate->minimum.z+gate->maximum.z)*.5f},
                    .half_size={(gate->maximum.x-gate->minimum.x)*.5f,(gate->maximum.y-gate->minimum.y)*.5f,(gate->maximum.z-gate->minimum.z)*.5f}};
                int object=gate->door_entity_id?object_index(scene,gate->door_entity_id):-1;
                if(gate->door_entity_id&&object<0)return DL_STREAM_SCENE_CORRUPT;
                return add_collider(bank,barrier,object);
            }
            if(job->index==scene->mission->region_count){dl_query_cache_index_begin(&bank->query_cache);job->phase=ASSEMBLY_QUERY;continue;}
            uint32_t id=++job->index;if(bank_resident(bank,id))return DL_STREAM_SCENE_OK;
            DlMissionRegion region;if(dl_mission_region(scene->mission,id,&region)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
            DlCollider barrier={.center={region.minimum.x*.5f+region.maximum.x*.5f,region.minimum.y*.5f+region.maximum.y*.5f,region.minimum.z*.5f+region.maximum.z*.5f},
                .half_size={(region.maximum.x-region.minimum.x)*.5f,(region.maximum.y-region.minimum.y)*.5f,(region.maximum.z-region.minimum.z)*.5f}};
            return add_collider(bank,barrier,-1);
        }
        if(job->phase==ASSEMBLY_QUERY){
            if(dl_query_cache_index_step(&bank->query_cache))job->phase=ASSEMBLY_DONE;
            return DL_STREAM_SCENE_OK;
        }
        return DL_STREAM_SCENE_OK;
    }
}
DlStreamSceneStatus dl_stream_scene_prepare_step(DlStreamScene *scene,size_t record_budget,bool *ready){
    if(!valid(scene)||!ready)return DL_STREAM_SCENE_ARGUMENT;
    *ready=scene->pending_ready;
    if(!scene->assembly.active)return scene->pending_ready?DL_STREAM_SCENE_OK:DL_STREAM_SCENE_NOT_READY;
    while(record_budget--){
        DlStreamSceneStatus status=assembly_record(scene);
        if(status!=DL_STREAM_SCENE_OK){dl_stream_scene_prepare_cancel(scene);*ready=false;return status;}
        if(scene->assembly.phase==ASSEMBLY_DONE){assembly_release(scene);scene->pending_ready=*ready=true;break;}
    }
    return DL_STREAM_SCENE_OK;
}
DlStreamSceneStatus dl_stream_scene_prepare(DlStreamScene *scene,DlWorldStream *stream,const DlGame *game){
    if(!prepare_arguments(scene,stream,game))return DL_STREAM_SCENE_ARGUMENT;
    /* The synchronous compatibility API replaces an older unpublished job. */
    dl_stream_scene_prepare_cancel(scene);
    DlStreamSceneStatus result=dl_stream_scene_prepare_begin(scene,stream,game);if(result!=DL_STREAM_SCENE_OK)return result;
    bool ready=false;return dl_stream_scene_prepare_step(scene,SIZE_MAX,&ready);
}
const DlLevel *dl_stream_scene_pending(const DlStreamScene *scene){
    return valid(scene)&&scene->pending_ready?&scene->banks[scene->active_bank^1u].level:NULL;
}
bool dl_stream_scene_pending_guards_match(const DlStreamScene *scene,const DlGame *game){
    if(!dl_stream_scene_pending(scene)||!game||game->enemy_count!=scene->bootstrap->enemy_count)return false;
    const DlStreamSceneBank *bank=&scene->banks[scene->active_bank^1u];
    int expected[DL_MAX_ENEMIES]={0},actual[DL_MAX_ENEMIES]={0};
    for(int i=0;i<scene->bootstrap->model_count;++i){const DlModelInstance *model=&scene->bootstrap->models[i];
        if(model->role==DL_MODEL_GUARD&&bank_resident(bank,locate(scene,bank,game->enemies[model->enemy_index].position)))
            ++expected[model->enemy_index];
    }
    for(int i=0;i<bank->level.model_count;++i){const DlModelInstance *model=&bank->models[i];
        if(model->role==DL_MODEL_GUARD)++actual[model->enemy_index];
    }
    for(int i=0;i<game->enemy_count;++i)if(expected[i]!=actual[i])return false;
    return true;
}
void dl_stream_scene_publish(DlStreamScene *scene){
    if(!valid(scene)||!scene->pending_ready||scene->assembly.active||scene->revision==UINT32_MAX)return;
    scene->active_bank^=1u;++scene->revision;scene->pending_ready=false;
}
DlStreamSceneStatus dl_stream_scene_refresh(DlStreamScene *scene,DlWorldStream *stream,const DlGame *game){
    DlStreamSceneStatus result=dl_stream_scene_prepare(scene,stream,game);
    if(result==DL_STREAM_SCENE_OK)dl_stream_scene_publish(scene);
    return result;
}
const DlLevel *dl_stream_scene_level(const DlStreamScene *scene){
    return valid(scene)&&scene->revision?&scene->banks[scene->active_bank].level:NULL;
}
uint32_t dl_stream_scene_region_at(const DlStreamScene *scene,DlVec3 position){
    return valid(scene)?locate(scene,&scene->banks[scene->active_bank],position):0;
}
uint32_t dl_stream_scene_region_at_preferred(const DlStreamScene *scene,DlVec3 position,uint32_t preferred){
    if(!valid(scene)||!finite_vec(position))return 0;
    if(preferred&&preferred<=scene->mission->region_count&&region_contains(scene,preferred,position))return preferred;
    return locate(scene,&scene->banks[scene->active_bank],position);
}
uint32_t dl_stream_scene_working_mask(const DlStreamScene *scene,uint32_t region){
    if(!valid(scene)||!region||region>scene->mission->region_count)return 0;
    return scene->bootstrap->stream->policy==DL_STREAM_POLICY_AUTHORED?
        scene->bootstrap->stream->policy_regions[region-1].working_mask:UINT32_C(1)<<(region-1);
}
static float gate_distance(const DlStreamTransition *gate,DlVec3 p){
    float x=fmaxf(gate->minimum.x-p.x,fmaxf(0,p.x-gate->maximum.x));
    float y=fmaxf(gate->minimum.y-p.y,fmaxf(0,p.y-gate->maximum.y));
    float z=fmaxf(gate->minimum.z-p.z,fmaxf(0,p.z-gate->maximum.z));
    return sqrtf(x*x+y*y+z*z);
}
static bool gate_crossed(const DlStreamTransition *gate,DlVec3 from,DlVec3 to){
    float entry=0,exit=1;
    const float start[3]={from.x,from.y,from.z},end[3]={to.x,to.y,to.z};
    const float minimum[3]={gate->minimum.x,gate->minimum.y,gate->minimum.z};
    const float maximum[3]={gate->maximum.x,gate->maximum.y,gate->maximum.z};
    for(int axis=0;axis<3;++axis){
        float delta=end[axis]-start[axis];
        if(delta==0){if(start[axis]<minimum[axis]||start[axis]>maximum[axis])return false;}
        else{
            float first=(minimum[axis]-start[axis])/delta,last=(maximum[axis]-start[axis])/delta;
            if(first>last){float swap=first;first=last;last=swap;}
            entry=fmaxf(entry,first);exit=fminf(exit,last);
            if(entry>exit)return false;
        }
    }
    return true;
}
void dl_stream_scene_policy_reset(DlStreamPolicyState *state){if(state)memset(state,0,sizeof *state);}
static float approach_score(const DlStreamTransition *gate,DlVec3 position,const DlStreamPolicyState *state){
    float score=gate_distance(gate,position);
    if(!state->has_approach_direction)return score;
    DlVec3 end={position.x+state->approach_direction.x*approach_distance,position.y,
        position.z+state->approach_direction.z*approach_distance};
    if(gate_crossed(gate,position,end))return 0;
    for(int i=1;i<=3;++i){float distance_ahead=approach_distance*i/3;
        DlVec3 probe={position.x+state->approach_direction.x*distance_ahead,position.y,
        position.z+state->approach_direction.z*distance_ahead};
        float distance=gate_distance(gate,probe);if(distance<score)score=distance;
    }
    return score;
}
uint32_t dl_stream_scene_demand(const DlStreamScene *scene,DlVec3 position,uint32_t current){
    DlStreamPolicyState state={.current_region=current};
    return dl_stream_scene_policy_update(scene,position,0,&state);
}
uint32_t dl_stream_scene_policy_update(const DlStreamScene *scene,DlVec3 position,uint32_t resident,DlStreamPolicyState *state){
    if(!valid(scene)||!state||!finite_vec(position))return 0;
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    if(state->current_region>scene->mission->region_count||
        (state->has_previous_position&&!finite_vec(state->previous_position))||
        (state->has_approach_position&&!finite_vec(state->approach_position))||
        (state->has_approach_direction&&!finite_vec(state->approach_direction)))return 0;
    if(state->has_approach_position){
        float x=position.x-state->approach_position.x,z=position.z-state->approach_position.z;
        float length2=x*x+z*z;
        if(length2>1){state->has_approach_direction=false;state->approach_reverse_distance=0;} /* remote placement */
        else if(length2>.000001f){float length=sqrtf(length2);
            DlVec3 direction={x/length,0,z/length};
            float alignment=direction.x*state->approach_direction.x+direction.z*state->approach_direction.z;
            if(state->has_approach_direction&&alignment<-.2f)state->approach_reverse_distance+=length;
            else state->approach_reverse_distance=0;
            /* Landing and contact corrections must not reverse a prefetch.
             * Deliberate return travel switches after ten centimetres; turns
             * toward a different doorway continue to switch immediately. */
            if(!state->has_approach_direction||alignment>=-.2f||state->approach_reverse_distance>=.1f){
                state->approach_direction=direction;state->has_approach_direction=true;state->approach_reverse_distance=0;
            }
        }
    }
    state->approach_position=position;state->has_approach_position=true;
    uint32_t waiting=0;
    uint32_t candidate=dl_stream_scene_region_at_preferred(scene,position,state->current_region?state->current_region:scene->mission->entry_region_id);
    if(!state->current_region){if(!candidate)return 0;state->current_region=candidate;}
    else if(candidate&&candidate!=state->current_region){
        uint32_t target=dl_stream_scene_working_mask(scene,candidate);
        for(uint32_t i=0;i<definition->transition_count;++i){
            const DlStreamTransition *gate=&definition->transitions[i];
            if(gate->from_region!=state->current_region||gate->to_region!=candidate||
                !(contains_bounds(gate->minimum,gate->maximum,position)||
                  (state->has_previous_position&&gate_crossed(gate,state->previous_position,position))))continue;
            if((resident&target)==target){state->current_region=candidate;state->pending_transition=0;}
            else waiting=i+1;
            break;
        }
    }
    bool retain_sample=waiting!=0;
    if(candidate==state->current_region&&state->has_previous_position&&definition->policy==DL_STREAM_POLICY_AUTHORED){
        for(uint32_t i=0;i<definition->transition_count;++i){const DlStreamTransition *gate=&definition->transitions[i];
            if(gate->from_region==state->current_region&&region_contains(scene,gate->to_region,position)&&
                gate_crossed(gate,state->previous_position,position)){retain_sample=true;break;}
        }
    }
    /* Keep the from-side sample while a crossed destination is loading, or
     * preferred activation overlap still retains the current region. Otherwise
     * the next frame could forget the thin gate before leaving that overlap.
     * Returning to the from side replaces the sample and cancels the attempt. */
    if(!retain_sample||!state->has_previous_position){state->previous_position=position;state->has_previous_position=true;}
    uint32_t wanted=dl_stream_scene_working_mask(scene,state->current_region);
    if(definition->policy!=DL_STREAM_POLICY_AUTHORED)return wanted;
    /* At ordinary walking speed this gives preparation about two seconds of
     * approach time while retaining only one bounded transition union. */
    uint32_t best=waiting;float nearest=approach_distance;
    for(uint32_t i=0;i<definition->transition_count;++i){const DlStreamTransition *gate=&definition->transitions[i];
        if(gate->from_region!=state->current_region)continue;
        float distance=gate_distance(gate,position),score=approach_score(gate,position,state);
        if(!waiting&&distance<=approach_distance&&(!best||score<nearest)){nearest=score;best=i+1;}
    }
    if(!waiting&&state->pending_transition&&state->pending_transition<=definition->transition_count){
        const DlStreamTransition *pending=&definition->transitions[state->pending_transition-1];
        float distance=gate_distance(pending,position),score=approach_score(pending,position,state);
        if(pending->from_region==state->current_region&&distance<=approach_distance&&(!best||nearest+.15f>=score))best=state->pending_transition;
    }
    state->pending_transition=best;
    if(best)wanted|=dl_stream_scene_working_mask(scene,definition->transitions[best-1].to_region);
    return wanted;
}
bool dl_stream_scene_transition_at(const DlStreamScene *scene,uint32_t from,uint32_t to,DlVec3 position){
    if(!valid(scene)||!finite_vec(position))return false;
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    for(uint32_t i=0;i<definition->transition_count;++i){const DlStreamTransition *gate=&definition->transitions[i];
        if(gate->from_region==from&&gate->to_region==to&&contains_bounds(gate->minimum,gate->maximum,position))return true;}
    return false;
}
bool dl_stream_scene_gate_waiting(const DlStreamScene *scene,DlVec3 position,uint32_t resident){
    if(!valid(scene)||!finite_vec(position))return false;
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    if(definition->policy!=DL_STREAM_POLICY_AUTHORED)return false;
    for(uint32_t i=0;i<definition->transition_count;++i){const DlStreamTransition *gate=&definition->transitions[i];
        uint32_t target=dl_stream_scene_working_mask(scene,gate->to_region);
        if((resident&(UINT32_C(1)<<(gate->from_region-1)))&&(resident&target)!=target&&gate_distance(gate,position)<=.35f)return true;}
    return false;
}
uint32_t dl_stream_scene_door_target(const DlStreamScene *scene,uint32_t current,uint32_t door,uint32_t *transition){
    if(transition)*transition=0;
    if(!valid(scene)||!door||!transition)return 0;
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    if(definition->policy!=DL_STREAM_POLICY_AUTHORED)return 0;
    for(uint32_t i=0;i<definition->transition_count;++i){const DlStreamTransition *gate=&definition->transitions[i];
        if(gate->from_region==current&&gate->door_entity_id==door){*transition=i+1;
            return dl_stream_scene_working_mask(scene,current)|dl_stream_scene_working_mask(scene,gate->to_region);}}
    return 0;
}
bool dl_stream_scene_region_resident(const DlStreamScene *scene,uint32_t region_id){
    return valid(scene)&&bank_resident(&scene->banks[scene->active_bank],region_id);
}
uint16_t dl_stream_scene_enemy_mask(const DlStreamScene *scene,const DlGame *game){
    if(!valid(scene)||!game||game->enemy_count!=scene->bootstrap->enemy_count)return 0;
    uint16_t mask=0;
    for(int i=0;i<game->enemy_count;++i){
        uint32_t region=dl_stream_scene_region_at(scene,game->enemies[i].position);
        if(dl_stream_scene_region_resident(scene,region))mask|=(uint16_t)(UINT16_C(1)<<i);
    }
    return mask;
}
const char *dl_stream_scene_status_name(DlStreamSceneStatus status){
    static const char *const names[]={"ok","argument","capacity","corrupt","not-ready"};
    return (unsigned)status<sizeof names/sizeof names[0]?names[status]:"unknown";
}
