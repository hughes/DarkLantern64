#ifndef DARKLANTERN_RUNTIME_DIAGNOSTICS_H
#define DARKLANTERN_RUNTIME_DIAGNOSTICS_H

#include "stream_scene.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Optional diagnostics only: these helpers never mutate gameplay, policy,
 * publication, resource ownership or input. Masks name region ID - 1 bits. */
typedef struct {
    uint32_t physical_mask, published_mask, pending_mask;
    bool cpu_ready, gpu_ready;
} DlRuntimeReadiness;

typedef enum {
    DL_RUNTIME_READY, DL_RUNTIME_WAIT_READ, DL_RUNTIME_WAIT_CPU,
    DL_RUNTIME_WAIT_GPU, DL_RUNTIME_WAIT_PUBLICATION
} DlRuntimeReadinessReason;
typedef enum {
    DL_RUNTIME_MOTION_NONE, DL_RUNTIME_MOTION_TOWARD,
    DL_RUNTIME_MOTION_PARALLEL, DL_RUNTIME_MOTION_AWAY
} DlRuntimeMotionDirection;
typedef enum {
    DL_RUNTIME_SOURCE_NONE, DL_RUNTIME_SOURCE_COLLIDER,
    DL_RUNTIME_SOURCE_GATE_BARRIER, DL_RUNTIME_SOURCE_REGION_BARRIER,
    DL_RUNTIME_SOURCE_FLOOR
} DlRuntimeSourceKind;

typedef struct {
    DlRuntimeSourceKind kind;
    uint32_t entity, region, gate, record; /* authored collider ordinal+1; zero for barriers */
    int object_index;
    const char *object_id;
} DlRuntimeQuerySource;

typedef struct {
    bool valid, gate_waiting, horizontal_suppressed, jump_suppressed;
    DlVec3 position, requested_delta, requested_position;
    uint32_t gate, from_region, to_region, door_entity, required_mask;
    uint32_t missing_physical, missing_published;
    float gate_distance;
    DlRuntimeMotionDirection direction;
    DlRuntimeReadinessReason readiness;
    DlGameQueryDiagnostic position_query;
    DlRuntimeQuerySource position_source;
} DlRuntimeMotionDiagnostic;

typedef enum {
    DL_RUNTIME_INTERACTION_READY, DL_RUNTIME_INTERACTION_OUT_OF_RANGE,
    DL_RUNTIME_INTERACTION_CONSUMED, DL_RUNTIME_INTERACTION_LOOT_REQUIRED,
    DL_RUNTIME_INTERACTION_OCCLUDED, DL_RUNTIME_INTERACTION_OCCUPIED
} DlRuntimeInteractionReason;
typedef struct {
    bool valid, in_range, eligible, door_open, door_occupied;
    int object_index, ignored_object;
    const char *object_id;
    DlObjectKind object_kind;
    DlVec3 eye, target;
    float distance, radius;
    DlRuntimeInteractionReason reason;
    DlGameQueryDiagnostic ordinary_los, interaction_los;
    DlRuntimeQuerySource ordinary_source, interaction_source;
} DlRuntimeInteractionDiagnostic;

void dl_runtime_query_source(const DlStreamScene *scene,const DlGame *game,
    const DlGameQueryDiagnostic *query,DlRuntimeQuerySource *out);
bool dl_runtime_diagnose_motion(const DlStreamScene *scene,const DlGame *game,
    const DlInput *requested,const DlInput *filtered,float dt,
    DlRuntimeReadiness readiness,DlRuntimeMotionDiagnostic *out);
/* Lightweight read-only intent/mask/gate probe. Predicted-position/source
 * queries stay invalid. Releasing a crouch retains the simulation's standing
 * clearance check so intent uses the same speed as the next update. */
bool dl_runtime_diagnose_motion_intent(const DlStreamScene *scene,const DlGame *game,
    const DlInput *requested,const DlInput *filtered,float dt,
    DlRuntimeReadiness readiness,DlRuntimeMotionDiagnostic *out);
bool dl_runtime_diagnose_interaction(const DlStreamScene *scene,const DlGame *game,
    int object,DlRuntimeInteractionDiagnostic *out);
const char *dl_runtime_readiness_name(DlRuntimeReadinessReason reason);
const char *dl_runtime_motion_direction_name(DlRuntimeMotionDirection direction);
const char *dl_runtime_source_name(DlRuntimeSourceKind kind);
const char *dl_runtime_interaction_reason_name(DlRuntimeInteractionReason reason);

#ifdef __cplusplus
}
#endif
#endif
