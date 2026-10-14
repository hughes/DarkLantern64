#include "lighting_policy.h"
#include <string.h>

uint32_t dl_lighting_independent_doors(const DlLevel *level){
    uint32_t doors=0;
    if(level&&level->objects)for(int i=0;i<level->object_count;++i)doors+=level->objects[i].kind==DL_OBJECT_DOOR;
    return doors;
}
static bool valid_model(const DlLevel *level,int index){return level&&level->models&&level->meshes&&index>=0&&
    index<level->model_count&&level->models[index].mesh<level->mesh_count;}
bool dl_lighting_static_eligible(const DlLevel *level,int index){
    if(!valid_model(level,index)||!level->environment.enabled)return false;
    const DlModelInstance *model=&level->models[index];return model->role!=DL_MODEL_GUARD&&
        model->role!=DL_MODEL_OBJECTIVE&&!level->meshes[model->mesh].animation;
}
bool dl_lighting_inline_eligible(const DlLevel *level,int index,bool enabled,uint32_t doors){
    return valid_model(level,index)&&dl_lighting_inline_payload(enabled,level->environment.enabled,doors,&level->models[index]);
}
DlLightingDecision dl_lighting_decision(const DlLevel *level,int index,DlLightingSettings settings){
    DlLightingDecision d={.mode=DL_LIGHT_INVALID,.change_mode=DL_LIGHT_INVALID,
        .reason=DL_LIGHT_INVALID_MODEL,.change_reason=DL_LIGHT_INVALID_MODEL};
    if(!valid_model(level,index))return d;
    const DlModelInstance *model=&level->models[index];const DlMesh *mesh=&level->meshes[model->mesh];
    d.triangles=mesh->index_count>0?(uint32_t)(mesh->index_count/3):0;
    d.static_eligible=dl_lighting_static_eligible(level,index);
    d.inline_eligible=dl_lighting_inline_eligible(level,index,settings.bake_enabled,settings.independent_doors);
    if(!level->environment.enabled){
        d.mode=model->role==DL_MODEL_GUARD?DL_LIGHT_LEGACY_ACTOR_PROBES:
            model->role==DL_MODEL_OBJECTIVE?DL_LIGHT_LEGACY_OBJECTIVE_EMISSIVE:DL_LIGHT_LEGACY_SCALAR;
        d.reason=model->role==DL_MODEL_GUARD?DL_LIGHT_GUARD:model->role==DL_MODEL_OBJECTIVE?DL_LIGHT_OBJECTIVE:
            mesh->animation?DL_LIGHT_ANIMATED_MESH:DL_LIGHT_LEGACY_ENVIRONMENT;
    }else if(model->role==DL_MODEL_GUARD){d.mode=DL_LIGHT_ACTOR_BODY_PROBES;d.reason=DL_LIGHT_GUARD;}
    else if(model->role==DL_MODEL_OBJECTIVE){d.mode=DL_LIGHT_OBJECTIVE_PROBE;d.reason=DL_LIGHT_OBJECTIVE;}
    else if(mesh->animation){d.mode=DL_LIGHT_LIVE_NIGHT;d.reason=DL_LIGHT_ANIMATED_MESH;}
    else if(d.inline_eligible){d.mode=DL_LIGHT_INLINE_TWO_STATE;d.reason=DL_LIGHT_INLINE_PAYLOAD;}
    else if(settings.bake_enabled&&settings.standalone_loaded){d.mode=DL_LIGHT_STANDALONE_BAKED;d.reason=DL_LIGHT_STANDALONE_ASSET;}
    else {d.mode=DL_LIGHT_LIVE_NIGHT;d.reason=!settings.bake_enabled?DL_LIGHT_DISABLED_BUILD:
        settings.independent_doors>1?DL_LIGHT_INDEPENDENT_DOORS:
        (model->baked_colors[0]!=NULL)!=(model->baked_colors[1]!=NULL)?DL_LIGHT_INCOMPLETE_INLINE_PAYLOAD:
        settings.standalone_rejected?DL_LIGHT_STANDALONE_REJECTED:DL_LIGHT_NIGHT_WITHOUT_BAKE;}
    d.change_mode=d.mode;d.change_reason=d.reason;
    if(d.static_eligible){
        if(settings.independent_doors>1){d.change_mode=DL_LIGHT_LIVE_NIGHT;d.change_reason=DL_LIGHT_INDEPENDENT_DOORS;}
        else if(d.mode==DL_LIGHT_LIVE_NIGHT){d.change_mode=DL_LIGHT_RUNTIME_TWO_STATE;d.change_reason=DL_LIGHT_CACHED_SINGLE_DOOR_STATES;}
    }
    return d;
}
void dl_lighting_policy_report(const DlLevel *level,DlLightingSettings settings,bool changed,DlLightingPolicyReport *out){
    if(!out)return;
    memset(out,0,sizeof *out);if(!level)return;
    for(int i=0;i<level->model_count;++i){DlLightingDecision d=dl_lighting_decision(level,i,settings);
        DlLightingMode mode=changed?d.change_mode:d.mode;DlLightingReason reason=changed?d.change_reason:d.reason;
        DlLightingCount *c=&out->cases[mode][reason];if(!c->models)++out->case_count;++c->models;c->triangles+=d.triangles;
        ++out->models;out->triangles+=d.triangles;}
}
const char *dl_lighting_mode_name(DlLightingMode mode){
    static const char *const names[]={"inline_two_state","standalone_baked","live_night","actor_body_probes","objective_probe",
        "legacy_scalar","legacy_actor_probes","legacy_objective_emissive","runtime_two_state","invalid"};
    return (unsigned)mode<DL_LIGHT_MODE_COUNT?names[mode]:"invalid";
}
const char *dl_lighting_reason_name(DlLightingReason reason){
    static const char *const names[]={"inline_payload","standalone_asset","independent_doors","guard","objective","animated_mesh",
        "night_without_bake","legacy_environment","disabled_build","standalone_rejected","incomplete_inline_payload",
        "cached_single_door_states","invalid_model"};return (unsigned)reason<DL_LIGHT_REASON_COUNT?names[reason]:"invalid_model";
}
typedef struct {char *out;size_t capacity,used;bool failed;} Text;
static void string(Text *t,const char *s){if(!s)s="unidentified";for(;*s;++s){if(t->used+1>=t->capacity){t->failed=true;return;}t->out[t->used++]=*s;}}
static void number(Text *t,uint64_t n){char digits[20];unsigned count=0;do{digits[count++]=(char)('0'+n%10);n/=10;}while(n);
    while(count){char s[2]={digits[--count],0};string(t,s);}}
static void field(Text *t,const char *key,uint64_t n){string(t,key);number(t,n);}
static size_t finish(Text *t){string(t,"\n");if(t->failed||!t->capacity)return 0;t->out[t->used]=0;return t->used;}
size_t dl_lighting_policy_header_text(char *out,size_t capacity,const DlLightingPolicyIdentity *id,const DlLightingPolicyReport *r){
    if(!out||!id||!r)return 0;
    Text t={out,capacity,0,false};string(&t,"DL64 lighting_policy version=1");field(&t," report=",id->report);
    string(&t,id->changed?" phase=lighting_change":" phase=publication");string(&t," build=");string(&t,id->build);string(&t," source=");string(&t,id->source);
    string(&t," package=");string(&t,id->package);string(&t," standalone=");string(&t,id->standalone);
    field(&t," doors=",id->doors);field(&t," revision=",id->revision);field(&t," published=",id->published);field(&t," door_revision=",id->door_revision);
    field(&t," models=",r->models);field(&t," cases=",r->case_count);string(&t," scope=published_model_policy");return finish(&t);
}
size_t dl_lighting_policy_case_text(char *out,size_t capacity,uint64_t report,DlLightingMode mode,DlLightingReason reason,DlLightingCount count){
    if(!out)return 0;
    Text t={out,capacity,0,false};string(&t,"DL64 lighting_case version=1");field(&t," report=",report);
    string(&t," mode=");string(&t,dl_lighting_mode_name(mode));string(&t," reason=");string(&t,dl_lighting_reason_name(reason));
    field(&t," models=",count.models);field(&t," triangles=",count.triangles);return finish(&t);
}
size_t dl_lighting_policy_cost_text(char *out,size_t capacity,uint64_t report,uint32_t ticks,uint32_t frequency){
    if(!out)return 0;
    Text t={out,capacity,0,false};string(&t,"DL64 lighting_policy_cost version=1");field(&t," report=",report);
    field(&t," ticks=",ticks);field(&t," ticks_per_second=",frequency);string(&t," scope=policy_rows_and_transport");return finish(&t);
}
