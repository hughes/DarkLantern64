"""Render the saved guard's painted source and cooked atlas in an owned process.

No saved scene is changed. Output is deliberately a neutral material/UV check,
not a claim about N64 lighting or frame rate.
"""
from pathlib import Path
import argparse
import json
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/guard-atlas-study/blender')
    parser.add_argument('--front-face-only', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    args.output.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    obj = next(o for o in scene.objects if o.type == 'MESH' and o.get('dl64_character_id') == 'basic-guard')
    rig = obj.find_armature()
    for other in scene.objects:
        other.hide_render = other != obj
    rig.animation_data.action = next(a for a in bpy.data.actions if a.get('dl64_clip_id') == 'idle')
    scene.frame_set(rig.animation_data.action['dl64_frame_start'])
    node = next(n for n in obj.data.materials[0].node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    authoring = node.image
    preview = bpy.data.images.load(str(args.preview.resolve()), check_existing=False)
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 512
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.world.use_nodes = True
    world = next(n for n in scene.world.node_tree.nodes if n.type == 'BACKGROUND')
    world.inputs['Color'].default_value = (.22, .24, .28, 1)
    world.inputs['Strength'].default_value = .65
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = 0
    for name, location, power, size in (
        ('UV check key', (3, -4, 4), 340, 4),
        ('UV check fill', (-3, -2, 2), 160, 3),
        ('UV check rim', (1, 3, 3), 240, 3),
    ):
        light = bpy.data.lights.new(name, 'AREA')
        light.energy, light.shape, light.size = power, 'DISK', size
        placed = bpy.data.objects.new(name, light)
        scene.collection.objects.link(placed)
        placed.location = location
        placed.rotation_euler = (Vector((0, 0, 1)) - placed.location).to_track_quat('-Z', 'Y').to_euler()
    camera = bpy.data.objects.new('UV check camera', bpy.data.cameras.new('UV check camera'))
    scene.collection.objects.link(camera)
    camera.data.type = 'ORTHO'
    scene.camera = camera
    records = []
    for name, location, target, scale, image in (
        ('guard-painted-source-front', (0, -4, 1), (0, 0, .94), 2.1, authoring),
        ('guard-ci4-front', (0, -4, 1), (0, 0, .94), 2.1, preview),
        ('guard-ci4-three-quarter', (2.6, -4, 1.4), (0, 0, .94), 2.1, preview),
        ('guard-ci4-back', (-1.2, 4, 1.3), (0, 0, .94), 2.1, preview),
        ('guard-ci4-face', (.10, -3, 1.67), (0, 0, 1.67), .47, preview),
    ):
        if args.front_face_only and name not in ('guard-ci4-front', 'guard-ci4-face'):
            continue
        node.image = image
        camera.location = location
        camera.rotation_euler = (Vector(target) - camera.location).to_track_quat('-Z', 'Y').to_euler()
        camera.data.ortho_scale = scale
        scene.render.filepath = str(args.output / (name + '.png'))
        bpy.ops.render.render(write_still=True)
        records.append(scene.render.filepath)
    print(json.dumps({'passed': True, 'images': records, 'saved_source_modified': False}, indent=2))


if __name__ == '__main__':
    main()
