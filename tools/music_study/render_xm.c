/* Host audition using the inspected libdragon/libxm sources supplied at build
 * time. This is a reference renderer, not an emulation of the RSP mixer. */
#include "xm_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr, "Usage: render_xm input.xm[64] output.f32 frames block_frames\n");
        return 2;
    }
    FILE *in = fopen(argv[1], "rb");
    if (!in) return 3;
    char magic[4];
    if (fread(magic, 1, 4, in) != 4) return 3;
    rewind(in);
    xm_context_t *ctx = NULL;
    int error;
    if (!memcmp(magic, "XM64", 4)) error = xm_context_load(&ctx, in, 32000);
    else {
        fseek(in, 0, SEEK_END);
        long size = ftell(in);
        rewind(in);
        char *data = malloc(size);
        if (!data || fread(data, 1, size, in) != (size_t)size) return 3;
        error = xm_create_context_safe(&ctx, data, size, 32000);
        free(data);
    }
    fclose(in);
    if (error || !ctx) return 4;
    FILE *out = fopen(argv[2], "wb");
    if (!out) return 5;
    size_t frames = strtoul(argv[3], NULL, 10);
    size_t block = strtoul(argv[4], NULL, 10);
    if (!frames || block < 1 || block > 4096) return 6;
    float pcm[8192];
    float peak = 0;
    for (size_t pos = 0; pos < frames;) {
        size_t n = frames - pos < block ? frames - pos : block;
        xm_generate_samples(ctx, pcm, n);
        for (size_t i = 0; i < n * 2; ++i) {
            if (!isfinite(pcm[i])) return 7;
            if (fabsf(pcm[i]) > peak) peak = fabsf(pcm[i]);
        }
        if (fwrite(pcm, sizeof(float) * 2, n, out) != n) return 8;
        pos += n;
    }
    if (fclose(out)) return 8;
    printf("{\"frames\":%zu,\"rate\":32000,\"peak\":%.9g,\"loops\":%u,\"channels\":%u,\"instruments\":%u}\n",
        frames, peak, xm_get_loop_count(ctx), ctx->module.num_channels,
        ctx->module.num_instruments);
    xm_free_context(ctx);
    return 0;
}
