#ifndef DARKLANTERN_STREAM_SCENE_H
#define DARKLANTERN_STREAM_SCENE_H

#include "game.h"
#include "content_limits.h"
#include "world_stream.h"

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
} DlStreamSceneGroup;
typedef struct {
    DlLevel level;
    DlVec3 vertices[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlVec2 uvs[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlNormal normals[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    uint16_t indices[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlMesh meshes[DL_STREAM_SCENE_MAX_MESHES];
    DlModelInstance models[DL_MAX_MODELS];
    DlCollider colliders[DL_STREAM_SCENE_MAX_COLLIDERS];
    DlFloorTriangle floors[DL_STREAM_SCENE_MAX_FLOORS];
    int16_t collider_objects[DL_STREAM_SCENE_MAX_COLLIDERS];
    DlStreamSceneGroup groups[DL_STREAM_SCENE_MAX_GROUPS];
    uint32_t resident_ids[DL_STREAM_SCENE_MAX_RESIDENT_REGIONS];
    uint32_t resident_count;
    int group_count, triangle_count;
} DlStreamSceneBank;
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
} DlStreamScene;

DlStreamSceneStatus dl_stream_scene_init(DlStreamScene *scene, const DlLevel *bootstrap,
    const DlMission *mission);
/* Invalidate/release renderer resources first. This function never resets
 * gameplay. On failure the old working level remains intact. game may be NULL
 * for initial loading; thereafter use it to locate guards at current positions. */
DlStreamSceneStatus dl_stream_scene_refresh(DlStreamScene *scene, DlWorldStream *stream,
    const DlGame *game);
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
