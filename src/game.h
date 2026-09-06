#ifndef DARKLANTERN_GAME_H
#define DARKLANTERN_GAME_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Right handed world: metres, +Y up, yaw zero faces +Z. Positions are feet. */
typedef struct { float x, y, z; } DlVec3;
typedef struct { float u, v; } DlVec2;
typedef struct { int8_t x, y, z; } DlNormal; /* normalized XYZ quantized to -127..127 */
typedef struct {
    DlVec3 position;
    float radius, intensity;
    DlVec3 color; /* linear RGB; all zero preserves legacy white lights */
} DlLight;
typedef struct {
    DlVec3 center, half_size;
    float yaw; /* radians; collision proxies remain upright */
    bool door;
} DlCollider;

typedef struct {
    const DlVec3 *vertices;
    int vertex_count;
    const uint16_t *indices; /* three indices per triangle */
    int index_count;
    const DlVec2 *uvs; /* optional; one UV per vertex, including duplicated seams */
    const DlNormal *normals; /* optional authored normals; NULL retains flat faces */
} DlMesh;

typedef enum { DL_MODEL_STATIC, DL_MODEL_GUARD, DL_MODEL_DOOR,
               DL_MODEL_CONTROL, DL_MODEL_OBJECTIVE } DlModelRole;
typedef struct {
    const char *id;
    uint16_t mesh;
    DlVec3 position, rotation, scale; /* Euler XYZ radians; scale then Rx, Ry, Rz */
    uint8_t color[4];
    DlModelRole role;
    int16_t texture; /* -1 means untextured; ignored when level has no textures */
    uint8_t emissive[3];
    bool double_sided;
    int16_t enemy_index; /* guard role: index in DlLevel.enemies; otherwise -1 */
    bool loot_highlight; /* opt-in presentation pulse; never a light or AI signal */
} DlModelInstance;

typedef struct { const char *path; uint16_t width, height; } DlTexture;
typedef struct {
    bool enabled; /* false preserves the first playable's scalar lighting */
    DlVec3 ambient, moon_direction, moon_color; /* direction points toward moon */
    float moon_intensity;
    DlVec3 fog_color;
    float fog_near, fog_far;
    DlVec3 sky_top, sky_bottom;
    float exposure; /* presentation only; never affects stealth visibility */
} DlEnvironment;

#define DL_MAX_ENEMIES 16
typedef enum {
#define DL_ENEMY_TYPE(symbol, id, label, speed, sight, hearing) DL_ENEMY_##symbol,
#include "enemy_types.def"
#undef DL_ENEMY_TYPE
} DlEnemyType;
typedef enum { DL_BEHAVIOR_PATROL, DL_BEHAVIOR_SENTRY } DlEnemyBehavior;
typedef struct {
    const char *id;
    DlEnemyType type;
    DlEnemyBehavior behavior;
    DlVec3 spawn;
    float yaw;
    const DlVec3 *patrol;
    int patrol_count;
    float speed, sight_range, hearing_range;
} DlEnemyDef;

typedef struct {
    const char *id, *label;
    DlVec3 position;
    float yaw, pitch; /* radians; source authoring uses degrees */
    bool door_open, crouched;
} DlStartPreset;

typedef struct {
    uint32_t version;
    const char *title;
    const DlCollider *colliders;
    int collider_count;
    const DlMesh *meshes;
    int mesh_count;
    const DlModelInstance *models;
    int model_count;
    DlVec3 spawn;
    float spawn_yaw;
    const DlEnemyDef *enemies;
    int enemy_count;
    DlVec3 control, objective;
    const DlLight *lights;
    int light_count;
    DlEnvironment environment;
    const DlTexture *textures;
    int texture_count;
    const DlStartPreset *test_starts;
    int test_start_count;
} DlLevel;

typedef enum { DL_PATROL, DL_INVESTIGATE, DL_SEARCH, DL_CHASE } DlGuardState;
typedef struct {
    DlVec3 position;
    float vertical_velocity, yaw, awareness;
    DlGuardState state;
    int patrol_index;
    DlVec3 investigate_target;
    float search_timer;
    bool sees_player, heard_sound;
} DlEnemy;
typedef enum { DL_EVENT_NONE, DL_EVENT_STEP, DL_EVENT_NOISE, DL_EVENT_DOOR,
               DL_EVENT_DETECTED, DL_EVENT_OBJECTIVE } DlEvent;

typedef struct {
    float forward, strafe, turn, look;
    bool crouch, use, noise, restart, jump;
} DlInput;

typedef struct {
    const DlLevel *level;
    DlVec3 player;
    float yaw, pitch, vertical_velocity;
    bool grounded, crouched, door_open, complete, caught;
    DlEnemy enemies[DL_MAX_ENEMIES];
    int enemy_count;
    float visibility;
    DlVec3 last_sound;
    float footstep_timer, sound_age, elapsed;
    uint32_t sound_serial;
    DlEvent event;
} DlGame;

void dl_game_init(DlGame *game, const DlLevel *level);
/* Reset the full world at the ordinary spawn (-1) or an authored test start.
 * An invalid selection leaves the existing game untouched. */
bool dl_game_start(DlGame *game, const DlLevel *level, int preset_index);
void dl_game_update(DlGame *game, const DlInput *input, float dt);
DlVec3 dl_player_eye(const DlGame *game);
bool dl_position_clear(const DlGame *game, DlVec3 feet, float radius, float height);
bool dl_line_of_sight(const DlGame *game, DlVec3 a, DlVec3 b);
float dl_visibility(const DlGame *game, DlVec3 position);
/* Linear RGB irradiance: low ambient + occluded moon/point Lambert diffuse.
 * Callers offset surface positions out of their own collision proxy. */
DlVec3 dl_surface_light(const DlGame *game, DlVec3 position, DlVec3 normal);
const char *dl_guard_state_name(DlGuardState state);
#ifdef DL_SCALE_BENCH
/* Diagnostic entry point: one existing guard substep, without player update,
 * visibility sampling, rendering, or a terminal-game early exit. */
void dl_game_benchmark_guard_step(DlGame *game, float dt);
#endif

#ifdef __cplusplus
}
#endif
#endif
