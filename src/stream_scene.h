#ifndef DARKLANTERN_STREAM_SCENE_H
#define DARKLANTERN_STREAM_SCENE_H

#include "game.h"
#include "content_limits.h"
#include "world_stream.h"
#ifndef DL_RUNTIME_DIAGNOSTICS
#define DL_RUNTIME_DIAGNOSTICS DL_RUNTIME_TRACE
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t id;
    uint8_t color[4];
    bool double_sided;
    uint16_t texture_id; /* zero: untextured; otherwise bootstrap texture index + 1 */
} DlStreamMaterial;
typedef enum { DL_STREAM_POLICY_LEGACY=0, DL_STREAM_POLICY_AUTHORED=1 } DlStreamPolicy;
typedef struct {
    DlVec3 activation_minimum, activation_maximum;
    uint32_t working_mask;
} DlStreamPolicyRegion;
typedef struct {
    uint32_t from_region, to_region;
    DlVec3 minimum, maximum;
    uint32_t door_entity_id; /* zero for an open gate; otherwise a global door */
} DlStreamTransition;
typedef struct {
    uint32_t current_region,pending_transition; /* pending is gate index+1; zero is none */
    DlVec3 previous_position;
    bool has_previous_position;
    DlVec3 approach_position, approach_direction;
    float approach_reverse_distance;
    bool has_approach_position, has_approach_direction;
} DlStreamPolicyState;
typedef struct DlStreamDefinition {
    const char *package_path;
    uint32_t directory_bytes, file_bytes, max_region_bytes, region_count;
    const uint32_t *model_entity_ids, *object_entity_ids, *enemy_entity_ids;
    const DlStreamMaterial *materials;
    uint32_t material_count;
    bool keep_neighbors_resident; /* conservative open sightlines; default probe policy is false */
    DlStreamPolicy policy;
    const DlStreamPolicyRegion *policy_regions; /* dense region ID - 1 */
    const DlStreamTransition *transitions;
    uint32_t transition_count, max_resident_regions;
    const char *package_sha256; /* binds optional traces to the authored ID sidecar */
} DlStreamDefinition;

enum {
    DL_STREAM_SCENE_MAX_REGIONS = 32,
    DL_STREAM_SCENE_MAX_RESIDENT_REGIONS = 5,
    DL_STREAM_SCENE_MAX_ENTITIES = 2048,
    DL_STREAM_SCENE_MAX_TRIANGLES = 1440,
    DL_STREAM_SCENE_MAX_GROUPS = 64,
    DL_STREAM_SCENE_MAX_MATERIALS = 64,
    DL_STREAM_SCENE_MAX_COLLIDERS = 400,
    DL_STREAM_SCENE_MAX_FLOORS = 128,
    DL_STREAM_SCENE_MAX_MESHES = DL_MAX_MODELS + DL_STREAM_SCENE_MAX_GROUPS
};
typedef enum {
    DL_STREAM_SCENE_OK, DL_STREAM_SCENE_ARGUMENT, DL_STREAM_SCENE_CAPACITY,
    DL_STREAM_SCENE_CORRUPT, DL_STREAM_SCENE_NOT_READY
} DlStreamSceneStatus;
typedef struct {
    uint32_t region_id, material_id;
    int first_vertex, triangle_count, written_triangles;
    DlVec3 origin;
    uint8_t attribute_flags;
    bool baked;
} DlStreamSceneGroup;
typedef struct {
    DlLevel level;
    DlVec3 vertices[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlVec2 uvs[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlNormal normals[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    uint8_t baked_colors[2][DL_STREAM_SCENE_MAX_TRIANGLES][24];
    uint16_t indices[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlMesh meshes[DL_STREAM_SCENE_MAX_MESHES];
    DlModelInstance models[DL_MAX_MODELS];
    DlCollider colliders[DL_STREAM_SCENE_MAX_COLLIDERS];
    DlFloorTriangle floors[DL_STREAM_SCENE_MAX_FLOORS];
    int16_t collider_objects[DL_STREAM_SCENE_MAX_COLLIDERS];
    DlStreamSceneGroup groups[DL_STREAM_SCENE_MAX_GROUPS];
    uint32_t resident_ids[DL_STREAM_SCENE_MAX_RESIDENT_REGIONS];
    uint32_t resident_count;
    int group_count, triangle_count, last_group;
    DlQueryCache query_cache;
#if DL_RUNTIME_DIAGNOSTICS
    /* Cold provenance follows all hot fields. Existing caps bound entity to
     * 12 bits, region to 6 and authored collider ordinal+1 to 9. Gate keeps its
     * complete u32 domain; bit 59 distinguishes synthetic barriers. */
    uint64_t collider_sources[DL_STREAM_SCENE_MAX_COLLIDERS];
#endif
} DlStreamSceneBank;
typedef struct {
    DlWorldStream *stream;
    DlRegionHandle handles[DL_STREAM_SCENE_MAX_RESIDENT_REGIONS];
    const DlMissionChunk *chunks[DL_STREAM_SCENE_MAX_RESIDENT_REGIONS];
    DlVec3 enemy_positions[DL_MAX_ENEMIES];
    bool entities[DL_STREAM_SCENE_MAX_ENTITIES];
    size_t borrowed;
    uint32_t phase, chunk, section, record, entity, index, resident_mask;
    int vertex, instanced_vertices, instanced_triangles;
    bool active, has_game, contiguous_groups;
} DlStreamSceneAssembly;
/* Two bounded banks permit transactional refresh; neither bank refers into
 * streamed slot buffers after refresh. Bootstrap shared assets stay borrowed.
 * Door visuals remain shared/resident across all banks, independently of home.
 * Caller owns this storage, the bootstrap and the mission directory. */
typedef struct {
    const DlLevel *bootstrap;
    const DlMission *mission;
    DlStreamSceneBank banks[2];
    unsigned active_bank;
    uint32_t revision;
    DlStreamSceneAssembly assembly;
    bool pending_ready;
} DlStreamScene;

DlStreamSceneStatus dl_stream_scene_init(DlStreamScene *scene, const DlLevel *bootstrap,
    const DlMission *mission);
/* Invalidate/release renderer resources first. This function never resets
 * gameplay. On failure the old working level remains intact. game may be NULL
 * for initial loading; thereafter use it to locate guards at current positions. */
DlStreamSceneStatus dl_stream_scene_refresh(DlStreamScene *scene, DlWorldStream *stream,
    const DlGame *game);
/* Prepare the inactive bank while the published bank remains drawable. */
DlStreamSceneStatus dl_stream_scene_prepare(DlStreamScene *scene, DlWorldStream *stream,
    const DlGame *game);
/* Snapshot resident chunks and guard positions. CPU borrows remain owned until
 * completion, failure or cancel. The manager/directory must stay alive. Each
 * step visits at most record_budget records, including coverage/finalization;
 * no SDK clock is required. Zero budget performs no work. pending() becomes
 * available only after ready=true; publish never exposes an incomplete bank. */
DlStreamSceneStatus dl_stream_scene_prepare_begin(DlStreamScene *scene, DlWorldStream *stream,
    const DlGame *game);
/* Select a subset of physically resident chunks. Mask bit region_id - 1 must
 * name an existing mission region; unavailable requested chunks return
 * NOT_READY before any borrow or bank mutation. Zero selects an empty view. */
DlStreamSceneStatus dl_stream_scene_prepare_begin_mask(DlStreamScene *scene, DlWorldStream *stream,
    const DlGame *game, uint32_t resident_mask);
DlStreamSceneStatus dl_stream_scene_prepare_step(DlStreamScene *scene, size_t record_budget,
    bool *ready);
void dl_stream_scene_prepare_cancel(DlStreamScene *scene);
const DlLevel *dl_stream_scene_pending(const DlStreamScene *scene);
/* Before publishing a prepared view, verify that moving guards still require
 * the same authored visual rows as the begin snapshot. No capacity is added. */
bool dl_stream_scene_pending_guards_match(const DlStreamScene *scene,const DlGame *game);
void dl_stream_scene_publish(DlStreamScene *scene);
const DlLevel *dl_stream_scene_level(const DlStreamScene *scene);
/* Conservative bounds locator; overlapping resident regions take precedence.
 * Zero denotes no containing region. These bounds are not a portal locator. */
uint32_t dl_stream_scene_region_at(const DlStreamScene *scene, DlVec3 position);
uint32_t dl_stream_scene_region_at_preferred(const DlStreamScene *scene, DlVec3 position,
    uint32_t preferred_region);
uint32_t dl_stream_scene_working_mask(const DlStreamScene *scene, uint32_t region_id);
/* Authored stable set plus one nearby target. This convenience form has no
 * hysteresis; platform scheduling should retain DlStreamPolicyState instead. */
uint32_t dl_stream_scene_demand(const DlStreamScene *scene, DlVec3 position,
    uint32_t current_region);
uint32_t dl_stream_scene_policy_update(const DlStreamScene *scene,DlVec3 position,
    uint32_t resident_mask,DlStreamPolicyState *state);
/* New mission, restart and teleport recovery must discard the old movement
 * segment. The next update locates its start without crossing stale gates. */
void dl_stream_scene_policy_reset(DlStreamPolicyState *state);
bool dl_stream_scene_transition_at(const DlStreamScene *scene, uint32_t from_region,
    uint32_t to_region, DlVec3 position);
bool dl_stream_scene_gate_waiting(const DlStreamScene *scene, DlVec3 position,
    uint32_t resident_mask);
uint32_t dl_stream_scene_door_target(const DlStreamScene *scene,uint32_t current_region,
    uint32_t door_entity_id,uint32_t *transition);
uint16_t dl_stream_scene_enemy_mask(const DlStreamScene *scene, const DlGame *game);
bool dl_stream_scene_region_resident(const DlStreamScene *scene, uint32_t region_id);
const char *dl_stream_scene_status_name(DlStreamSceneStatus status);

#ifdef __cplusplus
}
#endif
#endif
