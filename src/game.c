#include "game.h"
#include <float.h>
#include <math.h>
#include <string.h>

#define DL_PI 3.14159265358979323846f
#define PLAYER_RADIUS 0.18f
#define GUARD_RADIUS 0.20f
#define STAND_HEIGHT 1.65f
#define CROUCH_HEIGHT 0.95f
#define STEP_HEIGHT 0.28f
#define EPSILON 0.0001f
#define MAX_COLLIDERS 400

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }
static float horizontal_distance(DlVec3 a, DlVec3 b) {
    float x = b.x - a.x, z = b.z - a.z;
    return sqrtf(x * x + z * z);
}
static float distance_between(DlVec3 a, DlVec3 b) {
    float x = b.x - a.x, y = b.y - a.y, z = b.z - a.z;
    return sqrtf(x * x + y * y + z * z);
}
static bool finite_position(DlVec3 p) { return isfinite(p.x) && isfinite(p.y) && isfinite(p.z); }
static bool enemy_active(const DlGame *g, int index) {
    return (g->active_enemy_mask & (UINT16_C(1) << index)) != 0;
}
static float wrap_yaw(float yaw) {
    if (!isfinite(yaw)) return 0;
    yaw = fmodf(yaw, 2.0f * DL_PI);
    return yaw < 0 ? yaw + 2.0f * DL_PI : yaw;
}
static int collider_count(const DlGame *g) {
    if (!g || !g->level || !g->level->colliders) return 0;
    int count = g->level->collider_count;
    return count < 0 ? 0 : count > MAX_COLLIDERS ? MAX_COLLIDERS : count;
}
static int first_door(const DlGame *g) {
    for(int i=0;g->level->objects&&i<g->level->object_count&&i<DL_MAX_OBJECTS;++i)
        if(g->level->objects[i].kind==DL_OBJECT_DOOR)return i;
    return -1;
}
bool dl_game_object_open(const DlGame *g,int object) {
    if(object<0||object>=DL_MAX_OBJECTS)return false;
    return object==first_door(g)?g->door_open:(g->open_objects&(UINT64_C(1)<<object))!=0;
}
static bool active(const DlGame *g, const DlCollider *box) {
    if(!box->door)return true;
    if(!g->level->collider_objects)return !g->door_open;
    return !dl_game_object_open(g,g->level->collider_objects[box-g->level->colliders]);
}
static int model_object(const DlGame *g,const DlModelInstance *model) {
    for(int i=0;g->level->objects&&i<g->level->object_count&&i<DL_MAX_OBJECTS;++i)
        if(model->id&&strcmp(model->id,g->level->objects[i].id)==0)return i;
    return -1;
}
bool dl_game_model_open(const DlGame *g,const DlModelInstance *model) {
    if(model->role!=DL_MODEL_DOOR)return false;
    int object=model_object(g,model);
    return object<0?g->door_open:dl_game_object_open(g,object);
}
bool dl_game_model_visible(const DlGame *g,const DlModelInstance *model) {
    if(model->role!=DL_MODEL_LOOT&&model->role!=DL_MODEL_OBJECTIVE)return true;
    int object=model_object(g,model);
    if(object>=0&&model->role==DL_MODEL_LOOT)return !(g->collected_objects&(UINT64_C(1)<<object));
    if(object>=0&&model->role==DL_MODEL_OBJECTIVE)return !(g->completed_objectives&(UINT64_C(1)<<object));
    return !(model->role==DL_MODEL_OBJECTIVE&&g->complete);
}
uint32_t dl_game_lighting_revision(const DlGame *g){return g->object_revision*2+(g->door_open?1:0);}
static void rotation(const DlCollider *box, float *c, float *s) {
    *c = box->yaw == 0 ? 1.0f : cosf(box->yaw);
    *s = box->yaw == 0 ? 0.0f : sinf(box->yaw);
}
static DlVec3 local_point(const DlCollider *box, DlVec3 p) {
    DlVec3 offset = {p.x - box->center.x, p.y - box->center.y, p.z - box->center.z};
    if (box->yaw == 0) return offset;
    float c, s; rotation(box, &c, &s);
    return (DlVec3){c * offset.x - s * offset.z, offset.y, s * offset.x + c * offset.z};
}
static bool horizontal_overlap(const DlCollider *box, DlVec3 feet, float radius) {
    DlVec3 p = local_point(box, feet);
    float x = p.x - clampf(p.x, -box->half_size.x, box->half_size.x);
    float z = p.z - clampf(p.z, -box->half_size.z, box->half_size.z);
    return x * x + z * z < radius * radius;
}
static bool vertical_overlap(const DlCollider *box, DlVec3 feet, float height) {
    return feet.y + height > box->center.y - box->half_size.y + EPSILON &&
           feet.y < box->center.y + box->half_size.y - EPSILON;
}
static int floor_count(const DlGame *g) {
    return g->level->floors && g->level->floor_count > 0 ?
        (g->level->floor_count < DL_MAX_FLOORS ? g->level->floor_count : DL_MAX_FLOORS) : 0;
}
/* Barycentric coordinates on the XZ projection, with an exact interpolated
 * plane height. Vertical/degenerate and malformed native data supplies nothing. */
static bool floor_coordinates(const DlFloorTriangle *floor, DlVec3 point,
                              float *height, float *u, float *v) {
    for (int i = 0; i < 3; ++i) if (!finite_position(floor->vertices[i])) return false;
    DlVec3 a = floor->vertices[0], b = floor->vertices[1], c = floor->vertices[2];
    float bx = b.x - a.x, bz = b.z - a.z, cx = c.x - a.x, cz = c.z - a.z;
    float by = b.y - a.y, cy = c.y - a.y, det = bx * cz - bz * cx;
    if (!isfinite(det) || fabsf(det) <= 0.00000001f || !isfinite(by) || !isfinite(cy)) return false;
    float gradient_x = (by * cz - bz * cy) / det, gradient_z = (bx * cy - by * cx) / det;
    if (!isfinite(gradient_x) || !isfinite(gradient_z) ||
        gradient_x * gradient_x + gradient_z * gradient_z > 1.000001f) return false;
    float x = point.x - a.x, z = point.z - a.z;
    float first = (x * cz - z * cx) / det, second = (bx * z - bz * x) / det;
    float y = a.y + first * by + second * cy;
    if (!isfinite(first) || !isfinite(second) || !isfinite(y)) return false;
    *height = y; *u = first; *v = second;
    return true;
}
static bool floor_height(const DlFloorTriangle *floor, DlVec3 point, float *height) {
    /* Most bank floors are far from an actor; reject their XZ bounds before
     * plane divisions. Shared-edge ulps still reach barycentric containment. */
    float min_x = floor->vertices[0].x, max_x = min_x, min_z = floor->vertices[0].z, max_z = min_z;
    for (int i = 1; i < 3; ++i) {
        DlVec3 v = floor->vertices[i];
        if (v.x < min_x) min_x = v.x;
        if (v.x > max_x) max_x = v.x;
        if (v.z < min_z) min_z = v.z;
        if (v.z > max_z) max_z = v.z;
    }
    float margin_x = 4 * FLT_EPSILON * (fabsf(point.x) + fabsf(min_x) + fabsf(max_x) + 1);
    float margin_z = 4 * FLT_EPSILON * (fabsf(point.z) + fabsf(min_z) + fabsf(max_z) + 1);
    if (point.x < min_x - margin_x || point.x > max_x + margin_x ||
        point.z < min_z - margin_z || point.z > max_z + margin_z) return false;
    float u, v;
    if (!floor_coordinates(floor, point, height, &u, &v)) return false;
    /* Only a few arithmetic ulps at shared edges; no footprint expansion tied
     * to actor radius, bounds hull, proxy thickness or a stair approximation. */
    float margin = 4 * FLT_EPSILON;
    return u >= -margin && v >= -margin && u + v <= 1 + margin;
}
static float floor_below(const DlGame *g, DlVec3 feet, float maximum_y) {
    float highest = -INFINITY;
    for (int i = 0; i < floor_count(g); ++i) {
        float y;
        if (floor_height(&g->level->floors[i], feet, &y) && y <= maximum_y + EPSILON && y > highest) highest = y;
    }
    return highest;
}
bool dl_position_clear(const DlGame *g, DlVec3 feet, float radius, float height) {
    if (!g || !g->level || !finite_position(feet) || !isfinite(radius) ||
        !isfinite(height) || radius <= 0 || height <= 0) return false;
    for (int i = 0; i < collider_count(g); ++i) {
        const DlCollider *box = &g->level->colliders[i];
        if (active(g, box) && vertical_overlap(box, feet, height) &&
            horizontal_overlap(box, feet, radius)) return false;
    }
    for (int i = 0; i < floor_count(g); ++i) {
        float y;
        if (floor_height(&g->level->floors[i], feet, &y) && feet.y < y - EPSILON &&
            feet.y + height > y + EPSILON) return false;
    }
    return true;
}
static bool outside_slab(float a, float b, float half_size) {
    /* Only reject clear separation. The original division-based narrow phase
     * can round a just-outside endpoint onto t=1, so leave contacts within a
     * few arithmetic ulps to it rather than changing existing grazing rays. */
    float margin = 4 * FLT_EPSILON * (fabsf(a) + fabsf(b) + fabsf(half_size) + 1);
    return (a < -half_size - margin && b < -half_size - margin) ||
           (a > half_size + margin && b > half_size + margin);
}
static bool ray_hits_box(const DlCollider *box, DlVec3 a, DlVec3 b) {
    DlVec3 p = {a.x - box->center.x, a.y - box->center.y, a.z - box->center.z};
    DlVec3 q = {b.x - box->center.x, b.y - box->center.y, b.z - box->center.z};
    /* A segment with both endpoints outside one side cannot enter that slab.
     * Reject separate floors/ceilings before rotation or divisions. Equality
     * still reaches the closed-interval test, retaining endpoint occlusion. */
    if (outside_slab(p.y, q.y, box->half_size.y)) return false;
    /* Most authored proxies are axis aligned. Rotated proxies share one
     * sine/cosine pair across both endpoints of this segment. */
    if (box->yaw != 0) {
        float c, s; rotation(box, &c, &s);
        p = (DlVec3){c * p.x - s * p.z, p.y, s * p.x + c * p.z};
        q = (DlVec3){c * q.x - s * q.z, q.y, s * q.x + c * q.z};
    }
    if (outside_slab(p.x, q.x, box->half_size.x) ||
        outside_slab(p.z, q.z, box->half_size.z)) return false;
    float origin[3] = {p.x, p.y, p.z};
    float delta[3] = {q.x - p.x, q.y - p.y, q.z - p.z};
    float half[3] = {box->half_size.x, box->half_size.y, box->half_size.z};
    float entry = 0, exit = 1;
    for (int axis = 0; axis < 3; ++axis) {
        if (fabsf(delta[axis]) < 0.000001f) {
            if (origin[axis] < -half[axis] || origin[axis] > half[axis]) return false;
        } else {
            float lo = (-half[axis] - origin[axis]) / delta[axis];
            float hi = (half[axis] - origin[axis]) / delta[axis];
            if (lo > hi) { float temporary = lo; lo = hi; hi = temporary; }
            /* entry/exit start finite and these comparisons also retain the
             * prior fmaxf/fminf behavior if a malformed slab produces NaN.
             * Avoid two out-of-line libm calls and their spills per axis. */
            if (lo > entry) entry = lo;
            if (hi < exit) exit = hi;
            if (entry > exit) return false;
        }
    }
    return exit >= 0 && entry <= 1;
}
static bool line_of_sight_without_door(const DlGame *g, DlVec3 a, DlVec3 b, int object) {
    if (!g || !g->level || !finite_position(a) || !finite_position(b)) return false;
    for (int i = 0; i < collider_count(g); ++i) {
        const DlCollider *box = &g->level->colliders[i];
        if (object >= 0 && box->door && g->level->collider_objects &&
            g->level->collider_objects[i] == object) continue;
        if (active(g, box) && ray_hits_box(box, a, b)) return false;
    }
    for (int i = 0; i < floor_count(g); ++i) {
        const DlFloorTriangle *floor = &g->level->floors[i];
        float lo = floor->vertices[0].y, hi = lo;
        for (int j = 1; j < 3; ++j) {
            if (floor->vertices[j].y < lo) lo = floor->vertices[j].y;
            if (floor->vertices[j].y > hi) hi = floor->vertices[j].y;
        }
        if (a.y > hi && b.y > hi) continue;
        if (a.y < lo && b.y < lo) continue;
        float ay, by, au, av, bu, bv;
        if (!floor_coordinates(floor, a, &ay, &au, &av) ||
            !floor_coordinates(floor, b, &by, &bu, &bv)) continue;
        float pa = a.y - ay, pb = b.y - by;
        if (!isfinite(pa) || !isfinite(pb) || (pa > 0 && pb > 0) || (pa < 0 && pb < 0)) continue;
        float delta = pa - pb;
        if (delta != 0) {
            float t = pa / delta;
            DlVec3 contact = {a.x + t * (b.x - a.x), a.y + t * (b.y - a.y), a.z + t * (b.z - a.z)};
            float y;
            if (t >= 0 && t <= 1 && floor_height(floor, contact, &y)) return false;
        } else {
            /* Clip a coplanar ray against the three barycentric half spaces. */
            float entry = 0, exit = 1;
            float from[3] = {au, av, 1 - au - av}, to[3] = {bu, bv, 1 - bu - bv};
            for (int axis = 0; axis < 3 && entry <= exit; ++axis) {
                float d = to[axis] - from[axis];
                if (d == 0) { if (from[axis] < 0) entry = 2; }
                else if (d > 0) entry = fmaxf(entry, -from[axis] / d);
                else exit = fminf(exit, -from[axis] / d);
            }
            if (entry <= exit) return false;
        }
    }
    return true;
}
bool dl_line_of_sight(const DlGame *g, DlVec3 a, DlVec3 b) {
    return line_of_sight_without_door(g, a, b, -1);
}
DlVec3 dl_player_eye(const DlGame *g) {
    if (!g) return (DlVec3){0};
    DlVec3 eye = g->player; eye.y += g->crouched ? 0.80f : 1.50f; return eye;
}
static DlVec3 guard_eye(const DlEnemy *enemy) {
    DlVec3 eye = enemy->position; eye.y += 1.50f; return eye;
}
static DlVec3 light_color(DlVec3 value, bool legacy_white) {
    if (!finite_position(value)) return (DlVec3){0};
    if (legacy_white && value.x == 0 && value.y == 0 && value.z == 0)
        return (DlVec3){1, 1, 1};
    return (DlVec3){clampf(value.x, 0, 1), clampf(value.y, 0, 1), clampf(value.z, 0, 1)};
}
static DlVec3 unit_vector(DlVec3 value) {
    if (!finite_position(value)) return (DlVec3){0};
    float length2 = value.x * value.x + value.y * value.y + value.z * value.z;
    if (!isfinite(length2) || length2 < 0.00000001f) return (DlVec3){0};
    float inverse = 1 / sqrtf(length2);
    return (DlVec3){value.x * inverse, value.y * inverse, value.z * inverse};
}
static float diffuse(DlVec3 normal, DlVec3 direction, bool surface) {
    return surface ? fmaxf(0, normal.x * direction.x + normal.y * direction.y + normal.z * direction.z) : 1;
}
static void add_light(DlVec3 *result, DlVec3 color, float intensity) {
    result->x += color.x * intensity;
    result->y += color.y * intensity;
    result->z += color.z * intensity;
}
static DlVec3 incident_light(const DlGame *g, DlVec3 p, DlVec3 normal, bool surface) {
    const DlEnvironment *environment = &g->level->environment;
    DlVec3 result = environment->enabled ? light_color(environment->ambient, false) : (DlVec3){.08f, .08f, .08f};
    if (surface) normal = unit_vector(normal);
    if (environment->enabled && isfinite(environment->moon_intensity) && environment->moon_intensity > 0) {
        DlVec3 direction = unit_vector(environment->moon_direction);
        float facing = diffuse(normal, direction, surface);
        if (facing > 0 && (direction.x != 0 || direction.y != 0 || direction.z != 0)) {
            DlVec3 moon = {p.x + direction.x * 64, p.y + direction.y * 64, p.z + direction.z * 64};
            if (dl_line_of_sight(g, p, moon))
                add_light(&result, light_color(environment->moon_color, false),
                          clampf(environment->moon_intensity, 0, 16) * facing);
        }
    }
    for (int i = 0; g->level->lights && i < g->level->light_count; ++i) {
        const DlLight *light = &g->level->lights[i];
        if (!finite_position(light->position) || !isfinite(light->radius) ||
            !isfinite(light->intensity) || light->radius <= 0 || light->intensity <= 0) continue;
        DlVec3 delta = {light->position.x - p.x, light->position.y - p.y, light->position.z - p.z};
        float distance = distance_between(p, light->position);
        if (!isfinite(distance) || distance >= light->radius) continue;
        /* A probe exactly at the source has no defined direction: avoid a NaN
         * and use full influence, as the normal-independent visibility does. */
        float facing = distance < 0.0001f ? 1 : diffuse(normal,
            (DlVec3){delta.x / distance, delta.y / distance, delta.z / distance}, surface);
        if (facing <= 0 || !dl_line_of_sight(g, p, light->position)) continue;
        float falloff = 1 - distance / light->radius;
        add_light(&result, light_color(light->color, true),
                  clampf(light->intensity, 0, 16) * falloff * falloff * facing);
    }
    return result;
}
DlVec3 dl_surface_light(const DlGame *g, DlVec3 p, DlVec3 normal) {
    if (!g || !g->level || !finite_position(p) || !finite_position(normal)) return (DlVec3){0};
    return incident_light(g, p, normal, true);
}
float dl_visibility(const DlGame *g, DlVec3 p) {
    if (!g || !g->level || !finite_position(p)) return 0;
    if (g->level->environment.enabled) {
        /* Perception samples the same occluded sources at the player's eye,
         * without a surface normal. Exposure/fog/emission are display choices. */
        DlVec3 rgb = incident_light(g, p, (DlVec3){0}, false);
        return clampf(.2126f * rgb.x + .7152f * rgb.y + .0722f * rgb.z, 0, 1);
    }
    float amount = 0.08f;
    for (int i = 0; g->level->lights && i < g->level->light_count; ++i) {
        const DlLight *light = &g->level->lights[i];
        if (!finite_position(light->position) || !isfinite(light->radius) ||
            !isfinite(light->intensity) || light->radius <= 0 || light->intensity <= 0) continue;
        float distance = distance_between(p, light->position);
        if (distance >= light->radius || !dl_line_of_sight(g, p, light->position)) continue;
        float falloff = 1 - distance / light->radius;
        amount += light->intensity * falloff * falloff;
    }
    return clampf(amount, 0, 1);
}
static float support_below(const DlGame *g, DlVec3 feet, float radius, float maximum_y) {
    float highest = floor_below(g, feet, maximum_y);
    for (int i = 0; i < collider_count(g); ++i) {
        const DlCollider *box = &g->level->colliders[i];
        float top = box->center.y + box->half_size.y;
        if (active(g, box) && top <= maximum_y + EPSILON && top > highest &&
            horizontal_overlap(box, feet, radius)) highest = top;
    }
    return highest;
}
static bool supported(const DlGame *g, DlVec3 feet, float radius) {
    float ground = support_below(g, feet, radius, feet.y + EPSILON);
    return isfinite(ground) && fabsf(feet.y - ground) < 0.015f;
}
static void depenetrate(const DlGame *g, DlVec3 *feet, float radius, float height) {
    /* Actual OBB contact normals produce sliding along arbitrarily angled walls. */
    for (int pass = 0; pass < 6; ++pass) {
        bool changed = false;
        for (int i = 0; i < collider_count(g); ++i) {
            const DlCollider *box = &g->level->colliders[i];
            if (!active(g, box) || !vertical_overlap(box, *feet, height)) continue;
            DlVec3 local = local_point(box, *feet);
            float nx = local.x - clampf(local.x, -box->half_size.x, box->half_size.x);
            float nz = local.z - clampf(local.z, -box->half_size.z, box->half_size.z);
            float distance2 = nx * nx + nz * nz;
            if (distance2 >= radius * radius) continue;
            float penetration;
            if (distance2 > 0.00000001f) {
                float distance = sqrtf(distance2);
                nx /= distance; nz /= distance;
                penetration = radius - distance + EPSILON;
            } else {
                float xd = box->half_size.x - fabsf(local.x), zd = box->half_size.z - fabsf(local.z);
                if (xd < zd) { nx = local.x < 0 ? -1.0f : 1.0f; nz = 0; penetration = radius + xd + EPSILON; }
                else { nx = 0; nz = local.z < 0 ? -1.0f : 1.0f; penetration = radius + zd + EPSILON; }
            }
            float c, s; rotation(box, &c, &s);
            feet->x += (c * nx + s * nz) * penetration;
            feet->z += (-s * nx + c * nz) * penetration;
            changed = true;
        }
        if (!changed) return;
    }
}
static float move_horizontal(const DlGame *g, DlVec3 *feet, float dx, float dz,
                             float radius, float height, bool can_step) {
    DlVec3 before = *feet, candidate = {feet->x + dx, feet->y, feet->z + dz};
    float prior_floor = floor_below(g, before, before.y + EPSILON);
    float next_floor = floor_below(g, candidate, before.y + STEP_HEIGHT);
    if (can_step && (isfinite(next_floor) || (isfinite(prior_floor) && fabsf(prior_floor - before.y) < .015f))) {
        float floor = support_below(g, candidate, radius, before.y + STEP_HEIGHT);
        DlVec3 projected = {candidate.x, floor, candidate.z}, raised_start = {before.x, floor, before.z};
        if (isfinite(floor) && floor >= before.y - STEP_HEIGHT &&
            dl_position_clear(g, projected, radius, height) &&
            (floor <= before.y || dl_position_clear(g, raised_start, radius, height))) {
            *feet = projected;
            return horizontal_distance(before, *feet);
        }
    }
    if (dl_position_clear(g, candidate, radius, height)) *feet = candidate;
    else {
        float top = support_below(g, candidate, radius, feet->y + STEP_HEIGHT);
        DlVec3 raised = {candidate.x, top, candidate.z}, rise_start = {feet->x, top, feet->z};
        if (can_step && top > feet->y + EPSILON && dl_position_clear(g, raised, radius, height) &&
            dl_position_clear(g, rise_start, radius, height)) *feet = raised;
        else {
            depenetrate(g, &candidate, radius, height);
            if (dl_position_clear(g, candidate, radius, height)) *feet = candidate;
        }
    }
    return horizontal_distance(before, *feet);
}
static bool move_vertical(const DlGame *g, DlVec3 *feet, float *velocity,
                          float radius, float height, float dt) {
    *velocity = fmaxf(-18, *velocity - 12 * dt);
    float previous = feet->y, next = previous + *velocity * dt;
    bool grounded = false, ceiling = false;
    for (int i = 0; i < collider_count(g); ++i) {
        const DlCollider *box = &g->level->colliders[i];
        if (!active(g, box)) continue;
        float top = box->center.y + box->half_size.y, bottom = box->center.y - box->half_size.y;
        bool floor_crossing = *velocity <= 0 && previous >= top - EPSILON && next <= top;
        bool ceiling_crossing = *velocity > 0 && previous + height <= bottom + EPSILON && next + height >= bottom;
        if (!(floor_crossing || ceiling_crossing) || !horizontal_overlap(box, *feet, radius)) continue;
        if (floor_crossing) { next = top; grounded = true; }
        else { next = bottom - height; ceiling = true; }
    }
    for (int i = 0; i < floor_count(g); ++i) {
        float y;
        if (!floor_height(&g->level->floors[i], *feet, &y)) continue;
        if (*velocity <= 0 && previous >= y - EPSILON && next <= y) { next = y; grounded = true; }
        else if (*velocity > 0 && previous + height <= y + EPSILON && next + height >= y) {
            next = y - height; ceiling = true;
        }
    }
    if (grounded || ceiling) *velocity = 0;
    feet->y = next;
    return grounded;
}
static void move_guard(DlGame *g, DlEnemy *enemy, DlVec3 target, float speed, float dt) {
    float dx = target.x - enemy->position.x, dz = target.z - enemy->position.z;
    float distance = sqrtf(dx * dx + dz * dz);
    if (distance < 0.001f) return;
    enemy->yaw = atan2f(dx, dz);
    float step = fminf(speed * dt, distance);
    move_horizontal(g, &enemy->position, dx / distance * step, dz / distance * step,
                    GUARD_RADIUS, STAND_HEIGHT, supported(g, enemy->position, GUARD_RADIUS));
}
static void record_event(DlGame *g,DlEvent kind,DlVec3 p,float strength,int actor,int detail) {
    if(g->event_count>=DL_EVENT_CAPACITY){++g->event_overflow;return;}
    g->events[g->event_count++]=(DlWorldEvent){kind,p,g->elapsed,strength,(int16_t)actor,(int16_t)detail,-1};
}
static void record_transition(DlGame *g,int actor,DlGuardState before,DlGuardState after) {
    unsigned count=g->event_count;record_event(g,DL_EVENT_GUARD_STATE,g->enemies[actor].position,0,actor,after);
    if(g->event_count>count)g->events[count].previous=(int16_t)before;
}
static void emit_sound(DlGame *g, DlEvent event, DlVec3 p, float strength) {
    record_event(g,event,p,strength,-1,0);
    g->event = event; ++g->sound_serial; g->last_sound = p; g->sound_age = 0;
    for (int i = 0; i < g->enemy_count; ++i) g->enemies[i].heard_sound = false;
    if (strength <= 0 || g->complete || g->caught) return;
    for (int i = 0; i < g->enemy_count; ++i) {
        DlEnemy *enemy = &g->enemies[i];
        float range = fmaxf(0, g->level->enemies[i].hearing_range) * strength;
        DlVec3 ear = guard_eye(enemy);
        if (range <= 0 || distance_between(ear, p) > range) continue;
        /* An inactive listener may have no resident collision. This conservative
         * wake signal only uses distance; detailed occlusion resumes after its
         * region is available. Never treat missing geometry as a physics scene. */
        bool detailed = enemy_active(g, i);
        if (detailed && !dl_line_of_sight(g, ear, p)) range *= 0.30f;
        if (distance_between(ear, p) <= range) {
            enemy->heard_sound = true;
            if (enemy->state != DL_CHASE) {
                if(enemy->state!=DL_INVESTIGATE)record_transition(g,i,enemy->state,DL_INVESTIGATE);
                enemy->state = DL_INVESTIGATE; enemy->investigate_target = p;
                if (detailed) {
                    float floor = support_below(g, p, GUARD_RADIUS, p.y);
                    if (isfinite(floor)) enemy->investigate_target.y = floor;
                }
                enemy->search_timer = 6;
            }
        }
    }
}
static void nearest_patrol(DlEnemy *enemy, const DlEnemyDef *definition) {
    float nearest = INFINITY;
    for (int i = 0; definition->patrol && i < definition->patrol_count; ++i) {
        float distance = distance_between(enemy->position, definition->patrol[i]);
        if (distance < nearest) { nearest = distance; enemy->patrol_index = i; }
    }
}
static void init_enemy(DlEnemy *enemy, const DlEnemyDef *definition) {
    memset(enemy, 0, sizeof(*enemy));
    enemy->position = definition->spawn;
    enemy->yaw = wrap_yaw(definition->yaw);
    enemy->state = DL_PATROL;
    if (definition->behavior == DL_BEHAVIOR_PATROL && definition->patrol && definition->patrol_count > 0) {
        if (definition->patrol_count > 1 && distance_between(enemy->position, definition->patrol[0]) < 0.12f)
            enemy->patrol_index = 1;
        DlVec3 next = definition->patrol[enemy->patrol_index];
        enemy->yaw = atan2f(next.x - enemy->position.x, next.z - enemy->position.z);
    }
}
static void update_guard(DlGame *g, int index, float dt) {
    const DlEnemyDef *definition = &g->level->enemies[index];
    DlGuardState before=g->enemies[index].state;
    DlEnemy *enemy = &g->enemies[index];
    DlVec3 previous_position = enemy->position;
    DlVec3 eye = guard_eye(enemy), target = dl_player_eye(g);
    float distance = distance_between(eye, target), range = fmaxf(0, definition->sight_range);
    float dx = target.x - eye.x, dz = target.z - eye.z;
    float facing = distance > 0.001f ? (dx * sinf(enemy->yaw) + dz * cosf(enemy->yaw)) / distance : 1;
    float exposure = distance < 1.15f ? fmaxf(g->visibility, 0.60f) : g->visibility;
    float signal = range > 0 ? exposure * (1 - 0.65f * distance / range) : 0;
    enemy->sight_signal=signal;enemy->player_distance=distance;enemy->in_viewcone=facing>=0.573576f;
    enemy->unoccluded=range>0&&distance<=range&&enemy->in_viewcone&&signal>=.10f&&dl_line_of_sight(g,eye,target);
    enemy->sees_player=enemy->unoccluded;
    if (enemy->sees_player) {
        enemy->awareness = fminf(1, enemy->awareness + dt * signal * 1.8f);
        enemy->investigate_target = g->player; enemy->search_timer = 6;
        if (enemy->awareness >= 1 && enemy->state != DL_CHASE) {
            enemy->state = DL_CHASE; emit_sound(g, DL_EVENT_DETECTED, eye, 0);
        } else if (enemy->awareness >= 0.25f && enemy->state != DL_CHASE) enemy->state = DL_INVESTIGATE;
    } else enemy->awareness = fmaxf(0, enemy->awareness - dt * 0.25f);
    float speed = clampf(definition->speed, 0, 6);
    switch (enemy->state) {
    case DL_PATROL:
        if (definition->behavior == DL_BEHAVIOR_SENTRY) {
            if (horizontal_distance(enemy->position, definition->spawn) > 0.01f)
                move_guard(g, enemy, definition->spawn, speed, dt);
            else enemy->yaw = wrap_yaw(definition->yaw);
        } else if (definition->patrol && definition->patrol_count > 0) {
            enemy->patrol_index %= definition->patrol_count;
            DlVec3 next = definition->patrol[enemy->patrol_index];
            if (distance_between(enemy->position, next) < 0.12f) {
                enemy->patrol_index = (enemy->patrol_index + 1) % definition->patrol_count;
                next = definition->patrol[enemy->patrol_index];
            }
            move_guard(g, enemy, next, speed, dt);
        }
        break;
    case DL_INVESTIGATE:
    case DL_CHASE:
        if (!enemy->sees_player) enemy->search_timer -= dt;
        move_guard(g, enemy, enemy->investigate_target, speed * (enemy->state == DL_CHASE ? 1.45f : 1.1f), dt);
        if (enemy->state == DL_CHASE && distance_between(enemy->position, g->player) < 0.42f &&
            dl_line_of_sight(g, guard_eye(enemy), dl_player_eye(g))) g->caught = true;
        if (!enemy->sees_player && (enemy->search_timer <= 0 || distance_between(enemy->position, enemy->investigate_target) < 0.16f)) {
            enemy->state = DL_SEARCH; enemy->search_timer = 2.5f;
        }
        break;
    case DL_SEARCH:
        enemy->yaw = wrap_yaw(enemy->yaw + dt * 1.3f);
        enemy->search_timer -= dt;
        if (enemy->search_timer <= 0) { enemy->state = DL_PATROL; nearest_patrol(enemy, definition); }
        break;
    }
    move_vertical(g, &enemy->position, &enemy->vertical_velocity, GUARD_RADIUS, STAND_HEIGHT, dt);
    float travel = horizontal_distance(enemy->position, previous_position);
    float previous_distance=enemy->animation_distance;
    enemy->animation_distance += travel;
    const DlFootContacts *contacts=g->level->foot_contacts?&g->level->foot_contacts[index]:NULL;
    if(travel>0&&contacts&&contacts->stride>0&&contacts->count>0) {
        float from=previous_distance/contacts->stride,to=enemy->animation_distance/contacts->stride;
        for(int i=0;i<contacts->count&&i<8;++i) {
            float phase=contacts->phases[i]/65535.f;
            unsigned crossings=(unsigned)fmaxf(0,floorf(to-phase)-floorf(from-phase));
            unsigned available=DL_EVENT_CAPACITY-g->event_count;
            if(crossings>available){g->event_overflow+=crossings-available;crossings=available;}
            while(crossings--)record_event(g,DL_EVENT_GUARD_CONTACT,enemy->position,.12f,index,i);
        }
    } else if(travel>0&&floorf(enemy->animation_distance/.72f)>floorf(previous_distance/.72f))
        record_event(g,DL_EVENT_GUARD_CONTACT,enemy->position,.12f,index,0);
    float target_speed = dt > 0 ? travel / dt : 0;
    enemy->animation_speed += (target_speed - enemy->animation_speed) * fminf(1, dt * 12);
    /* A bad route must not repeatedly reset everyone else's progress. */
    if (enemy->position.y < -20) init_enemy(enemy, definition);
    if(enemy->state!=before)record_transition(g,index,before,enemy->state);
}
#ifdef DL_SCALE_BENCH
void dl_game_benchmark_guard_step(DlGame *g, float dt) {
    if (!g || !g->level || g->enemy_count == 0 || !enemy_active(g, 0) || !isfinite(dt) || dt <= 0) return;
    update_guard(g, 0, dt);
}
#endif

void dl_game_init(DlGame *g, const DlLevel *level) {
    if (!g) return;
    memset(g, 0, sizeof(*g)); g->level = level;
    if (!level) return;
    g->player = level->spawn; g->yaw = wrap_yaw(level->spawn_yaw);
    g->enemy_count = !level->enemies || level->enemy_count < 0 ? 0 :
        level->enemy_count > DL_MAX_ENEMIES ? DL_MAX_ENEMIES : level->enemy_count;
    dl_game_set_active_enemies(g, UINT16_MAX);
    for (int i = 0; i < g->enemy_count; ++i) init_enemy(&g->enemies[i], &level->enemies[i]);
    g->sound_age = 10;
    g->grounded = supported(g, g->player, PLAYER_RADIUS);
    g->visibility = dl_visibility(g, dl_player_eye(g));
}
void dl_game_set_active_enemies(DlGame *g, uint16_t mask) {
    if (!g) return;
    uint32_t valid = g->enemy_count <= 0 ? 0 :
        g->enemy_count >= DL_MAX_ENEMIES ? UINT16_MAX :
        (UINT32_C(1) << g->enemy_count) - 1;
    g->active_enemy_mask = (uint16_t)(mask & valid);
}
bool dl_game_start(DlGame *g, const DlLevel *level, int preset_index) {
    if (!g || !level || preset_index < -1 ||
        (preset_index >= 0 && (!level->test_starts || preset_index >= level->test_start_count))) return false;
    dl_game_init(g, level);
    if (preset_index >= 0) {
        const DlStartPreset *preset = &level->test_starts[preset_index];
        g->player = preset->position;
        g->yaw = wrap_yaw(preset->yaw); g->pitch = preset->pitch;
        g->door_open = preset->door_open; g->crouched = preset->crouched;
        if(preset->door_open)for(int i=0;level->objects&&i<level->object_count&&i<DL_MAX_OBJECTS;++i)
            if(level->objects[i].kind==DL_OBJECT_DOOR)g->open_objects|=UINT64_C(1)<<i;
        g->grounded = supported(g, g->player, PLAYER_RADIUS);
        g->visibility = dl_visibility(g, dl_player_eye(g)) * (g->crouched ? .55f : 1);
    }
    return true;
}
static bool door_occupied_object(const DlGame *g,int object) {
    float height = g->crouched ? CROUCH_HEIGHT : STAND_HEIGHT;
    for (int i = 0; i < collider_count(g); ++i) {
        const DlCollider *box = &g->level->colliders[i];
        if (!box->door) continue;
        if(object>=0&&g->level->collider_objects&&g->level->collider_objects[i]!=object)continue;
        if (vertical_overlap(box, g->player, height) && horizontal_overlap(box, g->player, PLAYER_RADIUS)) return true;
        for (int enemy = 0; enemy < g->enemy_count; ++enemy)
            if (enemy_active(g, enemy) && vertical_overlap(box, g->enemies[enemy].position, STAND_HEIGHT) &&
                horizontal_overlap(box, g->enemies[enemy].position, GUARD_RADIUS)) return true;
    }
    return false;
}
static void complete_objectives(DlGame *g) {
    uint64_t required=0;
    for(int i=0;i<g->level->object_count&&i<DL_MAX_OBJECTS;++i)
        if(g->level->objects[i].kind==DL_OBJECT_OBJECTIVE)required|=UINT64_C(1)<<i;
    if(required&&(g->completed_objectives&required)==required)g->complete=true;
}
static void use_objects(DlGame *g,bool zones) {
    DlVec3 eye=dl_player_eye(g);
    int chosen=-1;float nearest=INFINITY;
    for(int i=0;i<g->level->object_count&&i<DL_MAX_OBJECTS;++i) {
        const DlObjectDef *o=&g->level->objects[i];uint64_t bit=UINT64_C(1)<<i;
        if(o->zone!=zones)continue;
        if(o->kind==DL_OBJECT_LOOT&&(g->collected_objects&bit))continue;
        if(o->kind==DL_OBJECT_OBJECTIVE&&((g->completed_objectives&bit)||g->loot_value<o->required_loot))continue;
        float distance=distance_between(zones?g->player:eye,o->position);
        DlVec3 target=o->position;if(zones)target.y+=.1f; /* floor-authored zone origins */
        int ignore_door=o->kind==DL_OBJECT_DOOR?i:-1;
        if(distance<=o->radius&&distance<nearest&&line_of_sight_without_door(g,eye,target,ignore_door)){nearest=distance;chosen=i;}
    }
    if(chosen<0)return;
    const DlObjectDef *o=&g->level->objects[chosen];uint64_t bit=UINT64_C(1)<<chosen;
    if(o->kind==DL_OBJECT_DOOR) {
        bool opened=!dl_game_object_open(g,chosen);
        if(!opened&&door_occupied_object(g,chosen))return;
        if(opened)g->open_objects|=bit;else g->open_objects&=~bit;
        if(chosen==first_door(g))g->door_open=opened;
        ++g->object_revision;emit_sound(g,DL_EVENT_DOOR,o->position,.65f);
    } else if(o->kind==DL_OBJECT_CONTROL) {
        // Preflight all linked doors so a blocked closing door cannot partially
        // activate a multi-target control.
        for(int i=0;i<g->level->object_count&&i<DL_MAX_OBJECTS;++i)
            if((o->targets&(UINT64_C(1)<<i))&&dl_game_object_open(g,i)&&door_occupied_object(g,i))return;
        for(int i=0;i<g->level->object_count&&i<DL_MAX_OBJECTS;++i)if(o->targets&(UINT64_C(1)<<i)) {
            bool opened=!dl_game_object_open(g,i);
            if(opened)g->open_objects|=UINT64_C(1)<<i;else g->open_objects&=~(UINT64_C(1)<<i);
            if(i==first_door(g))g->door_open=opened;
        }
        ++g->object_revision;emit_sound(g,DL_EVENT_DOOR,o->position,.65f);
    } else if(o->kind==DL_OBJECT_LOOT) {
        g->collected_objects|=bit;g->loot_value+=o->value;
        emit_sound(g,DL_EVENT_OBJECTIVE,o->position,0);
    } else if(o->kind==DL_OBJECT_OBJECTIVE) {
        g->completed_objectives|=bit;complete_objectives(g);emit_sound(g,DL_EVENT_OBJECTIVE,o->position,0);
    }
}
static void use_nearby(DlGame *g) {
    if(g->level->objects){use_objects(g,false);return;}
    DlVec3 eye = dl_player_eye(g);
    if (distance_between(eye, g->level->objective) <= 1.5f && dl_line_of_sight(g, eye, g->level->objective)) {
        g->complete = true; emit_sound(g, DL_EVENT_OBJECTIVE, g->level->objective, 0);
    } else if (distance_between(eye, g->level->control) <= 1.5f && dl_line_of_sight(g, eye, g->level->control)) {
        if (g->door_open && door_occupied_object(g,-1)) return;
        g->door_open = !g->door_open; emit_sound(g, DL_EVENT_DOOR, g->level->control, 0.65f);
    }
}
void dl_game_update(DlGame *g, const DlInput *input, float dt) {
    if (!g || !g->level || !input) return;
    g->event_count=0;
    if (input->restart) { dl_game_init(g, g->level); return; }
    if (!isfinite(dt) || dt <= 0 || g->complete || g->caught) return;
    dt = fminf(dt, 0.25f);
    if (input->crouch) g->crouched = true;
    else if (dl_position_clear(g, g->player, PLAYER_RADIUS, STAND_HEIGHT)) g->crouched = false;
    if (input->use) use_nearby(g);
    if(g->level->objects)use_objects(g,true);
    if (g->complete) return;
    if (input->noise) emit_sound(g, DL_EVENT_NOISE, dl_player_eye(g), 1);
    if (input->jump && g->grounded) { g->vertical_velocity = 4.6f; g->grounded = false; }
    float forward = isfinite(input->forward) ? clampf(input->forward, -1, 1) : 0;
    float strafe = isfinite(input->strafe) ? clampf(input->strafe, -1, 1) : 0;
    float turn = isfinite(input->turn) ? clampf(input->turn, -1, 1) : 0;
    float look = isfinite(input->look) ? clampf(input->look, -1, 1) : 0;
    float magnitude = sqrtf(forward * forward + strafe * strafe);
    if (magnitude > 1) { forward /= magnitude; strafe /= magnitude; }
    int substeps = (int)ceilf(dt * 120.0f);
    float step_dt = dt / substeps;
    for (int i = 0; i < substeps && !g->caught; ++i) {
        g->elapsed += step_dt; g->sound_age += step_dt;
        if (g->sound_age > 0.85f)
            for (int enemy = 0; enemy < g->enemy_count; ++enemy)
                if (enemy_active(g, enemy)) g->enemies[enemy].heard_sound = false;
        /* Authored yaw grows from +Z toward +X, which is a left turn in the
         * right-handed world. Positive player turn/strafe mean screen right. */
        g->yaw = wrap_yaw(g->yaw - turn * 2.3f * step_dt);
        g->pitch = clampf(g->pitch + look * 1.7f * step_dt, -1.35f, 1.35f);
        float speed = g->crouched ? 1.0f : 2.1f, s = sinf(g->yaw), c = cosf(g->yaw);
        float height = g->crouched ? CROUCH_HEIGHT : STAND_HEIGHT;
        float moved = move_horizontal(g, &g->player,
            (s * forward - c * strafe) * speed * step_dt,
            (c * forward + s * strafe) * speed * step_dt, PLAYER_RADIUS, height, g->grounded);
        g->grounded = move_vertical(g, &g->player, &g->vertical_velocity, PLAYER_RADIUS, height, step_dt);
        if (moved > 0.00001f && g->grounded) {
            g->footstep_timer += step_dt;
            float interval = g->crouched ? 0.55f : 0.36f;
            if (g->footstep_timer >= interval) {
                g->footstep_timer -= interval;
                DlVec3 source = g->player; source.y += 0.10f;
                emit_sound(g, DL_EVENT_STEP, source, g->crouched ? 0.14f : 0.70f);
            }
        } else g->footstep_timer = 0;
        g->visibility = dl_visibility(g, dl_player_eye(g)) * (g->crouched ? 0.55f : 1);
        for (int enemy = 0; enemy < g->enemy_count; ++enemy)
            if (enemy_active(g, enemy)) update_guard(g, enemy, step_dt);
        if (g->player.y < -20) { dl_game_init(g, g->level); return; }
    }
}
const char *dl_guard_state_name(DlGuardState state) {
    switch (state) {
    case DL_PATROL: return "PATROL";
    case DL_INVESTIGATE: return "INVESTIGATE";
    case DL_SEARCH: return "SEARCH";
    case DL_CHASE: return "CHASE";
    default: return "UNKNOWN";
    }
}
