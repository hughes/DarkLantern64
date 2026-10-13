#include "runtime_diagnostics.h"
#include <math.h>
#include <string.h>

static float clamp(float value,float lo,float hi){return value<lo?lo:value>hi?hi:value;}
static float axis_distance(float p,float lo,float hi){return fmaxf(lo-p,fmaxf(0,p-hi));}
static float gate_distance(const DlStreamTransition *gate,DlVec3 p){
    float x=axis_distance(p.x,gate->minimum.x,gate->maximum.x);
    float y=axis_distance(p.y,gate->minimum.y,gate->maximum.y);
    float z=axis_distance(p.z,gate->minimum.z,gate->maximum.z);
    return sqrtf(x*x+y*y+z*z);
}
static float input_axis(float value){return isfinite(value)?clamp(value,-1,1):0;}
static bool finite_position(DlVec3 p){return isfinite(p.x)&&isfinite(p.y)&&isfinite(p.z);}

void dl_runtime_query_source(const DlStreamScene *scene,const DlGame *game,
    const DlGameQueryDiagnostic *query,DlRuntimeQuerySource *out){
    if(!out)return;
    memset(out,0,sizeof *out);out->object_index=-1;
    if(!game||!game->level||!query||!query->valid||query->clear)return;
    if(query->floor_index>=0){out->kind=DL_RUNTIME_SOURCE_FLOOR;return;}
    int index=query->collider_index;
    if(index<0||index>=game->level->collider_count)return;
    out->kind=DL_RUNTIME_SOURCE_COLLIDER;out->object_index=query->object_index;
    if(query->object_index>=0&&query->object_index<game->level->object_count&&game->level->objects)
        out->object_id=game->level->objects[query->object_index].id;
    if(!scene)return;
#if DL_RUNTIME_DIAGNOSTICS
    for(unsigned b=0;b<2;++b){const DlStreamSceneBank *bank=&scene->banks[b];
        if(game->level!=&bank->level)continue;
        uint64_t packed=bank->collider_sources[index];
        out->entity=(uint32_t)(packed&UINT64_C(0xfff));out->region=(uint32_t)((packed>>12)&UINT64_C(0x3f));
        out->record=(uint32_t)((packed>>18)&UINT64_C(0x1ff));out->gate=(uint32_t)(packed>>27);
        if(out->gate)out->kind=DL_RUNTIME_SOURCE_GATE_BARRIER;
        else if(packed&(UINT64_C(1)<<59))out->kind=DL_RUNTIME_SOURCE_REGION_BARRIER;
        return;
    }
#else
    (void)scene;
#endif
}

/* Predict the requested horizontal displacement with the simulation's input
 * clamp, normalized diagonals, speed, turn order and 120 Hz substeps. Collision
 * and stepping still belong to simulation; this is explicitly an intent probe. */
static DlVec3 requested_delta(const DlGame *game,const DlInput *input,float dt,bool *crouched){
    DlVec3 delta={0};
    *crouched=input->crouch||(game->crouched&&!dl_position_clear(game,game->player,.18f,1.65f));
    if(dt<=0||game->complete||game->caught||input->restart)return delta;
    dt=fminf(dt,.25f);
    float forward=input_axis(input->forward),strafe=input_axis(input->strafe),turn=input_axis(input->turn);
    float magnitude=sqrtf(forward*forward+strafe*strafe);
    if(magnitude>1){forward/=magnitude;strafe/=magnitude;}
    int steps=(int)ceilf(dt*120);float step=dt/steps,yaw=game->yaw,speed=*crouched?1.f:2.1f;
    for(int i=0;i<steps;++i){
        yaw=fmodf(yaw-turn*2.3f*step,6.28318530717958647692f);
        if(yaw<0)yaw+=6.28318530717958647692f;
        delta.x+=(sinf(yaw)*forward-cosf(yaw)*strafe)*speed*step;
        delta.z+=(cosf(yaw)*forward+sinf(yaw)*strafe)*speed*step;
    }
    return delta;
}
static DlRuntimeMotionDirection motion_direction(const DlStreamScene *scene,
    const DlStreamTransition *gate,DlVec3 position,DlVec3 delta){
    if(delta.x*delta.x+delta.z*delta.z<1e-12f)return DL_RUNTIME_MOTION_NONE;
    float x=clamp(position.x,gate->minimum.x,gate->maximum.x)-position.x;
    float z=clamp(position.z,gate->minimum.z,gate->maximum.z)-position.z;
    if(x*x+z*z<1e-12f){
        /* Inside a gate, the two region centers give its forward direction. */
        DlMissionRegion from,to;
        if(scene->mission&&dl_mission_region(scene->mission,gate->from_region,&from)==DL_MISSION_OK&&
            dl_mission_region(scene->mission,gate->to_region,&to)==DL_MISSION_OK){
            x=(to.minimum.x+to.maximum.x-from.minimum.x-from.maximum.x)*.5f;
            z=(to.minimum.z+to.maximum.z-from.minimum.z-from.maximum.z)*.5f;
        }
    }
    float dot=x*delta.x+z*delta.z;
    float epsilon=1e-5f*sqrtf((x*x+z*z)*(delta.x*delta.x+delta.z*delta.z));
    if(dot>epsilon)return DL_RUNTIME_MOTION_TOWARD;
    if(dot<-epsilon)return DL_RUNTIME_MOTION_AWAY;
    return DL_RUNTIME_MOTION_PARALLEL;
}
static DlRuntimeReadinessReason readiness_reason(uint32_t required,DlRuntimeReadiness readiness){
    if((readiness.published_mask&required)==required)return DL_RUNTIME_READY;
    if((readiness.physical_mask&required)!=required)return DL_RUNTIME_WAIT_READ;
    if((readiness.pending_mask&required)!=required||!readiness.cpu_ready)return DL_RUNTIME_WAIT_CPU;
    if(!readiness.gpu_ready)return DL_RUNTIME_WAIT_GPU;
    return DL_RUNTIME_WAIT_PUBLICATION;
}
static bool diagnose_motion(const DlStreamScene *scene,const DlGame *game,
    const DlInput *requested,const DlInput *filtered,float dt,DlRuntimeReadiness readiness,
    bool inspect_position,DlRuntimeMotionDiagnostic *out){
    if(!out)return false;
    memset(out,0,sizeof *out);out->position_query.collider_index=out->position_query.floor_index=out->position_query.object_index=-1;
    out->position_source.object_index=-1;
    if(!scene||!scene->bootstrap||!scene->bootstrap->stream||!game||!game->level||!requested||!filtered||
        !finite_position(game->player)||!isfinite(game->yaw)||!isfinite(dt))return false;
    const DlStreamDefinition *definition=scene->bootstrap->stream;
    if(definition->transition_count&&!definition->transitions)return false;
    out->valid=true;out->position=game->player;
    bool crouched;out->requested_delta=requested_delta(game,requested,dt,&crouched);
    out->requested_position=(DlVec3){game->player.x+out->requested_delta.x,game->player.y,game->player.z+out->requested_delta.z};
    out->horizontal_suppressed=(input_axis(requested->forward)!=input_axis(filtered->forward)||
        input_axis(requested->strafe)!=input_axis(filtered->strafe));
    out->jump_suppressed=requested->jump&&!filtered->jump;
    if(inspect_position){
        dl_game_diagnose_position(game,out->requested_position,.18f,crouched?.95f:1.65f,&out->position_query);
        dl_runtime_query_source(scene,game,&out->position_query,&out->position_source);
    }
    if(definition->policy!=DL_STREAM_POLICY_AUTHORED)return true;
    float nearest=INFINITY;uint32_t selected=0;
    for(uint32_t i=0;i<definition->transition_count;++i){const DlStreamTransition *gate=&definition->transitions[i];
        if(!gate->from_region||gate->from_region>32)continue;
        if(!(readiness.published_mask&(UINT32_C(1)<<(gate->from_region-1))))continue;
        uint32_t required=dl_stream_scene_working_mask(scene,gate->to_region);
        float distance=gate_distance(gate,game->player);
        bool waiting=(readiness.published_mask&required)!=required&&distance<=.35f;
        if(waiting){selected=i+1;out->gate_waiting=true;break;}
        if(distance<nearest){nearest=distance;selected=i+1;}
    }
    if(!selected)return true;
    const DlStreamTransition *gate=&definition->transitions[selected-1];
    out->gate=selected;out->from_region=gate->from_region;out->to_region=gate->to_region;out->door_entity=gate->door_entity_id;
    out->required_mask=dl_stream_scene_working_mask(scene,gate->to_region);
    out->missing_physical=out->required_mask&~readiness.physical_mask;
    out->missing_published=out->required_mask&~readiness.published_mask;
    out->gate_distance=gate_distance(gate,game->player);
    out->direction=motion_direction(scene,gate,game->player,out->requested_delta);
    out->readiness=readiness_reason(out->required_mask,readiness);
    return true;
}
bool dl_runtime_diagnose_motion(const DlStreamScene *scene,const DlGame *game,
    const DlInput *requested,const DlInput *filtered,float dt,DlRuntimeReadiness readiness,
    DlRuntimeMotionDiagnostic *out){
    return diagnose_motion(scene,game,requested,filtered,dt,readiness,true,out);
}
bool dl_runtime_diagnose_motion_intent(const DlStreamScene *scene,const DlGame *game,
    const DlInput *requested,const DlInput *filtered,float dt,DlRuntimeReadiness readiness,
    DlRuntimeMotionDiagnostic *out){
    return diagnose_motion(scene,game,requested,filtered,dt,readiness,false,out);
}

bool dl_runtime_diagnose_interaction(const DlStreamScene *scene,const DlGame *game,
    int object,DlRuntimeInteractionDiagnostic *out){
    if(!out)return false;
    memset(out,0,sizeof *out);out->object_index=out->ignored_object=-1;
    if(!game||!game->level||!game->level->objects||object<0||object>=game->level->object_count||object>=DL_MAX_OBJECTS)return false;
    const DlObjectDef *definition=&game->level->objects[object];
    out->valid=true;out->object_index=object;out->object_id=definition->id;out->object_kind=definition->kind;
    out->eye=dl_player_eye(game);out->target=definition->position;out->radius=definition->radius;
    DlVec3 origin=definition->zone?game->player:out->eye;
    float x=origin.x-definition->position.x,y=origin.y-definition->position.y,z=origin.z-definition->position.z;
    out->distance=sqrtf(x*x+y*y+z*z);out->in_range=out->distance<=out->radius;
    if(definition->zone)out->target.y+=.1f;
    out->ignored_object=definition->kind==DL_OBJECT_DOOR?object:-1;
    dl_game_diagnose_line_of_sight(game,out->eye,out->target,-1,&out->ordinary_los);
    dl_game_diagnose_line_of_sight(game,out->eye,out->target,out->ignored_object,&out->interaction_los);
    dl_runtime_query_source(scene,game,&out->ordinary_los,&out->ordinary_source);
    dl_runtime_query_source(scene,game,&out->interaction_los,&out->interaction_source);
    uint64_t bit=UINT64_C(1)<<object;
    if(definition->kind==DL_OBJECT_DOOR){out->door_open=dl_game_object_open(game,object);
        out->door_occupied=out->door_open&&dl_game_door_occupied(game,object);}
    else if(definition->kind==DL_OBJECT_CONTROL){
        for(int i=0;i<game->level->object_count&&i<DL_MAX_OBJECTS;++i)
            if((definition->targets&(UINT64_C(1)<<i))&&dl_game_object_open(game,i)&&dl_game_door_occupied(game,i))out->door_occupied=true;
    }
    if(!out->in_range)out->reason=DL_RUNTIME_INTERACTION_OUT_OF_RANGE;
    else if((definition->kind==DL_OBJECT_LOOT&&(game->collected_objects&bit))||
        (definition->kind==DL_OBJECT_OBJECTIVE&&(game->completed_objectives&bit)))out->reason=DL_RUNTIME_INTERACTION_CONSUMED;
    else if(definition->kind==DL_OBJECT_OBJECTIVE&&game->loot_value<definition->required_loot)out->reason=DL_RUNTIME_INTERACTION_LOOT_REQUIRED;
    else if(!out->interaction_los.clear)out->reason=DL_RUNTIME_INTERACTION_OCCLUDED;
    else if(out->door_occupied)out->reason=DL_RUNTIME_INTERACTION_OCCUPIED;
    else{out->eligible=true;out->reason=DL_RUNTIME_INTERACTION_READY;}
    return true;
}
const char *dl_runtime_readiness_name(DlRuntimeReadinessReason reason){
    static const char *names[]={"ready","read","cpu","gpu","publication"};
    return (unsigned)reason<sizeof names/sizeof names[0]?names[reason]:"unknown";
}
const char *dl_runtime_motion_direction_name(DlRuntimeMotionDirection direction){
    static const char *names[]={"none","toward","parallel","away"};
    return (unsigned)direction<sizeof names/sizeof names[0]?names[direction]:"unknown";
}
const char *dl_runtime_source_name(DlRuntimeSourceKind kind){
    static const char *names[]={"none","collider","gate_barrier","region_barrier","floor"};
    return (unsigned)kind<sizeof names/sizeof names[0]?names[kind]:"unknown";
}
const char *dl_runtime_interaction_reason_name(DlRuntimeInteractionReason reason){
    static const char *names[]={"ready","out_of_range","consumed","loot_required","occluded","occupied"};
    return (unsigned)reason<sizeof names/sizeof names[0]?names[reason]:"unknown";
}
