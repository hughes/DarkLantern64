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
} DlStreamMaterial;
typedef struct DlStreamDefinition {
    const char *package_path;
    uint32_t directory_bytes, file_bytes, max_region_bytes, region_count;
    const uint32_t *model_entity_ids, *object_entity_ids, *enemy_entity_ids;
    const DlStreamMaterial *materials;
    uint32_t material_count;
} DlStreamDefinition;

enum {
    DL_STREAM_SCENE_MAX_REGIONS = 32,
    DL_STREAM_SCENE_MAX_RESIDENT_REGIONS = 2,
    DL_STREAM_SCENE_MAX_ENTITIES = 256,
    DL_STREAM_SCENE_MAX_TRIANGLES = 512,
    DL_STREAM_SCENE_MAX_GROUPS = 16,
    DL_STREAM_SCENE_MAX_MATERIALS = 64,
    DL_STREAM_SCENE_MAX_COLLIDERS = 256,
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
} DlStreamSceneGroup;
typedef struct {
    DlLevel level;
    DlVec3 vertices[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    uint16_t indices[DL_STREAM_SCENE_MAX_TRIANGLES*3];
    DlMesh meshes[DL_STREAM_SCENE_MAX_MESHES];
    DlModelInstance models[DL_MAX_MODELS];
    DlCollider colliders[DL_STREAM_SCENE_MAX_COLLIDERS];
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
uint16_t dl_stream_scene_enemy_mask(const DlStreamScene *scene, const DlGame *game);
bool dl_stream_scene_region_resident(const DlStreamScene *scene, uint32_t region_id);
const char *dl_stream_scene_status_name(DlStreamSceneStatus status);

#ifdef __cplusplus
}
#endif
#endif
