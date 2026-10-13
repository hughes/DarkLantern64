#ifndef DARKLANTERN_WORLD_STREAM_H
#define DARKLANTERN_WORLD_STREAM_H

#include "mission.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    DL_STREAM_FREE, DL_STREAM_REQUESTED, DL_STREAM_READING,
    DL_STREAM_VALIDATING, DL_STREAM_RESIDENT, DL_STREAM_RETIRING,
    DL_STREAM_FAILED
} DlStreamState;
typedef enum {
    DL_STREAM_OK, DL_STREAM_ARGUMENT, DL_STREAM_HANDLE, DL_STREAM_NOT_READY,
    DL_STREAM_BUSY, DL_STREAM_CAPACITY, DL_STREAM_IO, DL_STREAM_CORRUPT
} DlStreamStatus;
typedef struct { uint32_t slot, generation; } DlRegionHandle;

/* Absolute package-file offset. Return 0 for a temporary stall, SIZE_MAX for
 * an IO error, or <= wanted bytes written. Never retain the destination. The
 * platform adapter is responsible for DMA/cache alignment and audio fairness.
 * This callback must be bounded/nonblocking; ordinary whole-file asset_load
 * is not an appropriate adapter. */
typedef size_t (*DlStreamRead)(void *context, uint32_t offset,
    void *destination, size_t wanted);

typedef struct {
    /* Caller-provided, nonoverlapping buffers; remain allocated for the whole
     * manager lifetime, including when this slot is FREE. */
    uint8_t *buffer;
    size_t capacity;
    DlStreamState state;
    uint32_t generation, region_id, cpu_pins, crc32;
    size_t bytes_read;
    uint64_t last_rsp, last_rdp;
    DlMissionRegion region;
    DlMissionChunk chunk;
    DlStreamStatus failure;
    DlMissionStatus validation_failure;
} DlRegionSlot;

typedef struct {
    size_t allocated_slot_bytes, reserved_bytes, resident_bytes;
    size_t peak_reserved_bytes;
    uint64_t bytes_read, read_stalls, failed_loads, completed_loads;
} DlStreamStats;

typedef struct {
    const DlMission *mission;
    DlRegionSlot *slots;
    size_t slot_count, cursor;
    size_t read_slice; /* optional platform limit; zero retains the portable 256-byte default */
    DlStreamRead read;
    void *read_context;
    uint64_t completed_rsp, completed_rdp;
    DlStreamStats stats;
} DlWorldStream;

/* No allocation. Only buffer/capacity need initial values in each slot.
 * The opened mission directory and slot buffers must stay alive and immutable
 * except for manager writes. Reinitialization while resources are owned is
 * forbidden by the caller contract. */
DlStreamStatus dl_world_stream_init(DlWorldStream *stream, const DlMission *mission,
    DlRegionSlot *slots, size_t slot_count, DlStreamRead read, void *context);
DlStreamStatus dl_world_stream_request(DlWorldStream *stream, uint32_t region_id,
    DlRegionHandle *handle);
/* Call at a tick/frame ownership boundary. Reads and CRC work together process
 * at most byte_budget bytes, using read_slice (default 256) bytes per slot. One
 * pump visits every slot at most once. Publication performs only bounded
 * structural validation (<=3 sections); lazy record getters validate values.
 * Return bytes actually read, not a wall-clock deadline guarantee. */
size_t dl_world_stream_pump(DlWorldStream *stream, size_t byte_budget);
DlStreamStatus dl_world_stream_state(const DlWorldStream *stream,
    DlRegionHandle handle, DlStreamState *state);
/* Borrowed view remains valid until the matching release. Retirement stops
 * new borrows but existing borrowers may finish/submit before releasing. */
DlStreamStatus dl_world_stream_borrow(DlWorldStream *stream, DlRegionHandle handle,
    const DlMissionChunk **chunk);
DlStreamStatus dl_world_stream_release(DlWorldStream *stream, DlRegionHandle handle);
/* Record graphics ownership while holding a CPU borrow. Serial 0 means that
 * fence domain is unused by this submission. Nonzero serials must be later than
 * reported completion. Each domain is monotonic u64 and must not wrap. Serial
 * values belong to the platform, not to region slots. */
DlStreamStatus dl_world_stream_submit(DlWorldStream *stream, DlRegionHandle handle,
    uint64_t rsp_serial, uint64_t rdp_serial);
DlStreamStatus dl_world_stream_retire(DlWorldStream *stream, DlRegionHandle handle);
/* Cancel an unpublished/failed load immediately; a published chunk retires.
 * V1 reads retain no DMA ownership after callback return. An asynchronous DMA
 * adapter must finish its transfer before acknowledging cancellation. */
DlStreamStatus dl_world_stream_cancel(DlWorldStream *stream, DlRegionHandle handle);
DlStreamStatus dl_world_stream_complete(DlWorldStream *stream,
    uint64_t rsp_serial, uint64_t rdp_serial);
const char *dl_world_stream_status_name(DlStreamStatus status);

#ifdef __cplusplus
}
#endif
#endif
