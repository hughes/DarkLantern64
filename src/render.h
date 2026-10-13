#ifndef DARKLANTERN_RENDER_H
#define DARKLANTERN_RENDER_H

#include "game.h"
#include "launch.h"
#ifdef DL_RENDER_T3D
#define DL_RENDER_BACKEND "rsp-tiny3d"
#else
#define DL_RENDER_BACKEND "cpu-rdpq"
#endif

typedef struct {
    bool debug;
    float frame_ms;
    float cpu_ms;
    int memory_bytes;
    int heap_used;
    int heap_total;
    uint32_t frames;
    bool streaming, stream_loading;
    uint32_t stream_current, stream_regions, stream_loads, stream_retired;
    uint32_t stream_reserved_bytes, stream_allocated_bytes;
} DlRenderStats;

void dl_render_init(void);
void dl_render_prepare_scene(const DlGame *game);
/* Call between frames before replacing a level. Frame/depth buffers persist;
 * level textures and lighting caches are released after queued graphics finish. */
void dl_render_release_scene(void);
/* Stream submissions use independent RSP syncpoints and the actual RDP detach
 * completion callback. Monotonic serials survive scene switches. */
void dl_render_stream_completed(uint64_t *rsp, uint64_t *rdp);
uint64_t dl_render_stream_submitted(void);
float dl_render_menu(const DlBundle *bundle, const DlLauncher *launcher, const DlRenderStats *stats);
int dl_render_triangle_count(void);
int dl_render_texture_upload_count(void);
/* Last submitted frame: frustum visibility does not stop gameplay simulation.
 * Transformed vertices counts world-to-camera work, once per visible vertex. */
int dl_render_model_count(void);
int dl_render_visible_model_count(void);
int dl_render_transformed_vertex_count(void);
/* Animation sub-costs belong to the transforms section. Flushes a window of
 * CPU pose/skinning costs; these are emulator CPU ticks, not RSP/RDP timings.
 * Window totals cover all visible models; maxima are per individual pose. */
void dl_render_report_animation(void);
#if defined(DL_CAPTURE) || defined(DL_MENU_TEST) || defined(DL_STREAM_TEST) || defined(DL_BAFFORD_STREAM_TEST) || defined(DL_BAFFORD_EXPANDED_TEST) || defined(DL_BAFFORD_DISTRICT_TEST)
void dl_render_request_capture(int id);
#endif
#ifdef DL_CAPTURE
void dl_render_set_animation_preview(const char *clip,float time,float yaw,float pitch);
#endif
/* Legacy aggregate elapsed time excluding waiting for a free VI buffer.
 * This includes audio interrupts and any internal graphics queue stalls;
 * dl_profile_latest() provides the disjoint, audio-adjusted breakdown. */
float dl_render_frame(const DlGame *game, const DlRenderStats *stats);

#endif
