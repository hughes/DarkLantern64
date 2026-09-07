/* Isolated SDK compatibility proof. Two vertex loads under different matrices
 * feed the same triangles, retaining vertices 0/1 while replacing 2/3.
 * No texture cooker, game renderer, SDK install, or global settings involved. */
#include <libdragon.h>
#include <t3d/t3d.h>

int main(void) {
    debug_init_isviewer();
    debug_init_usblog();
    display_init(RESOLUTION_320x240, DEPTH_16_BPP, 3, GAMMA_NONE, FILTERS_RESAMPLE);
    rdpq_init();
    t3d_init((T3DInitParams){0});
    surface_t depth = surface_alloc(FMT_RGBA16, 320, 240);
    T3DMat4FP *matrices = malloc_uncached(2 * sizeof(T3DMat4FP));
    T3DVertPacked *vertices = malloc_uncached(2 * sizeof(T3DVertPacked));
    assert(matrices && vertices);
    const uint16_t normal = t3d_vert_pack_normal(&(T3DVec3){{0, 0, 1}});
    vertices[0] = (T3DVertPacked){
        .posA = {-6, -6, 0}, .normA = normal, .rgbaA = 0xff4040ff,
        .posB = {-6, 6, 0}, .normB = normal, .rgbaB = 0x40ff40ff,
    };
    vertices[1] = (T3DVertPacked){
        .posA = {6, -6, 0}, .normA = normal, .rgbaA = 0x4040ffff,
        .posB = {6, 6, 0}, .normB = normal, .rgbaB = 0xffff40ff,
    };
    T3DViewport viewport = t3d_viewport_create();
    t3d_viewport_set_projection(&viewport, T3D_DEG_TO_RAD(60.0f), 1.0f, 100.0f);
    t3d_viewport_look_at(&viewport, &(T3DVec3){{0, 0, -25}},
                       &(T3DVec3){{0, 0, 0}}, &(T3DVec3){{0, 1, 0}});
    const uint8_t ambient[] = {255, 255, 255, 255};
    debugf("DL64 tiny3d_proof boot revision=ec557373e986b5e041cc102a7ff787eb07921937\n");
    for (unsigned frame = 0;; ++frame) {
        /* This blocking proof deliberately favors reproducible matrix lifetime
         * over throughput. The production renderer must buffer submitted data. */
        rspq_wait();
        T3DMat4 base, moved;
        t3d_mat4_identity(&base);
        t3d_mat4_identity(&moved);
        if (frame < 60)
            t3d_mat4_rotate(&moved, &(T3DVec3){{0, 0, 1}}, (float)frame * 0.01f);
        t3d_mat4_to_fixed(&matrices[0], &base);
        t3d_mat4_to_fixed(&matrices[1], &moved);
        surface_t *screen = display_get();
        rdpq_attach(screen, &depth);
        t3d_frame_start();
        t3d_viewport_attach(&viewport);
        rdpq_mode_combiner(RDPQ_COMBINER_SHADE);
        t3d_screen_clear_color(RGBA32(8, 8, 16, 255));
        t3d_screen_clear_depth();
        t3d_light_set_ambient(ambient);
        t3d_light_set_count(0);
        unsigned cull = frame >= 90 ? T3D_FLAG_CULL_FRONT : frame >= 60 ? T3D_FLAG_CULL_BACK : 0;
        t3d_state_set_drawflags(T3D_FLAG_SHADED | T3D_FLAG_DEPTH | cull);
        t3d_matrix_push_pos(1);
        t3d_matrix_set(&matrices[0], true);
        t3d_vert_load(&vertices[0], 0, 2);
        t3d_matrix_set(&matrices[1], true);
        t3d_vert_load(&vertices[1], 2, 2);
        t3d_tri_draw(0, 1, 2);
        t3d_tri_draw(2, 1, 3);
        t3d_tri_sync();
        t3d_matrix_pop(1);
        rdpq_detach_show();
        if (frame == 0 || frame == 30 || frame == 60 || frame == 90) {
            rspq_wait();
            data_cache_hit_invalidate(screen->buffer, screen->stride * screen->height);
            unsigned colored = 0;
            const uint16_t *pixels = UncachedAddr(screen->buffer);
            for (unsigned y = 20; y < 220; ++y)
                for (unsigned x = 20; x < 300; ++x) {
                    const uint16_t color = pixels[y * (screen->stride / 2) + x];
                    colored += ((color >> 11) > 8 || ((color >> 6) & 31) > 8 || ((color >> 1) & 31) > 8);
                }
            /* At zero rotation the first triangle's world normal is (0,0,-144).
             * Dot(normal, eye-centroid)>0 for eye=(0,0,-25), so the CPU renderer
             * calls it front-facing. With RH view and screen Y down its signed
             * projected area is negative. Check both Tiny3D culling flags. */
            debugf("DL64 tiny3d_proof pixels=%u matrices=2 shared_triangles=2 cull=%s cpu_front=1\n",
                   colored, cull == T3D_FLAG_CULL_FRONT ? "front" : cull == T3D_FLAG_CULL_BACK ? "back" : "none");
            if (frame == 90) assertf(colored == 0, "CULL_FRONT should reject the CPU-front triangle");
            else assertf(colored > 500, "Tiny3D RSP/RDP proof produced no visible geometry");
            const unsigned capture = frame / 30 + 1;
            debugf("DL64 capture_begin id=%u width=320 height=240 format=rgba5551\n", capture);
            for (unsigned y = 0; y < 240; ++y) {
                static const char hex[] = "0123456789abcdef";
                char row[1281];
                for (unsigned x = 0; x < 320; ++x) {
                    const uint16_t pixel = pixels[y * (screen->stride / 2) + x];
                    for (unsigned digit = 0; digit < 4; ++digit)
                        row[x * 4 + digit] = hex[(pixel >> ((3 - digit) * 4)) & 15];
                }
                row[1280] = 0;
                debugf("DL64 capture_row id=%u y=%u data=%s\n", capture, y, row);
            }
            debugf("DL64 capture_end id=%u\n", capture);
            if (frame == 90) {
                debugf("DL64 capture_complete views=4\n");
                debugf("DL64 tiny3d_proof complete\n");
            }
        }
    }
}
