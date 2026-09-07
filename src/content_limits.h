#ifndef DARKLANTERN_CONTENT_LIMITS_H
#define DARKLANTERN_CONTENT_LIMITS_H

/* Shared authoring/runtime geometry limits. Instance counts include repeated
 * meshes; per-mesh scratch remains bounded independently of the scene total. */
#define DL_MAX_MESH_VERTICES 4096
#define DL_MAX_SCENE_VERTICES 6144
#define DL_MAX_SCENE_TRIANGLES 4096
#define DL_MAX_MODELS 128

#endif
