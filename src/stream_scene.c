#include "stream_scene.h"
#include <math.h>
#include <string.h>

static bool valid(const DlStreamScene *scene){
    return scene&&scene->bootstrap&&scene->bootstrap->stream&&scene->mission&&scene->mission->directory;
}
static DlVec3 vec(DlMissionVec3 p){return (DlVec3){p.x,p.y,p.z};}
static bool finite_vec(DlVec3 p){return isfinite(p.x)&&isfinite(p.y)&&isfinite(p.z);}
static bool contains(const DlMissionRegion *region,DlVec3 p){
    return p.x>=region->minimum.x&&p.x<=region->maximum.x&&
        p.y>=region->minimum.y&&p.y<=region->maximum.y&&p.z>=region->minimum.z&&p.z<=region->maximum.z;
}
static bool bank_resident(const DlStreamSceneBank *bank,uint32_t id){
    for(uint32_t i=0;i<bank->resident_count;++i)if(bank->resident_ids[i]==id)return true;
    return false;
}
static uint32_t locate(const DlStreamScene *scene,const DlStreamSceneBank *bank,DlVec3 p){
    if(!finite_vec(p))return 0;
    uint32_t first=0;
    for(uint32_t i=1;i<=scene->mission->region_count;++i){
        DlMissionRegion region;
        if(dl_mission_region(scene->mission,i,&region)!=DL_MISSION_OK)return 0;
        if(contains(&region,p)){if(bank_resident(bank,i))return i;if(!first)first=i;}
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
        !definition->max_region_bytes||bootstrap->collider_count!=0)return DL_STREAM_SCENE_ARGUMENT;
    if(mission->region_count>DL_STREAM_SCENE_MAX_REGIONS||mission->entity_count>DL_STREAM_SCENE_MAX_ENTITIES||
        bootstrap->model_count<0||bootstrap->model_count>DL_MAX_MODELS||
        bootstrap->mesh_count<0||bootstrap->mesh_count>DL_MAX_MODELS||
        bootstrap->object_count<0||bootstrap->object_count>DL_MAX_OBJECTS||
        bootstrap->enemy_count<0||bootstrap->enemy_count>DL_MAX_ENEMIES||
        bootstrap->texture_count<0||bootstrap->texture_count>64||
        bootstrap->light_count<0||bootstrap->light_count>16||
        definition->material_count>DL_STREAM_SCENE_MAX_MATERIALS)return DL_STREAM_SCENE_CAPACITY;
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
        if(!definition->materials[i].id)return DL_STREAM_SCENE_ARGUMENT;
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
    if(bank->level.collider_count>=DL_STREAM_SCENE_MAX_COLLIDERS)return DL_STREAM_SCENE_CAPACITY;
    int index=bank->level.collider_count++;
    bank->colliders[index]=box;bank->collider_objects[index]=(int16_t)object;return DL_STREAM_SCENE_OK;
}
static DlStreamSceneStatus scan_chunk(DlStreamScene *scene,DlStreamSceneBank *bank,
    const DlMissionChunk *chunk,uint32_t region_id,bool *entities){
    DlMissionSection section;
    if(dl_mission_section(chunk,DL_MISSION_SECTION_ENTITIES,&section)==DL_MISSION_OK){
        if(section.count>scene->mission->entity_count)return DL_STREAM_SCENE_CORRUPT;
        for(uint32_t i=0;i<section.count;++i){
            uint32_t id;
            if(dl_mission_entity_id(chunk,scene->mission,region_id,i,&id)!=DL_MISSION_OK||entities[id-1])
                return DL_STREAM_SCENE_CORRUPT;
            entities[id-1]=true;
        }
    }
    if(dl_mission_section(chunk,DL_MISSION_SECTION_TRIANGLES,&section)==DL_MISSION_OK){
        if(section.count>(uint32_t)(DL_STREAM_SCENE_MAX_TRIANGLES-bank->triangle_count))return DL_STREAM_SCENE_CAPACITY;
        for(uint32_t i=0;i<section.count;++i){
            DlMissionTriangle triangle;
            if(dl_mission_triangle(chunk,i,&triangle)!=DL_MISSION_OK||!material(scene,triangle.material_id))
                return DL_STREAM_SCENE_CORRUPT;
            for(int v=0;v<3;++v){
                DlVec3 world={triangle.positions[v].x+chunk->origin.x,triangle.positions[v].y+chunk->origin.y,
                    triangle.positions[v].z+chunk->origin.z};
                if(!finite_vec(world))return DL_STREAM_SCENE_CORRUPT;
            }
            int group=0;
            while(group<bank->group_count&&(bank->groups[group].region_id!=region_id||
                bank->groups[group].material_id!=triangle.material_id))++group;
            if(group==bank->group_count){
                if(group>=DL_STREAM_SCENE_MAX_GROUPS)return DL_STREAM_SCENE_CAPACITY;
                bank->groups[group]=(DlStreamSceneGroup){.region_id=region_id,.material_id=triangle.material_id,
                    .origin=vec(chunk->origin)};++bank->group_count;
            }
            ++bank->groups[group].triangle_count;++bank->triangle_count;
        }
    }
    if(dl_mission_section(chunk,DL_MISSION_SECTION_COLLIDERS,&section)==DL_MISSION_OK){
        if(section.count>(uint32_t)(DL_STREAM_SCENE_MAX_COLLIDERS-bank->level.collider_count))return DL_STREAM_SCENE_CAPACITY;
        for(uint32_t i=0;i<section.count;++i){
            DlMissionCollider collider;
            if(dl_mission_collider(chunk,scene->mission,i,&collider)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
            DlCollider box={.center={collider.center.x+chunk->origin.x,collider.center.y+chunk->origin.y,
                collider.center.z+chunk->origin.z},.half_size=vec(collider.half_size),.yaw=collider.yaw};
            if(!finite_vec(box.center))return DL_STREAM_SCENE_CORRUPT;
            int object=-1;
            if(collider.entity_id){
                DlMissionEntity entity;dl_mission_entity(scene->mission,collider.entity_id,&entity);
                if(entity.kind==DL_MISSION_ENTITY_DOOR){
                    object=object_index(scene,entity.id);
                    if(object<0)return DL_STREAM_SCENE_CORRUPT;
                    box.door=true;
                }
            }
            DlStreamSceneStatus status=add_collider(bank,box,object);
            if(status!=DL_STREAM_SCENE_OK)return status;
        }
    }
    return DL_STREAM_SCENE_OK;
}
static DlStreamSceneStatus fill_chunk(DlStreamSceneBank *bank,const DlMissionChunk *chunk,uint32_t region_id){
    DlMissionSection section;
    if(dl_mission_section(chunk,DL_MISSION_SECTION_TRIANGLES,&section)!=DL_MISSION_OK)return DL_STREAM_SCENE_OK;
    for(uint32_t i=0;i<section.count;++i){
        DlMissionTriangle triangle;
        if(dl_mission_triangle(chunk,i,&triangle)!=DL_MISSION_OK)return DL_STREAM_SCENE_CORRUPT;
        int group=0;
        while(group<bank->group_count&&(bank->groups[group].region_id!=region_id||
            bank->groups[group].material_id!=triangle.material_id))++group;
        if(group==bank->group_count)return DL_STREAM_SCENE_CORRUPT;
        DlStreamSceneGroup *target=&bank->groups[group];
        int local=target->written_triangles++*3;
        for(int v=0;v<3;++v){
            bank->vertices[target->first_vertex+local+v]=vec(triangle.positions[v]);
            bank->indices[target->first_vertex+local+v]=(uint16_t)(local+v);
        }
    }
    return DL_STREAM_SCENE_OK;
}
DlStreamSceneStatus dl_stream_scene_refresh(DlStreamScene *scene,DlWorldStream *stream,const DlGame *game){
    if(!valid(scene)||!stream||stream->mission!=scene->mission||!stream->slots||
        (game&&game->enemy_count!=scene->bootstrap->enemy_count))return DL_STREAM_SCENE_ARGUMENT;
    if(scene->revision==UINT32_MAX)return DL_STREAM_SCENE_CAPACITY;
    DlStreamSceneBank *bank=&scene->banks[scene->active_bank^1u];
    memset(bank,0,sizeof *bank);bank->level=*scene->bootstrap;
    bank->level.meshes=bank->meshes;bank->level.models=bank->models;bank->level.model_count=0;
    bank->level.colliders=bank->colliders;bank->level.collider_count=0;bank->level.collider_objects=bank->collider_objects;
    bank->level.baked_lighting=NULL;
    if(scene->bootstrap->mesh_count)memcpy(bank->meshes,scene->bootstrap->meshes,
        (size_t)scene->bootstrap->mesh_count*sizeof(DlMesh));
    DlRegionHandle handles[DL_STREAM_SCENE_MAX_RESIDENT_REGIONS];
    const DlMissionChunk *chunks[DL_STREAM_SCENE_MAX_RESIDENT_REGIONS];
    size_t borrowed=0;DlStreamSceneStatus result=DL_STREAM_SCENE_OK;
    for(size_t i=0;i<stream->slot_count;++i){
        const DlRegionSlot *slot=&stream->slots[i];
        if(slot->state!=DL_STREAM_RESIDENT)continue;
        if(borrowed==DL_STREAM_SCENE_MAX_RESIDENT_REGIONS){result=DL_STREAM_SCENE_CAPACITY;goto finish;}
        handles[borrowed]=(DlRegionHandle){(uint32_t)i,slot->generation};
        if(dl_world_stream_borrow(stream,handles[borrowed],&chunks[borrowed])!=DL_STREAM_OK){
            result=DL_STREAM_SCENE_NOT_READY;goto finish;
        }
        bank->resident_ids[borrowed]=slot->region_id;++borrowed;
    }
    bank->resident_count=(uint32_t)borrowed;
    bool entities[DL_STREAM_SCENE_MAX_ENTITIES]={false};
    for(size_t i=0;i<borrowed;++i){
        result=scan_chunk(scene,bank,chunks[i],bank->resident_ids[i],entities);
        if(result!=DL_STREAM_SCENE_OK)goto finish;
    }
    for(uint32_t id=1;id<=scene->mission->entity_count;++id){
        DlMissionEntity entity;dl_mission_entity(scene->mission,id,&entity);
        if(bank_resident(bank,entity.home_region_id)&&!entities[id-1]){result=DL_STREAM_SCENE_CORRUPT;goto finish;}
    }
    int vertex=0;
    for(int i=0;i<bank->group_count;++i){
        DlStreamSceneGroup *group=&bank->groups[i];group->first_vertex=vertex;vertex+=group->triangle_count*3;
    }
    for(size_t i=0;i<borrowed;++i){
        result=fill_chunk(bank,chunks[i],bank->resident_ids[i]);if(result!=DL_STREAM_SCENE_OK)goto finish;
    }
    int instanced_vertices=vertex,instanced_triangles=bank->triangle_count;
    for(int i=0;i<scene->bootstrap->model_count;++i){
        const DlModelInstance *model=&scene->bootstrap->models[i];
        DlMissionEntity entity;dl_mission_entity(scene->mission,scene->bootstrap->stream->model_entity_ids[i],&entity);
        uint32_t region=entity.home_region_id;
        if(model->role==DL_MODEL_GUARD&&game)region=locate(scene,bank,game->enemies[model->enemy_index].position);
        /* Door leaves are small shared bootstrap assets. Keep their authoritative
         * visual state from both sides even when the home architecture retires.
         * The probe cooker supplies static wall closure on each region's side. */
        if(model->role!=DL_MODEL_DOOR&&!bank_resident(bank,region))continue;
        if(bank->level.model_count+bank->group_count>=DL_MAX_MODELS){result=DL_STREAM_SCENE_CAPACITY;goto finish;}
        const DlMesh *mesh=&scene->bootstrap->meshes[model->mesh];
        if(mesh->vertex_count>DL_MAX_SCENE_VERTICES-instanced_vertices||
            mesh->index_count/3>DL_MAX_SCENE_TRIANGLES-instanced_triangles){result=DL_STREAM_SCENE_CAPACITY;goto finish;}
        instanced_vertices+=mesh->vertex_count;instanced_triangles+=mesh->index_count/3;
        bank->models[bank->level.model_count++]=*model;
    }
    for(int i=0;i<bank->group_count;++i){
        const DlStreamSceneGroup *group=&bank->groups[i];
        const DlStreamMaterial *source=material(scene,group->material_id);
        int mesh=bank->level.mesh_count++;
        bank->meshes[mesh]=(DlMesh){.vertices=bank->vertices+group->first_vertex,.vertex_count=group->triangle_count*3,
            .indices=bank->indices+group->first_vertex,.index_count=group->triangle_count*3};
        DlModelInstance *model=&bank->models[bank->level.model_count++];
        *model=(DlModelInstance){.mesh=(uint16_t)mesh,.position=group->origin,.scale={1,1,1},.role=DL_MODEL_STATIC,
            .texture=-1,.double_sided=source->double_sided,.enemy_index=-1};
        memcpy(model->color,source->color,4);
    }
    for(uint32_t id=1;id<=scene->mission->region_count;++id){
        if(bank_resident(bank,id))continue;
        DlMissionRegion region;dl_mission_region(scene->mission,id,&region);
        DlCollider barrier={.center={region.minimum.x*0.5f+region.maximum.x*0.5f,
            region.minimum.y*0.5f+region.maximum.y*0.5f,region.minimum.z*0.5f+region.maximum.z*0.5f},
            .half_size={(region.maximum.x-region.minimum.x)*0.5f,(region.maximum.y-region.minimum.y)*0.5f,
                (region.maximum.z-region.minimum.z)*0.5f}};
        result=add_collider(bank,barrier,-1);if(result!=DL_STREAM_SCENE_OK)goto finish;
    }
finish:
    for(size_t i=0;i<borrowed;++i)dl_world_stream_release(stream,handles[i]);
    if(result==DL_STREAM_SCENE_OK){scene->active_bank^=1u;++scene->revision;}
    return result;
}
const DlLevel *dl_stream_scene_level(const DlStreamScene *scene){
    return valid(scene)&&scene->revision?&scene->banks[scene->active_bank].level:NULL;
}
uint32_t dl_stream_scene_region_at(const DlStreamScene *scene,DlVec3 position){
    return valid(scene)?locate(scene,&scene->banks[scene->active_bank],position):0;
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
