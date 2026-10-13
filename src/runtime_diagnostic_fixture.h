#ifndef DARKLANTERN_RUNTIME_DIAGNOSTIC_FIXTURE_H
#define DARKLANTERN_RUNTIME_DIAGNOSTIC_FIXTURE_H

#include "runtime_diagnostics.h"

/* Portable, synthetic two-room mission. No converted game content, renderer or
 * N64 adapter is required. Allocate this large object statically or on the heap.
 * Region 1 owns a door; region 2 owns nearby loot. Both directions use that door.
 * A published single-room view inserts a linked synthetic loading barrier. */
enum {
    DL_RUNTIME_DOORWAY_DIRECTORY_BYTES=232,
    DL_RUNTIME_DOORWAY_CHUNK_BYTES=148,
    DL_RUNTIME_DOORWAY_FILE_BYTES=548
};
typedef struct {
    uint8_t package[DL_RUNTIME_DOORWAY_FILE_BYTES];
    uint8_t buffers[2][DL_RUNTIME_DOORWAY_CHUNK_BYTES];
    DlMission mission;
    DlRegionSlot slots[2];
    DlRegionHandle handles[2];
    DlWorldStream world;
    DlStreamScene scene;
    DlGame game;
    size_t read_limit; /* zero: no extra restriction; set for partial reads */
    bool read_stalled, read_failed;
} DlRuntimeDoorwayFixture;

/* Init creates an empty published view and initializes gameplay at x=-.30,
 * looking toward the doorway (+X). Loads and publication stay separate. */
bool dl_runtime_doorway_init(DlRuntimeDoorwayFixture *fixture);
DlStreamStatus dl_runtime_doorway_request(DlRuntimeDoorwayFixture *fixture,uint32_t region);
DlStreamStatus dl_runtime_doorway_load(DlRuntimeDoorwayFixture *fixture,uint32_t region);
DlStreamSceneStatus dl_runtime_doorway_publish(DlRuntimeDoorwayFixture *fixture);
uint32_t dl_runtime_doorway_physical_mask(const DlRuntimeDoorwayFixture *fixture);
uint32_t dl_runtime_doorway_published_mask(const DlRuntimeDoorwayFixture *fixture);

#endif
