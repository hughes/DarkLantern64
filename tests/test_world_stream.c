#include "world_stream.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)
static uint8_t fixture[2048], buffers[2][512];
static size_t fixture_bytes, directory_bytes;
static DlMission mission;
static DlRegionSlot slots[2];
static DlWorldStream stream;
typedef struct {
    const uint8_t *data; size_t bytes, max_read, calls;
    unsigned stalls; size_t short_read;
    bool error, oversized;
} Reader;
static Reader reader;
static void put16(uint8_t *p, uint16_t n) { p[0] = (uint8_t)(n >> 8); p[1] = (uint8_t)n; }
static void put32(uint8_t *p, uint32_t n) {
    p[0] = (uint8_t)(n >> 24); p[1] = (uint8_t)(n >> 16); p[2] = (uint8_t)(n >> 8); p[3] = (uint8_t)n;
}
static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static void putfloat(uint8_t *p, float f) { uint32_t bits; memcpy(&bits, &f, sizeof bits); put32(p, bits); }
/* Independent bit-at-a-time fixture checksum. */
static uint32_t crc32(const uint8_t *p, size_t bytes) {
    uint32_t crc = UINT32_MAX;
    while (bytes--) { crc ^= *p++; for (int bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320u : 0); }
    return crc ^ UINT32_MAX;
}
static size_t read_memory(void *context, uint32_t offset, void *out, size_t wanted) {
    Reader *r = context; ++r->calls;
    if (wanted > r->max_read) r->max_read = wanted;
    if (r->error) return SIZE_MAX;
    if (r->oversized) return wanted + 1;
    if (r->stalls) { --r->stalls; return 0; }
    if (offset >= r->bytes) return SIZE_MAX;
    size_t n = r->bytes - offset; if (n > wanted) n = wanted;
    if (r->short_read && n > r->short_read) n = r->short_read;
    memcpy(out, r->data + offset, n); return n;
}
static size_t chunk(uint8_t *p, uint32_t entity) {
    enum { TRIANGLES = 8, TRIANGLE_OFFSET = 80, ENTITY_OFFSET = 432, BYTES = 436 };
    memcpy(p, "DLR1", 4); put16(p + 4, 1); put16(p + 6, 32); put32(p + 8, BYTES); put32(p + 12, 2);
    put16(p + 32, 1); put32(p + 36, TRIANGLE_OFFSET); put32(p + 40, TRIANGLES * 44);
    put32(p + 44, TRIANGLES); put32(p + 48, 44);
    put16(p + 56, 3); put32(p + 60, ENTITY_OFFSET); put32(p + 64, 4); put32(p + 68, 1); put32(p + 72, 4);
    for (unsigned i = 0; i < TRIANGLES; ++i) {
        uint8_t *record = p + TRIANGLE_OFFSET + i * 44;
        putfloat(record + 12, 1); putfloat(record + 28, 1);
        put32(record + 36, 1); put32(record + 40, i + 1);
    }
    put32(p + ENTITY_OFFSET, entity); return BYTES;
}
static void prepare(void) {
    memset(fixture, 0, sizeof fixture);
    directory_bytes = 232;
    size_t offsets[2] = {240, 688}, sizes[2];
    for (unsigned i = 0; i < 2; ++i) sizes[i] = chunk(fixture + offsets[i], i + 1);
    fixture_bytes = offsets[1] + sizes[1];
    memcpy(fixture, "DLM1", 4); put16(fixture + 4, 1); put16(fixture + 6, 64);
    put32(fixture + 8, (uint32_t)fixture_bytes); put32(fixture + 12, 2); put32(fixture + 16, 2);
    put32(fixture + 20, (uint32_t)directory_bytes); put32(fixture + 24, 1);
    for (unsigned i = 0; i < 2; ++i) {
        uint8_t *p = fixture + 64 + i * 64;
        put32(p, i + 1); put32(p + 4, (uint32_t)offsets[i]); put32(p + 8, (uint32_t)sizes[i]);
        put32(p + 12, (uint32_t)sizes[i]); put32(p + 16, crc32(fixture + offsets[i], sizes[i]));
        put32(p + 20, i); put32(p + 24, 1);
        for (int axis = 0; axis < 3; ++axis) { putfloat(p + 32 + axis * 4, -2); putfloat(p + 44 + axis * 4, 2); }
        uint8_t *e = fixture + 192 + i * 16;
        put32(e, i + 1); put32(e + 4, i + 1); put16(e + 8, (uint16_t)(i + 2));
    }
    put32(fixture + 224, 2); put32(fixture + 228, 1);
    put32(fixture + 28, crc32(fixture, directory_bytes));
    reader = (Reader){.data = fixture, .bytes = fixture_bytes};
}
static int initialize(void) {
    CHECK(dl_mission_open(fixture, directory_bytes, fixture_bytes, &mission) == DL_MISSION_OK);
    for (int i = 0; i < 2; ++i) slots[i] = (DlRegionSlot){.buffer = buffers[i], .capacity = sizeof buffers[i]};
    CHECK(dl_world_stream_init(&stream, &mission, slots, 2, read_memory, &reader) == DL_STREAM_OK);
    CHECK(stream.stats.allocated_slot_bytes == sizeof buffers);
    return 0;
}
static int finish(DlRegionHandle h) {
    DlStreamState state;
    for (int i = 0; i < 4096; ++i) {
        CHECK(dl_world_stream_state(&stream, h, &state) == DL_STREAM_OK);
        if (state == DL_STREAM_RESIDENT) return 0;
        CHECK(state != DL_STREAM_FAILED);
        size_t n = dl_world_stream_pump(&stream, 37); CHECK(n <= 37);
    }
    CHECK(false); return 1;
}
static int budgets_and_publication(void) {
    prepare(); CHECK(initialize() == 0);
    DlRegionHandle a, duplicate, b; const DlMissionChunk *view = NULL;
    CHECK(dl_world_stream_request(&stream, 1, &a) == DL_STREAM_OK);
    CHECK(dl_world_stream_request(&stream, 1, &duplicate) == DL_STREAM_OK);
    CHECK(a.slot == duplicate.slot && a.generation == duplicate.generation);
    CHECK(dl_world_stream_request(&stream, 2, &b) == DL_STREAM_OK);
    CHECK(dl_world_stream_request(&stream, 0, &duplicate) == DL_STREAM_ARGUMENT);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_NOT_READY && view == NULL);
    reader.stalls = 2; CHECK(dl_world_stream_pump(&stream, 10) == 0);
    CHECK(stream.stats.read_stalls == 2 && reader.calls == 2);
    CHECK(dl_world_stream_pump(&stream, 0) == 0 && reader.calls == 2);
    reader.short_read = 7;
    for (unsigned i = 0; i < 40; ++i) {
        CHECK(dl_world_stream_pump(&stream, 10) <= 10);
        CHECK(stream.stats.resident_bytes == 0);
    }
    reader.short_read = 0; CHECK(finish(a) == 0); CHECK(finish(b) == 0);
    CHECK(stream.stats.bytes_read == 872 && stream.stats.completed_loads == 2);
    CHECK(stream.stats.reserved_bytes == 872 && stream.stats.peak_reserved_bytes == 872);
    CHECK(stream.stats.resident_bytes == 872 && reader.max_read <= 256);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_OK);
    uint32_t entity; CHECK(dl_mission_entity_id(view, &mission, 1, 0, &entity) == DL_MISSION_OK && entity == 1);
    DlMissionTriangle triangle; CHECK(dl_mission_triangle(view, 0, &triangle) == DL_MISSION_OK && triangle.material_id == 1);
    CHECK(dl_world_stream_release(&stream, a) == DL_STREAM_OK);
    return 0;
}
static int retirement_and_handles(void) {
    prepare(); CHECK(initialize() == 0); stream.slot_count = 1;
    DlRegionHandle a, b; const DlMissionChunk *view;
    CHECK(dl_world_stream_request(&stream, 1, &a) == DL_STREAM_OK); CHECK(finish(a) == 0);
    CHECK(dl_world_stream_submit(&stream, a, 1, 1) == DL_STREAM_NOT_READY);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_OK);
    CHECK(dl_world_stream_submit(&stream, a, 3, 5) == DL_STREAM_OK);
    CHECK(dl_world_stream_retire(&stream, a) == DL_STREAM_OK);
    CHECK(stream.stats.resident_bytes == 0 && stream.stats.reserved_bytes == 436);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_NOT_READY);
    CHECK(dl_world_stream_request(&stream, 1, &b) == DL_STREAM_BUSY);
    CHECK(dl_world_stream_request(&stream, 2, &b) == DL_STREAM_CAPACITY);
    /* Existing CPU ownership may finish a submission after retirement began. */
    CHECK(dl_world_stream_submit(&stream, a, 4, 6) == DL_STREAM_OK);
    CHECK(dl_world_stream_submit(&stream, a, 3, 7) == DL_STREAM_ARGUMENT);
    CHECK(slots[0].last_rsp == 4 && slots[0].last_rdp == 6);
    CHECK(dl_world_stream_complete(&stream, 4, 5) == DL_STREAM_OK);
    CHECK(dl_world_stream_release(&stream, a) == DL_STREAM_OK);
    CHECK(slots[0].state == DL_STREAM_RETIRING); /* RSP done, RDP still owns it. */
    CHECK(dl_world_stream_complete(&stream, 3, 6) == DL_STREAM_ARGUMENT);
    CHECK(stream.completed_rsp == 4 && stream.completed_rdp == 5);
    CHECK(dl_world_stream_complete(&stream, 4, 6) == DL_STREAM_OK);
    CHECK(slots[0].state == DL_STREAM_FREE && stream.stats.reserved_bytes == 0);
    CHECK(dl_world_stream_request(&stream, 2, &b) == DL_STREAM_OK);
    CHECK(b.slot == a.slot && b.generation > a.generation);
    CHECK(dl_world_stream_release(&stream, a) == DL_STREAM_HANDLE);
    CHECK(dl_world_stream_retire(&stream, a) == DL_STREAM_HANDLE);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_HANDLE);
    CHECK(dl_world_stream_cancel(&stream, b) == DL_STREAM_OK);
    slots[0].generation = UINT32_MAX;
    CHECK(dl_world_stream_request(&stream, 1, &b) == DL_STREAM_CAPACITY);
    return 0;
}
static int pins_outlive_both_fences(void) {
    prepare(); CHECK(initialize() == 0);
    DlRegionHandle a; const DlMissionChunk *view;
    CHECK(dl_world_stream_request(&stream, 1, &a) == DL_STREAM_OK); CHECK(finish(a) == 0);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_OK);
    CHECK(dl_world_stream_borrow(&stream, a, &view) == DL_STREAM_OK);
    CHECK(dl_world_stream_submit(&stream, a, 9, 3) == DL_STREAM_OK);
    CHECK(dl_world_stream_cancel(&stream, a) == DL_STREAM_OK);
    CHECK(dl_world_stream_complete(&stream, 8, 3) == DL_STREAM_OK);
    CHECK(slots[a.slot].state == DL_STREAM_RETIRING); /* RDP done first. */
    CHECK(dl_world_stream_complete(&stream, 9, 3) == DL_STREAM_OK);
    CHECK(slots[a.slot].state == DL_STREAM_RETIRING);
    CHECK(dl_world_stream_submit(&stream, a, 9, 0) == DL_STREAM_ARGUMENT);
    CHECK(dl_world_stream_submit(&stream, a, 0, 3) == DL_STREAM_ARGUMENT);
    CHECK(dl_world_stream_release(&stream, a) == DL_STREAM_OK);
    CHECK(slots[a.slot].state == DL_STREAM_RETIRING);
    CHECK(dl_world_stream_release(&stream, a) == DL_STREAM_OK);
    CHECK(slots[a.slot].state == DL_STREAM_FREE);
    return 0;
}
static int failures_and_cancellation(void) {
    for (int mode = 0; mode < 5; ++mode) {
        prepare();
        if (mode == 4) {
            /* A matching checksum must not make a malformed section publish. */
            put16(fixture + 240 + 32, 4);
            put32(fixture + 64 + 16, crc32(fixture + 240, 436));
            put32(fixture + 28, 0); put32(fixture + 28, crc32(fixture, directory_bytes));
        }
        CHECK(initialize() == 0);
        DlRegionHandle h; CHECK(dl_world_stream_request(&stream, 1, &h) == DL_STREAM_OK);
        if (mode == 0) fixture[240 + 80] ^= 1;
        if (mode == 1) reader.error = true;
        if (mode == 2) reader.oversized = true;
        if (mode == 3) reader.bytes = 300;
        for (int step = 0; step < 40 && slots[h.slot].state != DL_STREAM_FAILED; ++step)
            CHECK(dl_world_stream_pump(&stream, 40) <= 40);
        CHECK(slots[h.slot].state == DL_STREAM_FAILED && stream.stats.failed_loads == 1);
        CHECK(stream.stats.resident_bytes == 0);
        const DlMissionChunk *view = NULL;
        CHECK(dl_world_stream_borrow(&stream, h, &view) == (mode == 0 || mode == 4 ? DL_STREAM_CORRUPT : DL_STREAM_IO));
        if (mode == 4) CHECK(slots[h.slot].validation_failure == DL_MISSION_LAYOUT);
        CHECK(view == NULL);
        CHECK(dl_world_stream_cancel(&stream, h) == DL_STREAM_OK);
        CHECK(stream.stats.reserved_bytes == 0);
    }
    prepare(); CHECK(initialize() == 0);
    DlRegionHandle h; CHECK(dl_world_stream_request(&stream, 1, &h) == DL_STREAM_OK);
    CHECK(dl_world_stream_pump(&stream, 11) == 11);
    CHECK(dl_world_stream_cancel(&stream, h) == DL_STREAM_OK);
    CHECK(dl_world_stream_pump(&stream, 50) == 0);
    CHECK(dl_world_stream_request(&stream, 1, &h) == DL_STREAM_OK);
    while (slots[h.slot].state != DL_STREAM_VALIDATING) CHECK(dl_world_stream_pump(&stream, 256) <= 256);
    CHECK(dl_world_stream_cancel(&stream, h) == DL_STREAM_OK);
    CHECK(stream.stats.resident_bytes == 0 && stream.stats.reserved_bytes == 0);
    return 0;
}
static int storage_bounds(void) {
    prepare(); CHECK(initialize() == 0);
    DlRegionSlot overlap_slots[2] = {{.buffer = buffers[0], .capacity = 100}, {.buffer = buffers[0] + 99, .capacity = 100}};
    CHECK(dl_world_stream_init(&stream, &mission, overlap_slots, 2, read_memory, &reader) == DL_STREAM_ARGUMENT);
    DlRegionSlot directory_slot = {.buffer = fixture, .capacity = 100};
    CHECK(dl_world_stream_init(&stream, &mission, &directory_slot, 1, read_memory, &reader) == DL_STREAM_ARGUMENT);
    DlRegionSlot self_slot = {.buffer = (uint8_t *)&stream, .capacity = sizeof stream};
    CHECK(dl_world_stream_init(&stream, &mission, &self_slot, 1, read_memory, &reader) == DL_STREAM_ARGUMENT);
    DlRegionSlot tiny = {.buffer = buffers[0], .capacity = 32};
    CHECK(dl_world_stream_init(&stream, &mission, &tiny, 1, read_memory, &reader) == DL_STREAM_OK);
    DlRegionHandle h; CHECK(dl_world_stream_request(&stream, 1, &h) == DL_STREAM_CAPACITY);
    CHECK(stream.stats.allocated_slot_bytes == 32 && !stream.stats.reserved_bytes);
    return 0;
}
/* Exercise independently cooked Python bytes, using only the resident
 * directory and bounded random reads from the package file. */
typedef struct { FILE *file; size_t max_read; } FileReader;
static size_t read_file(void *context, uint32_t offset, void *out, size_t wanted) {
    FileReader *r = context; if (wanted > r->max_read) r->max_read = wanted;
    if (offset > 0x7fffffffu || fseek(r->file, (long)offset, SEEK_SET)) return SIZE_MAX;
    size_t n = fread(out, 1, wanted, r->file); return n ? n : SIZE_MAX;
}
static int cooked_package(const char *path) {
    FILE *file = fopen(path, "rb"); CHECK(file != NULL);
    CHECK(!fseek(file, 0, SEEK_END)); long length = ftell(file); CHECK(length >= 64);
    CHECK(!fseek(file, 0, SEEK_SET)); uint8_t header[64]; CHECK(fread(header, 1, 64, file) == 64);
    size_t bytes = get32(header + 20); CHECK(bytes >= 64 && bytes <= (size_t)length);
    uint8_t *directory = malloc(bytes); CHECK(directory != NULL);
    memcpy(directory, header, 64); CHECK(fread(directory + 64, 1, bytes - 64, file) == bytes - 64);
    DlMission cooked; CHECK(dl_mission_open(directory, bytes, (size_t)length, &cooked) == DL_MISSION_OK);
    size_t capacity = 0;
    for (uint32_t id = 1; id <= cooked.region_count; ++id) {
        DlMissionRegion region; CHECK(dl_mission_region(&cooked, id, &region) == DL_MISSION_OK);
        if (capacity < region.decoded_bytes) capacity = region.decoded_bytes;
    }
    uint8_t *data = malloc(capacity); CHECK(data != NULL);
    DlRegionSlot slot = {.buffer = data, .capacity = capacity}; DlWorldStream loader;
    FileReader input = {file, 0}; CHECK(dl_world_stream_init(&loader, &cooked, &slot, 1, read_file, &input) == DL_STREAM_OK);
    unsigned triangles = 0, colliders = 0, entities = 0;
    /* Repeated forward/backward loading with only one slot proves directory
     * references remain usable independently of previously loaded chunks. */
    for (unsigned cycle = 0; cycle < 4; ++cycle) for (uint32_t n = 1; n <= cooked.region_count; ++n) {
        uint32_t id = cycle % 2 ? cooked.region_count + 1 - n : n;
        DlRegionHandle h; CHECK(dl_world_stream_request(&loader, id, &h) == DL_STREAM_OK);
        for (unsigned pump = 0; pump < 65536 && slot.state != DL_STREAM_RESIDENT; ++pump) {
            CHECK(dl_world_stream_pump(&loader, 13) <= 13); CHECK(slot.state != DL_STREAM_FAILED);
        }
        const DlMissionChunk *view; CHECK(dl_world_stream_borrow(&loader, h, &view) == DL_STREAM_OK);
        DlMissionSection section;
        if (dl_mission_section(view, DL_MISSION_SECTION_TRIANGLES, &section) == DL_MISSION_OK)
            for (uint32_t i = 0; i < section.count; ++i) { DlMissionTriangle t; CHECK(dl_mission_triangle(view, i, &t) == DL_MISSION_OK); ++triangles; }
        if (dl_mission_section(view, DL_MISSION_SECTION_COLLIDERS, &section) == DL_MISSION_OK)
            for (uint32_t i = 0; i < section.count; ++i) { DlMissionCollider c; CHECK(dl_mission_collider(view, &cooked, i, &c) == DL_MISSION_OK); ++colliders; }
        CHECK(dl_mission_section(view, DL_MISSION_SECTION_ENTITIES, &section) == DL_MISSION_OK);
        for (uint32_t i = 0; i < section.count; ++i) { uint32_t entity; CHECK(dl_mission_entity_id(view, &cooked, id, i, &entity) == DL_MISSION_OK); ++entities; }
        CHECK(dl_world_stream_submit(&loader, h, cycle * cooked.region_count + n, cycle * cooked.region_count + n) == DL_STREAM_OK);
        CHECK(dl_world_stream_retire(&loader, h) == DL_STREAM_OK);
        CHECK(dl_world_stream_release(&loader, h) == DL_STREAM_OK);
        CHECK(slot.state == DL_STREAM_RETIRING);
        CHECK(dl_world_stream_complete(&loader, cycle * cooked.region_count + n, cycle * cooked.region_count + n) == DL_STREAM_OK);
        CHECK(slot.state == DL_STREAM_FREE && !loader.stats.reserved_bytes);
    }
    CHECK(entities == cooked.entity_count * 4 && input.max_read <= 13);
    printf("cooked mission: %u regions, %u entities, %u triangles, %u colliders; four bidirectional cycles, directory=%lu bytes, allocated slot=%lu bytes, peak reservation=%lu bytes\n",
        cooked.region_count, entities / 4, triangles / 4, colliders / 4, (unsigned long)bytes,
        (unsigned long)capacity, (unsigned long)loader.stats.peak_reserved_bytes);
    free(data); free(directory); fclose(file); return 0;
}
int main(int argc, char **argv) {
    CHECK(budgets_and_publication() == 0); CHECK(retirement_and_handles() == 0);
    CHECK(pins_outlive_both_fences() == 0); CHECK(failures_and_cancellation() == 0); CHECK(storage_bounds() == 0);
    if (argc > 1) CHECK(cooked_package(argv[1]) == 0);
    puts("world streaming: bounded/stalled reads, unpublished failures, cancellation, CPU pins, independent RSP/RDP retirement, stale generations and disjoint storage passed");
    return 0;
}
