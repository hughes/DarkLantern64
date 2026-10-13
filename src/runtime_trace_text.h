#ifndef DARKLANTERN_RUNTIME_TRACE_TEXT_H
#define DARKLANTERN_RUNTIME_TRACE_TEXT_H
#include "runtime_trace.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Exact v1 integer rows, with no allocation or platform IO. A zero result
 * denotes an unsupported row or insufficient capacity; no partial row should
 * be emitted. Floating-point rows retain the platform's existing formatter. */
size_t dl_runtime_trace_text(char *output, size_t capacity, uint32_t session,
    const DlRuntimeRecord *record);
#ifdef __cplusplus
}
#endif
#endif
