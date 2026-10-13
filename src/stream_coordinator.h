#ifndef DARKLANTERN_STREAM_COORDINATOR_H
#define DARKLANTERN_STREAM_COORDINATOR_H

#include "stream_scene.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct DlStreamCoordinator DlStreamCoordinator;
typedef struct {
    const char *kind, *reason;
    uint64_t job;
    uint32_t mask, region;
} DlStreamCoordinatorEvent;

/* All platform operations run on the caller's foreground ownership boundary.
 * Reads must obey DlStreamRead's destination-lifetime contract. Abandon never
 * frees a staging buffer still owned by an asynchronous device. Renderer
 * callbacks only manage platform resources; the coordinator publishes levels.
 * Renderer steps may report readiness only for the supplied job token. An
 * asynchronous result from a canceled token must be discarded by the adapter. */
typedef struct {
    DlStreamRead read;
    void (*abandon)(void *context, const DlRegionSlot *slot);
    uint32_t (*clock)(void *context);
    bool (*io_busy)(void *context);
    void (*renderer_begin)(void *context, const DlGame *candidate, uint64_t job);
    bool (*renderer_step)(void *context, const DlGame *candidate, uint32_t tick_budget, uint64_t job);
    void (*renderer_cancel)(void *context, uint64_t job);
    void (*renderer_commit)(void *context, const DlGame *game, uint64_t job);
    void (*graphics_completed)(void *context, uint64_t *rsp, uint64_t *rdp);
    void (*observe)(void *context, const DlStreamCoordinator *coordinator,
        const DlGame *game, const DlStreamCoordinatorEvent *event);
} DlStreamCoordinatorCallbacks;

typedef struct {
    size_t read_slice, pump_byte_budget, assembly_records;
    uint32_t prepare_ticks, service_ticks, read_ticks;
    unsigned max_pump_attempts;
} DlStreamCoordinatorConfig;

typedef enum {
    DL_COORDINATOR_OK, DL_COORDINATOR_ARGUMENT, DL_COORDINATOR_GAME,
    DL_COORDINATOR_DEMAND, DL_COORDINATOR_WORLD, DL_COORDINATOR_SCENE,
    DL_COORDINATOR_QUERY
} DlStreamCoordinatorStatus;

typedef struct {
    uint32_t current_region, wanted_mask, physical_mask, published_mask;
    uint32_t candidate_mask, pending_transition, revision, retired, service_stage;
    uint64_t candidate_job, completed_rsp, completed_rdp;
    bool preparing, cpu_ready, gpu_ready, initial_ready, queued_use, settled;
    int queued_object;
    uint32_t queued_transition;
} DlStreamCoordinatorSnapshot;

/* Storage and all backing mission/slot/bootstrap assets are caller-owned.
 * Reinitialize only after cancel and graphics shutdown. Scene banks never
 * retain region-buffer pointers after assembly releases its generation pins. */
struct DlStreamCoordinator {
    DlWorldStream world;
    DlStreamScene scene;
    DlStreamCoordinatorCallbacks callbacks;
    DlStreamCoordinatorConfig config;
    void *context;
    DlStreamPolicyState policy_state;
    DlGame prepared_game;
    uint32_t view_regions, wanted_regions, prepared_regions, retired, service_stage;
    uint32_t queued_transition;
    int queued_object;
    uint16_t guard_enemy_mask, published_guard_mask;
    bool queued_use, preparing, assembling, gpu_started, gpu_ready;
    bool started, initial_cpu_ready, initial_ready;
    uint64_t candidate_job, candidate_sequence, prepared_generation;
    DlStreamCoordinatorStatus failure;
    DlStreamStatus world_failure;
    DlStreamSceneStatus scene_failure;
};

DlStreamCoordinatorConfig dl_stream_coordinator_default_config(void);
DlStreamCoordinatorStatus dl_stream_coordinator_init(DlStreamCoordinator *coordinator,
    const DlLevel *bootstrap, const DlMission *mission, DlRegionSlot *slots,
    size_t slot_count, const DlStreamCoordinatorCallbacks *callbacks,
    const DlStreamCoordinatorConfig *config, void *context);
DlStreamCoordinatorStatus dl_stream_coordinator_start(DlStreamCoordinator *coordinator,
    DlGame *game, int preset);
DlStreamCoordinatorStatus dl_stream_coordinator_initial_step(DlStreamCoordinator *coordinator,
    const DlGame *game, size_t byte_budget, bool *ready);
DlStreamCoordinatorStatus dl_stream_coordinator_initial_publish(DlStreamCoordinator *coordinator,
    DlGame *game);
void dl_stream_coordinator_initial_renderer_ready(DlStreamCoordinator *coordinator,
    const DlGame *game);
DlStreamCoordinatorStatus dl_stream_coordinator_service(DlStreamCoordinator *coordinator,
    DlGame *game);
void dl_stream_coordinator_filter_input(DlStreamCoordinator *coordinator,
    const DlGame *game, DlInput *input);
/* Cancellation discards only unpublished work and its CPU pins. The published
 * scene and graphics ownership remain intact until their completion serials. */
void dl_stream_coordinator_cancel(DlStreamCoordinator *coordinator);
DlStreamCoordinatorStatus dl_stream_coordinator_complete(DlStreamCoordinator *coordinator,
    uint64_t rsp, uint64_t rdp);
DlStreamCoordinatorStatus dl_stream_coordinator_submitted(DlStreamCoordinator *coordinator,
    uint64_t rsp, uint64_t rdp);
uint32_t dl_stream_coordinator_current(const DlStreamCoordinator *coordinator, const DlGame *game);
uint32_t dl_stream_coordinator_resident_mask(const DlStreamCoordinator *coordinator);
bool dl_stream_coordinator_settled(const DlStreamCoordinator *coordinator);
void dl_stream_coordinator_snapshot(const DlStreamCoordinator *coordinator,
    const DlGame *game, DlStreamCoordinatorSnapshot *snapshot);
const char *dl_stream_coordinator_status_name(DlStreamCoordinatorStatus status);

#ifdef __cplusplus
}
#endif
#endif
