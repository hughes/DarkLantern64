#ifndef DARKLANTERN_LIGHTING_POLICY_H
#define DARKLANTERN_LIGHTING_POLICY_H
#include "game.h"
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Shared renderer decisions. Initial preparation and a later door-state
 * change have different costs when the live-prepared two-state cache is used. */
typedef enum {
    DL_LIGHT_INLINE_TWO_STATE, DL_LIGHT_STANDALONE_BAKED, DL_LIGHT_LIVE_NIGHT,
    DL_LIGHT_ACTOR_BODY_PROBES, DL_LIGHT_OBJECTIVE_PROBE, DL_LIGHT_LEGACY_SCALAR,
    DL_LIGHT_LEGACY_ACTOR_PROBES, DL_LIGHT_LEGACY_OBJECTIVE_EMISSIVE,
    DL_LIGHT_RUNTIME_TWO_STATE, DL_LIGHT_INVALID, DL_LIGHT_MODE_COUNT
} DlLightingMode;
typedef enum {
    DL_LIGHT_INLINE_PAYLOAD, DL_LIGHT_STANDALONE_ASSET, DL_LIGHT_INDEPENDENT_DOORS,
    DL_LIGHT_GUARD, DL_LIGHT_OBJECTIVE, DL_LIGHT_ANIMATED_MESH,
    DL_LIGHT_NIGHT_WITHOUT_BAKE, DL_LIGHT_LEGACY_ENVIRONMENT, DL_LIGHT_DISABLED_BUILD,
    DL_LIGHT_STANDALONE_REJECTED, DL_LIGHT_INCOMPLETE_INLINE_PAYLOAD,
    DL_LIGHT_CACHED_SINGLE_DOOR_STATES, DL_LIGHT_INVALID_MODEL, DL_LIGHT_REASON_COUNT
} DlLightingReason;
typedef struct {
    bool bake_enabled, standalone_loaded, standalone_rejected;
    uint32_t independent_doors;
} DlLightingSettings;
typedef struct {
    DlLightingMode mode, change_mode;
    DlLightingReason reason, change_reason;
    bool static_eligible, inline_eligible;
    uint32_t triangles;
} DlLightingDecision;
typedef struct { uint32_t models, triangles; } DlLightingCount;
typedef struct {
    DlLightingCount cases[DL_LIGHT_MODE_COUNT][DL_LIGHT_REASON_COUNT];
    uint32_t models, triangles, case_count;
} DlLightingPolicyReport;

uint32_t dl_lighting_independent_doors(const DlLevel *level);
bool dl_lighting_static_eligible(const DlLevel *level,int index);
/* The renderer already validates model/mesh indexes at preparation. Keep its
 * per-corner predicate inlined while the diagnostic API checks external input. */
static inline bool dl_lighting_inline_payload(bool enabled,bool night,uint32_t doors,const DlModelInstance *model){
    return enabled&&night&&doors<=1&&model->baked_colors[0]&&model->baked_colors[1];
}
bool dl_lighting_inline_eligible(const DlLevel *level,int index,bool enabled,uint32_t doors);
DlLightingDecision dl_lighting_decision(const DlLevel *level,int index,DlLightingSettings settings);
void dl_lighting_policy_report(const DlLevel *level,DlLightingSettings settings,
    bool changed,DlLightingPolicyReport *report);
const char *dl_lighting_mode_name(DlLightingMode mode);
const char *dl_lighting_reason_name(DlLightingReason reason);

typedef struct {
    uint64_t report;
    const char *build, *source, *package, *standalone;
    uint32_t revision,published,door_revision,doors;
    bool changed;
} DlLightingPolicyIdentity;
/* Bounded integer-only log encoding. A zero result means insufficient space;
 * callers must not transmit a partial record. No allocation or IO. */
size_t dl_lighting_policy_header_text(char *out,size_t capacity,
    const DlLightingPolicyIdentity *identity,const DlLightingPolicyReport *report);
size_t dl_lighting_policy_case_text(char *out,size_t capacity,uint64_t report,
    DlLightingMode mode,DlLightingReason reason,DlLightingCount count);
size_t dl_lighting_policy_cost_text(char *out,size_t capacity,uint64_t report,
    uint32_t ticks,uint32_t frequency);
#ifdef __cplusplus
}
#endif
#endif
