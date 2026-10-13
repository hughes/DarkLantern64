"""Render a local Thief scene study without saving edits to its .blend source.

Invoke through background Blender:
  blender --background --disable-autoexec art/thief-bafford.blend
    --python tools/blender/render_thief.py -- build/thief-bafford full

``full`` creates a ceiling cutaway overview. ``entry`` creates a first-person
preview at the imported spawn. Both use the original imported texture materials;
lighting is a visualization approximation, not a Dark Engine photometric match.
"""
import json
import math
from pathlib import Path
import sys

import bpy
import bmesh
from mathutils import Vector


def terrain_objects(scene):
    return [obj for obj in scene.objects if obj.type == "MESH" and obj.get("dl64_model_id")]


def cut_ceilings(objects):
    """Remove downward-facing triangles from in-memory meshes only."""
    removed = 0
    for obj in objects:
        # Make a private copy even if a scene has linked mesh instances.
        obj.data = obj.data.copy()
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
        mesh = bmesh.new()
        mesh.from_mesh(obj.data)
        doomed = [face for face in mesh.faces
                  if (normal_matrix @ face.normal).normalized().z < -.8]
        removed += len(doomed)
        bmesh.ops.delete(mesh, geom=doomed, context="FACES")
        mesh.to_mesh(obj.data)
        mesh.free()
        obj.data.update()
    return removed


def camera_at(scene, position, target, *, orthographic=None, lens=24):
    data = bpy.data.cameras.new("Thief proof camera")
    obj = bpy.data.objects.new("Thief proof camera", data)
    scene.collection.objects.link(obj)
    obj.location = position
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()
    data.clip_start = .025
    data.clip_end = 1000
    data.lens = lens
    if orthographic is not None:
        data.type = "ORTHO"
        data.ortho_scale = orthographic
    scene.camera = obj
    return obj


def add_light(scene, kind, position, energy, *, color=(1, 1, 1), size=None):
    data = bpy.data.lights.new("Thief proof " + kind.lower(), kind)
    obj = bpy.data.objects.new(data.name, data)
    scene.collection.objects.link(obj)
    obj.location = position
    data.energy = energy
    data.color = color
    if kind == "AREA":
        data.shape = "DISK"
        data.size = size or 15
    elif kind == "POINT":
        data.shadow_soft_size = .15
        if hasattr(data, "use_shadow"):
            data.use_shadow = False
    return obj


def source_context(scene):
    data = json.loads(scene["dl64_scene_checkout"])
    return {row["id"]: row for row in data.get("context", [])}


def preview_lights(scene, context, camera):
    """Use the nearest original source light markers as point-light previews."""
    candidates = []
    for obj in scene.objects:
        ident = obj.get("dl64_reference_only")
        row = context.get(ident)
        if row and row["kind"] == "light":
            distance = (obj.matrix_world.translation - camera.location).length
            if distance < 25:
                candidates.append((distance, obj.matrix_world.translation.copy(), row))
    for _distance, point, row in sorted(candidates, key=lambda item: item[0])[:16]:
        add_light(scene, "POINT", point, max(0, row.get("intensity", 1)) * 110,
                  color=tuple(row.get("color", [1, 1, 1])))
    return min(16, len(candidates))


def render(work, part):
    if part not in ("full", "entry"):
        raise ValueError("render part must be full or entry")
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    objects = terrain_objects(scene)
    if not objects:
        raise ValueError("No imported terrain meshes in source scene")
    # Only the new visualization camera/lights participate. Collision and
    # gameplay empties remain available in the .blend and are not rendered.
    for obj in scene.objects:
        if obj.type in ("LIGHT", "CAMERA"):
            obj.hide_render = True
    corners = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    lower = Vector(tuple(min(point[a] for point in corners) for a in range(3)))
    upper = Vector(tuple(max(point[a] for point in corners) for a in range(3)))
    center = (lower + upper) / 2
    extent = upper - lower
    removed = light_count = 0
    if part == "full":
        removed = cut_ceilings(objects)
        target = Vector((center.x, center.y, max(0, center.z)))
        camera = camera_at(scene, target + Vector((115, -145, 185)), target,
                           orthographic=max(extent.x, extent.y) * 1.32)
        basis = camera.rotation_euler.to_matrix()
        right, up = basis.col[0], basis.col[1]
        xs = [(point - target).dot(right) for point in corners]
        ys = [(point - target).dot(up) for point in corners]
        camera.location += right * ((min(xs) + max(xs)) / 2) + up * ((min(ys) + max(ys)) / 2)
        # In a landscape Blender render ortho_scale describes frame width.
        camera.data.ortho_scale = max((max(ys) - min(ys)) * (1280 / 960), max(xs) - min(xs)) * 1.12
        key = add_light(scene, "SUN", (0, 0, 120), 2.2, color=(1, .93, .82))
        key.rotation_euler = (math.radians(24), math.radians(-28), math.radians(-30))
        key.data.angle = math.radians(20)
        fill = add_light(scene, "SUN", (0, 0, 120), .9, color=(.65, .76, 1))
        fill.rotation_euler = (math.radians(-32), math.radians(24), math.radians(145))
        scene.render.resolution_x, scene.render.resolution_y = 1280, 960
        filename = "overview-cutaway.png"
    else:
        context = source_context(scene)
        spawn = next((obj for obj in scene.objects
                      if context.get(obj.get("dl64_reference_only"), {}).get("kind") == "spawn"), None)
        if spawn is None:
            raise ValueError("Entry scene has no imported player spawn marker")
        position = spawn.matrix_world.translation + Vector((0, 0, 1.5))
        # The source start is heading 45 degrees: (+X,+Y) in Blender's Z-up
        # axes, corresponding to DarkLantern Y-up yaw 135 degrees.
        target = position + Vector((math.sqrt(.5), math.sqrt(.5), -.045)) * 20
        camera = camera_at(scene, position, target, lens=23)
        light_count = preview_lights(scene, context, camera)
        sun = add_light(scene, "SUN", (0, 0, 100), .7, color=(.63, .73, 1))
        sun.rotation_euler = (math.radians(30), math.radians(-20), math.radians(-15))
        fill = add_light(scene, "AREA", position + Vector((0, 0, 4)), 140,
                         color=(.75, .80, 1), size=12)
        fill.rotation_euler = (Vector((position.x, position.y, position.z - 1)) - fill.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.resolution_x, scene.render.resolution_y = 960, 640
        filename = "entry-source-preview.png"
    scene.world = bpy.data.worlds.new("Thief proof backdrop")
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (.025, .03, .045, 1)
    background.inputs["Strength"].default_value = .5
    engines = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.filepath = str(work / filename)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    if hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "taa_render_samples"):
            scene.eevee.taa_render_samples = 32
    bpy.ops.render.render(write_still=True)
    proof = dict(version=1, part=part, source_blend=bpy.data.filepath,
                 image=filename, source_meshes=len(objects),
                 ceiling_triangles_removed=removed, source_point_lights_used=light_count,
                 camera_position=list(camera.location), camera_rotation=list(camera.rotation_euler),
                 bounds=[list(lower), list(upper)],
                 resolution=[scene.render.resolution_x, scene.render.resolution_y],
                 method="Eevee rendering of imported original materials; visualization lighting approximation",
                 source_scene_saved=False,
                 cutaway=part == "full")
    (work / f"render-{part}.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof), flush=True)


if __name__ == "__main__":
    render(*sys.argv[sys.argv.index("--") + 1:])
