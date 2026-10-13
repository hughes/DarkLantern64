#include "world_stream.h"
#include <string.h>

static bool valid_stream(const DlWorldStream *s) {
    return s && s->mission && s->mission->directory && s->slots && s->slot_count && s->read;
}
static const DlRegionSlot *find_slot(const DlWorldStream *s, DlRegionHandle h) {
    if (!valid_stream(s) || h.slot >= s->slot_count || !h.generation) return NULL;
    const DlRegionSlot *slot = &s->slots[h.slot];
    return slot->state != DL_STREAM_FREE && slot->generation == h.generation ? slot : NULL;
}
static DlRegionSlot *mutable_slot(DlWorldStream *s, DlRegionHandle h) {
    return (DlRegionSlot *)find_slot(s, h);
}
static void free_slot(DlWorldStream *s, DlRegionSlot *slot) {
    s->stats.reserved_bytes -= slot->region.decoded_bytes;
    uint8_t *buffer = slot->buffer;
    size_t capacity = slot->capacity;
    uint32_t generation = slot->generation;
    memset(slot, 0, sizeof *slot);
    slot->buffer = buffer; slot->capacity = capacity; slot->generation = generation;
}
static void drain_retiring(DlWorldStream *s) {
    for (size_t i = 0; i < s->slot_count; ++i) {
        DlRegionSlot *slot = &s->slots[i];
        if (slot->state == DL_STREAM_RETIRING && !slot->cpu_pins &&
            slot->last_rsp <= s->completed_rsp && slot->last_rdp <= s->completed_rdp)
            free_slot(s, slot);
    }
}
static bool overlap(uintptr_t a, size_t na, uintptr_t b, size_t nb) {
    return a < b + nb && b < a + na;
}
DlStreamStatus dl_world_stream_init(DlWorldStream *s, const DlMission *mission,
    DlRegionSlot *slots, size_t count, DlStreamRead read, void *context) {
    if (!s || !mission || !mission->directory || !slots || !count ||
        count > UINT32_MAX || count > SIZE_MAX / sizeof *slots ||
        !read || !mission->region_count) return DL_STREAM_ARGUMENT;
    uintptr_t directory = (uintptr_t)mission->directory;
    uintptr_t manager = (uintptr_t)s, metadata = (uintptr_t)slots, definition = (uintptr_t)mission;
    size_t metadata_bytes = count * sizeof *slots;
    if (mission->directory_bytes > UINTPTR_MAX - directory ||
        sizeof *s > UINTPTR_MAX - manager || metadata_bytes > UINTPTR_MAX - metadata ||
        sizeof *mission > UINTPTR_MAX - definition ||
        overlap(manager, sizeof *s, metadata, metadata_bytes) ||
        overlap(manager, sizeof *s, directory, mission->directory_bytes) ||
        overlap(manager, sizeof *s, definition, sizeof *mission) ||
        overlap(metadata, metadata_bytes, directory, mission->directory_bytes) ||
        overlap(metadata, metadata_bytes, definition, sizeof *mission)) return DL_STREAM_ARGUMENT;
    size_t allocated = 0;
    /* Validate everything before resetting any caller storage. */
    for (size_t i = 0; i < count; ++i) {
        uintptr_t address = (uintptr_t)slots[i].buffer;
        if (!address || slots[i].capacity < DL_MISSION_CHUNK_HEADER_BYTES ||
            slots[i].capacity > UINTPTR_MAX - address ||
            slots[i].capacity > SIZE_MAX - allocated ||
            overlap(address, slots[i].capacity, directory, mission->directory_bytes) ||
            overlap(address, slots[i].capacity, manager, sizeof *s) ||
            overlap(address, slots[i].capacity, metadata, metadata_bytes) ||
            overlap(address, slots[i].capacity, definition, sizeof *mission))
            return DL_STREAM_ARGUMENT;
        for (size_t j = 0; j < i; ++j)
            if (overlap(address, slots[i].capacity, (uintptr_t)slots[j].buffer, slots[j].capacity))
                return DL_STREAM_ARGUMENT;
        allocated += slots[i].capacity;
    }
    *s = (DlWorldStream){.mission = mission, .slots = slots, .slot_count = count,
        .read = read, .read_context = context, .stats = {.allocated_slot_bytes = allocated}};
    for (size_t i = 0; i < count; ++i) {
        uint8_t *buffer = slots[i].buffer; size_t capacity = slots[i].capacity;
        slots[i] = (DlRegionSlot){.buffer = buffer, .capacity = capacity};
    }
    return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_request(DlWorldStream *s, uint32_t id, DlRegionHandle *handle) {
    if (!valid_stream(s) || !handle) return DL_STREAM_ARGUMENT;
    DlMissionRegion region;
    if (dl_mission_region(s->mission, id, &region) != DL_MISSION_OK) return DL_STREAM_ARGUMENT;
    size_t best = s->slot_count;
    for (size_t i = 0; i < s->slot_count; ++i) {
        DlRegionSlot *slot = &s->slots[i];
        if (slot->state != DL_STREAM_FREE && slot->region_id == id) {
            if (slot->state == DL_STREAM_RETIRING) return DL_STREAM_BUSY;
            if (slot->state == DL_STREAM_FAILED) return slot->failure;
            *handle = (DlRegionHandle){(uint32_t)i, slot->generation}; return DL_STREAM_OK;
        }
        if (slot->state == DL_STREAM_FREE && slot->generation < UINT32_MAX &&
            slot->capacity >= region.decoded_bytes &&
            (best == s->slot_count || slot->capacity < s->slots[best].capacity)) best = i;
    }
    if (best == s->slot_count) return DL_STREAM_CAPACITY;
    DlRegionSlot *slot = &s->slots[best];
    slot->state = DL_STREAM_REQUESTED; slot->region_id = id; slot->region = region;
    ++slot->generation; slot->crc32 = UINT32_MAX;
    s->stats.reserved_bytes += region.decoded_bytes;
    if (s->stats.reserved_bytes > s->stats.peak_reserved_bytes)
        s->stats.peak_reserved_bytes = s->stats.reserved_bytes;
    *handle = (DlRegionHandle){(uint32_t)best, slot->generation}; return DL_STREAM_OK;
}
static void fail_slot(DlWorldStream *s, DlRegionSlot *slot, DlStreamStatus failure,
    DlMissionStatus validation) {
    slot->state = DL_STREAM_FAILED; slot->failure = failure;
    slot->validation_failure = validation; ++s->stats.failed_loads;
}
size_t dl_world_stream_pump(DlWorldStream *s, size_t budget) {
    if (!valid_stream(s) || !budget) return 0;
    size_t processed = 0, start = s->cursor;
    for (size_t visited = 0; visited < s->slot_count; ++visited) {
        size_t i = (start + visited) % s->slot_count;
        DlRegionSlot *slot = &s->slots[i];
        if (slot->state == DL_STREAM_REQUESTED) slot->state = DL_STREAM_READING;
        if (slot->state == DL_STREAM_READING && processed < budget) {
            size_t wanted = slot->region.chunk_bytes - slot->bytes_read;
            if (wanted > 256) wanted = 256;
            if (wanted > budget - processed) wanted = budget - processed;
            size_t n = s->read(s->read_context, slot->region.chunk_offset + (uint32_t)slot->bytes_read,
                slot->buffer + slot->bytes_read, wanted);
            if (n == SIZE_MAX || n > wanted) {
                fail_slot(s, slot, DL_STREAM_IO, DL_MISSION_OK); continue;
            }
            if (!n) { ++s->stats.read_stalls; continue; }
            slot->crc32 = dl_mission_crc32_update(slot->crc32, slot->buffer + slot->bytes_read, n);
            slot->bytes_read += n; processed += n; s->stats.bytes_read += n;
            if (slot->bytes_read == slot->region.chunk_bytes) slot->state = DL_STREAM_VALIDATING;
        }
        /* Validation is a separate pump boundary, so callers can cancel a fully
         * read chunk before publication. No whole-payload CRC scan occurs here. */
        else if (slot->state == DL_STREAM_VALIDATING) {
            if ((slot->crc32 ^ UINT32_MAX) != slot->region.chunk_crc32) {
                fail_slot(s, slot, DL_STREAM_CORRUPT, DL_MISSION_CHECKSUM); continue;
            }
            DlMissionStatus status = dl_mission_chunk_open(slot->buffer, slot->bytes_read, &slot->chunk);
            if (status != DL_MISSION_OK) {
                fail_slot(s, slot, DL_STREAM_CORRUPT, status); continue;
            }
            slot->state = DL_STREAM_RESIDENT;
            s->stats.resident_bytes += slot->region.decoded_bytes; ++s->stats.completed_loads;
        }
    }
    s->cursor = (start + 1) % s->slot_count;
    return processed;
}
DlStreamStatus dl_world_stream_state(const DlWorldStream *s, DlRegionHandle handle, DlStreamState *state) {
    if (!state) return DL_STREAM_ARGUMENT;
    const DlRegionSlot *slot = find_slot(s, handle);
    if (!slot) return DL_STREAM_HANDLE;
    *state = slot->state; return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_borrow(DlWorldStream *s, DlRegionHandle handle, const DlMissionChunk **chunk) {
    if (!chunk) return DL_STREAM_ARGUMENT;
    DlRegionSlot *slot = mutable_slot(s, handle);
    if (!slot) return DL_STREAM_HANDLE;
    if (slot->state == DL_STREAM_FAILED) return slot->failure;
    if (slot->state != DL_STREAM_RESIDENT) return DL_STREAM_NOT_READY;
    if (slot->cpu_pins == UINT32_MAX) return DL_STREAM_CAPACITY;
    ++slot->cpu_pins; *chunk = &slot->chunk; return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_release(DlWorldStream *s, DlRegionHandle handle) {
    DlRegionSlot *slot = mutable_slot(s, handle);
    if (!slot) return DL_STREAM_HANDLE;
    if (!slot->cpu_pins) return DL_STREAM_ARGUMENT;
    --slot->cpu_pins; drain_retiring(s); return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_submit(DlWorldStream *s, DlRegionHandle handle, uint64_t rsp, uint64_t rdp) {
    DlRegionSlot *slot = mutable_slot(s, handle);
    if (!slot) return DL_STREAM_HANDLE;
    if (!slot->cpu_pins || (slot->state != DL_STREAM_RESIDENT && slot->state != DL_STREAM_RETIRING))
        return DL_STREAM_NOT_READY;
    if ((rsp && (rsp < slot->last_rsp || rsp <= s->completed_rsp)) ||
        (rdp && (rdp < slot->last_rdp || rdp <= s->completed_rdp))) return DL_STREAM_ARGUMENT;
    if (rsp) slot->last_rsp = rsp;
    if (rdp) slot->last_rdp = rdp;
    return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_retire(DlWorldStream *s, DlRegionHandle handle) {
    DlRegionSlot *slot = mutable_slot(s, handle);
    if (!slot) return DL_STREAM_HANDLE;
    if (slot->state != DL_STREAM_RESIDENT && slot->state != DL_STREAM_RETIRING) return DL_STREAM_NOT_READY;
    if (slot->state == DL_STREAM_RESIDENT) {
        s->stats.resident_bytes -= slot->region.decoded_bytes; slot->state = DL_STREAM_RETIRING;
    }
    drain_retiring(s); return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_cancel(DlWorldStream *s, DlRegionHandle handle) {
    DlRegionSlot *slot = mutable_slot(s, handle);
    if (!slot) return DL_STREAM_HANDLE;
    if (slot->state == DL_STREAM_RESIDENT || slot->state == DL_STREAM_RETIRING)
        return dl_world_stream_retire(s, handle);
    free_slot(s, slot); return DL_STREAM_OK;
}
DlStreamStatus dl_world_stream_complete(DlWorldStream *s, uint64_t rsp, uint64_t rdp) {
    if (!valid_stream(s) || rsp < s->completed_rsp || rdp < s->completed_rdp) return DL_STREAM_ARGUMENT;
    s->completed_rsp = rsp; s->completed_rdp = rdp; drain_retiring(s); return DL_STREAM_OK;
}
const char *dl_world_stream_status_name(DlStreamStatus status) {
    static const char *const names[] = {"ok", "argument", "handle", "not-ready", "busy", "capacity", "io", "corrupt"};
    return (unsigned)status < sizeof names / sizeof names[0] ? names[status] : "unknown";
}
