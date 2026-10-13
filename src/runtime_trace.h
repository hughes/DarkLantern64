#ifndef DARKLANTERN_RUNTIME_TRACE_H
#define DARKLANTERN_RUNTIME_TRACE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef DL_RUNTIME_TRACE
#define DL_RUNTIME_TRACE 0
#endif
#define DL_RUNTIME_TRACE_VERSION 1
#define DL_RUNTIME_TRACE_CAPACITY 256

/* Transport-independent, bounded v1 records. A live consumer can use the same
 * records as the ROM log exporter. No allocation, IO or callback from interrupts. */
typedef enum { DL_RT_EVENT, DL_RT_SNAPSHOT, DL_RT_SLOT, DL_RT_COST,
               DL_RT_MOTION, DL_RT_INTERACTION } DlRuntimeRecordType;
typedef struct {
    DlRuntimeRecordType type;
    uint64_t seq, time, job;
    const char *kind, *reason;
    uint64_t wide[4];
    uint32_t data[16];
    float position[9];
} DlRuntimeRecord;
typedef uint32_t (*DlRuntimeClock)(void *context);
typedef void (*DlRuntimeSink)(void *context, const DlRuntimeRecord *record);
typedef struct DlRuntimeTrace {
    DlRuntimeRecord records[DL_RUNTIME_TRACE_CAPACITY];
    size_t count, head;
    uint64_t sequence, emitted, dropped, now, trace_ticks;
    uint32_t last_tick, session;
    bool finished;
    DlRuntimeClock clock;
    void *context;
} DlRuntimeTrace;

void dl_runtime_trace_init(DlRuntimeTrace *trace, uint32_t session,
                          DlRuntimeClock clock, void *context);
/* Call at least once per 32-bit clock wrap, even when no events are emitted. */
uint64_t dl_runtime_trace_time(DlRuntimeTrace *trace);
uint64_t dl_runtime_region_job(uint32_t slot, uint32_t generation);
/* Sequence advances even when full; loss never looks like a complete trace. */
uint64_t dl_runtime_trace_push(DlRuntimeTrace *trace, DlRuntimeRecord record);
void dl_runtime_trace_event(DlRuntimeTrace *trace, const char *kind,
    const char *reason, uint64_t job, uint32_t region, uint32_t slot,
    uint32_t generation, uint32_t value, uint32_t total, uint32_t mask);
void dl_runtime_trace_flush(DlRuntimeTrace *trace, DlRuntimeSink sink, void *context);
void dl_runtime_trace_flush_limit(DlRuntimeTrace *trace, DlRuntimeSink sink,
                                 void *context, size_t limit);
#endif
