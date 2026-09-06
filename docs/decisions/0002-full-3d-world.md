# Decision 0002: Full 3D world and model-based content

Status: **accepted**, 2026-09-05, by explicit project-owner direction.

DarkLantern64 uses a fully three-dimensional environment. Levels are assembled from models with independent positions, rotations, and scales. Level geometry need not align to a grid. Placeholder scenery, guards, and interactable objects must be meshes too.

The source scene records XYZ transforms and reusable asset references. A grid may eventually be an optional snapping aid; it must not define the world, its collision, or the runtime content contract. The LightEngine viewport and N64 runtime consume the same model vertices and transforms.

The first implementation uses indexed OBJ meshes, a host content compiler, CPU model/view/projection transforms, and libdragon's RDP triangle rasterization with a depth buffer. This is a starting backend to measure, not a commitment to CPU geometry processing for the finished game. An RSP geometry backend such as Tiny3D remains a candidate. The installed SDK's [triangle API](https://libdragon.dev/ref/rdpq__tri_8h.html) provides screen-space triangle drawing; model transforms, projection, and clipping are the game's responsibility.

Initial collision uses upright boxes with arbitrary yaw and XYZ placement, separate from visual meshes. Gravity, raised floors, stairs, and jumping exercise vertical space. Sloped surfaces and detailed mesh collision require further collision work; visual geometry can already have arbitrary Euler rotations. These initial collision limits do not constrain future levels to a tile grid or fixed height.

Verification must include non-axis-aligned scenery, models at different heights, vertical camera movement, depth occlusion, and an editor transform edit reaching the ROM. Original N64 and M64 verification remains pending until performed on those devices.
