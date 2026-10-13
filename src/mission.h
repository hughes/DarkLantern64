#ifndef DARKLANTERN_MISSION_H
#define DARKLANTERN_MISSION_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    DL_MISSION_VERSION = 1,
    DL_MISSION_HEADER_BYTES = 64,
    DL_MISSION_REGION_BYTES = 64,
    DL_MISSION_ENTITY_BYTES = 16,
    DL_MISSION_CHUNK_HEADER_BYTES = 32,
    DL_MISSION_SECTION_BYTES = 24,
    DL_MISSION_MAX_SECTIONS = 3,
    DL_MISSION_TRIANGLE_BYTES = 44,
    DL_MISSION_COLLIDER_BYTES = 32
};

typedef enum {
    DL_MISSION_OK = 0, DL_MISSION_ARGUMENT, DL_MISSION_TRUNCATED,
    DL_MISSION_HEADER, DL_MISSION_CHECKSUM, DL_MISSION_LAYOUT,
    DL_MISSION_REFERENCE, DL_MISSION_VALUE, DL_MISSION_NOT_FOUND
} DlMissionStatus;

typedef enum {
    DL_MISSION_ENTITY_GENERIC = 0, DL_MISSION_ENTITY_GUARD = 1,
    DL_MISSION_ENTITY_DOOR = 2, DL_MISSION_ENTITY_LOOT = 3,
    DL_MISSION_ENTITY_OBJECTIVE = 4
} DlMissionEntityKind;

typedef enum {
    DL_MISSION_SECTION_TRIANGLES = 1,
    DL_MISSION_SECTION_COLLIDERS = 2,
    DL_MISSION_SECTION_ENTITIES = 3
} DlMissionSectionKind;

typedef struct { float x, y, z; } DlMissionVec3;
typedef struct {
    uint32_t id, chunk_offset, chunk_bytes, decoded_bytes, chunk_crc32;
    uint32_t first_dependency, dependency_count;
    DlMissionVec3 minimum, maximum;
} DlMissionRegion;
typedef struct { uint32_t id, home_region_id; DlMissionEntityKind kind; } DlMissionEntity;

/* Borrowed directory view. The caller keeps these bytes alive and immutable.
 * identity is copied; chunks are outside this view and need not be resident. */
typedef struct {
    const uint8_t *directory;
    size_t directory_bytes;
    uint32_t file_bytes, region_count, entity_count, entry_region_id;
    uint32_t dependency_count, dependency_offset;
    uint8_t identity[32];
} DlMission;

typedef struct {
    DlMissionSectionKind kind;
    uint32_t offset, bytes, count, record_bytes;
} DlMissionSection;
/* Structural view only: opening a chunk does not scan records or check CRC.
 * Caller validates the complete chunk CRC before publishing this view. Lazy
 * consumers must validate records before using them, including exactly-once
 * coverage of home entities when installing an entity-ID section. */
typedef struct {
    const uint8_t *data;
    size_t bytes;
    DlMissionVec3 origin;
    uint32_t section_count;
    DlMissionSection sections[DL_MISSION_MAX_SECTIONS];
} DlMissionChunk;
typedef struct {
    DlMissionVec3 positions[3];
    uint32_t material_id, surface_id;
} DlMissionTriangle;
typedef struct {
    DlMissionVec3 center, half_size;
    float yaw;
    uint32_t entity_id; /* zero: no association; otherwise a global entity ID */
} DlMissionCollider;

/* Failed open clears the output view. Other getters leave outputs untouched
 * on failure. All indexes except IDs are zero-based. No function allocates. */
DlMissionStatus dl_mission_open(const void *directory, size_t directory_bytes,
    size_t actual_file_bytes, DlMission *mission);
DlMissionStatus dl_mission_region(const DlMission *mission, uint32_t id, DlMissionRegion *region);
DlMissionStatus dl_mission_entity(const DlMission *mission, uint32_t id, DlMissionEntity *entity);
DlMissionStatus dl_mission_dependency(const DlMission *mission, uint32_t region_id,
    uint32_t index, uint32_t *dependency_id);
DlMissionStatus dl_mission_chunk_open(const void *data, size_t bytes, DlMissionChunk *chunk);
DlMissionStatus dl_mission_section(const DlMissionChunk *chunk, DlMissionSectionKind kind,
    DlMissionSection *section);
DlMissionStatus dl_mission_triangle(const DlMissionChunk *chunk, uint32_t index,
    DlMissionTriangle *triangle);
DlMissionStatus dl_mission_collider(const DlMissionChunk *chunk, const DlMission *mission,
    uint32_t index, DlMissionCollider *collider);
DlMissionStatus dl_mission_entity_id(const DlMissionChunk *chunk, const DlMission *mission,
    uint32_t region_id, uint32_t index, uint32_t *entity_id);
/* IEEE CRC32: begin with UINT32_MAX; finalize with xor UINT32_MAX. */
uint32_t dl_mission_crc32_update(uint32_t crc, const void *bytes, size_t size);
const char *dl_mission_status_name(DlMissionStatus status);

#ifdef __cplusplus
}
#endif
#endif
