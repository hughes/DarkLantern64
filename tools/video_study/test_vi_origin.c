#include "vi_origin.h"

#include <assert.h>
#include <stdio.h>

typedef struct {
    uint32_t previous;
    unsigned scans, presents, repeats;
    bool initialized;
} PresentationCounter;

static void record(PresentationCounter *counter, uint32_t raw,
                   const uint32_t *bases, unsigned count, uint32_t stride) {
    uint32_t base = 0;
    assert(dl_video_base_origin(raw, bases, count, stride, &base));
    if (counter->initialized) {
        ++counter->scans;
        if (counter->previous != base) ++counter->presents;
        else ++counter->repeats;
    }
    counter->initialized = true;
    counter->previous = base;
}

int main(void) {
    const uint32_t bases[] = {0x100000, 0x200000, 0x300000};
    const uint32_t stride = 640 * 2;
    uint32_t output = 0;
    for (unsigned i = 0; i < 3; ++i) {
        assert(dl_video_base_origin(bases[i], bases, 3, stride, &output));
        assert(output == bases[i]);
        assert(dl_video_base_origin(bases[i]+stride, bases, 3, stride, &output));
        assert(output == bases[i]);
    }

    /* Sixty alternating field addresses of one held buffer are sixty repeats,
     * never sixty newly presented frames. Seed just before the timed interval. */
    PresentationCounter held = {0};
    record(&held, bases[0]+stride, bases, 3, stride);
    for (unsigned field = 0; field < 60; ++field)
        record(&held, bases[0]+(field%2)*stride, bases, 3, stride);
    assert(held.scans == 60 && held.presents == 0 && held.repeats == 60);

    /* Thirty distinct buffers held for two alternating fields each produce
     * thirty base changes across sixty scans, even though every raw address
     * differs from the preceding raw address. */
    PresentationCounter interlaced = {0};
    record(&interlaced, bases[2]+stride, bases, 3, stride);
    for (unsigned frame = 0; frame < 30; ++frame) {
        record(&interlaced, bases[frame%3], bases, 3, stride);
        record(&interlaced, bases[frame%3]+stride, bases, 3, stride);
    }
    assert(interlaced.scans == 60 && interlaced.presents == 30 && interlaced.repeats == 30);

    PresentationCounter progressive = {0};
    record(&progressive, bases[2], bases, 3, 0);
    for (unsigned frame = 0; frame < 60; ++frame)
        record(&progressive, bases[frame%3], bases, 3, 0);
    assert(progressive.scans == 60 && progressive.presents == 60 && progressive.repeats == 0);

    /* Invalid data must not turn an unknown origin into a cached prior frame.
     * Adjacent bases can make a raw origin ambiguous: exact matches do not
     * outrank another buffer's field-offset match. Duplicate same-base records
     * are harmless. Reject offset arithmetic that would wrap. */
    const uint32_t ambiguous[] = {bases[0], bases[0]+stride};
    const uint32_t duplicate[] = {bases[0], bases[0]};
    const uint32_t high[] = {UINT32_MAX-16};
    output = 0xabcdef;
    assert(!dl_video_base_origin(bases[0]+stride, ambiguous, 2, stride, &output));
    assert(output == 0xabcdef);
    assert(!dl_video_base_origin(bases[0]+stride+1, bases, 3, stride, &output));
    assert(!dl_video_base_origin(bases[0]+stride, bases, 3, 0, &output));
    assert(!dl_video_base_origin(0x80000000|bases[0], bases, 3, stride, &output));
    assert(!dl_video_base_origin(15, high, 1, 32, &output));
    assert(!dl_video_base_origin(bases[0], NULL, 3, stride, &output));
    assert(!dl_video_base_origin(bases[0], bases, 0, stride, &output));
    assert(!dl_video_base_origin(bases[0], bases, 4, stride, &output));
    assert(!dl_video_base_origin(bases[0], bases, 3, stride, NULL));
    assert(output == 0xabcdef);
    assert(dl_video_base_origin(bases[0]+stride, duplicate, 2, stride, &output));
    assert(output == bases[0]);
    assert(dl_video_base_origin(high[0], high, 1, 32, &output));
    assert(output == high[0]);
    puts("VI origin normalization: held fields, 30 interlaced / 60 progressive frames, ambiguity and invalid input passed.");
    return 0;
}
