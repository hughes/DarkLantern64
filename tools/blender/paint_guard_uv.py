"""Fit the painted guard atlas to an existing saved rig without rebuilding it.

Run in a private background Blender process loading art/guard.blend. The source
atlas is packed and remains the material's editable image. Target preview is a
separately named packed image; it never replaces authoring pixels on export.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import runpy
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
# Half-open rectangles in the generated sheet's top-left 64-pixel grid.
REGIONS = {
    'face': (0, 0, 16, 16.7), 'skin': (16, 0, 32, 16.7),
    'helmet': (32, 0, 48, 16.7), 'shoulder': (48, 0, 64, 16.7),
    'front': (0, 16.7, 16, 38.3), 'back': (16, 16.7, 32, 38.3),
    'sleeve': (32, 16.7, 48, 51), 'trousers': (48, 16.7, 64, 51),
    'glove': (0, 38.3, 16, 51), 'boot': (16, 38.3, 32, 51),
    'belt': (0, 51, 16, 64), 'red': (16, 51, 32, 64),
    'brass': (32, 51, 48, 64), 'dark': (48, 51, 64, 64),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def geometry_signature(obj):
    """UV-independent source geometry, shading and binding identity."""
    mesh = obj.data
    return digest({
        'positions': [list(v.co) for v in mesh.vertices],
        'polygons': [list(p.vertices) for p in mesh.polygons],
        'smooth': [p.use_smooth for p in mesh.polygons],
        'weights': [[(obj.vertex_groups[g.group].name, g.weight) for g in v.groups] for v in mesh.vertices],
        'corner_normals': [list(n.vector) for n in mesh.corner_normals],
        'object_matrix': [list(row) for row in obj.matrix_world],
    })


def rectangle_uv(region, u, v):
    x0, y0, x1, y1 = REGIONS[region]
    # A full target texel inset excludes generated borders and neighboring tiles.
    x = x0 + 1 + max(0, min(1, u)) * (x1 - x0 - 2)
    y = y0 + 1 + (1 - max(0, min(1, v))) * (y1 - y0 - 2)
    return x / 64, 1 - y / 64


def fitted_uvs(obj):
    helpers = runpy.run_path(str(ROOT / 'tools/blender/guard_character.py'), run_name='guard_uv_helpers')
    convert = helpers['TO_GAME']
    mesh = obj.data
    uv = mesh.uv_layers.active
    palette = mesh.attributes.get('dl64_original_palette')
    if palette is None:
        palette = mesh.attributes.new('dl64_original_palette', 'INT', 'FACE')
        for polygon in mesh.polygons:
            tex = uv.data[polygon.loop_start].uv
            palette.data[polygon.index].value = min(3, int(tex.x * 4)) + 4 * min(3, int(tex.y * 4))
    regions = {}
    coords = [convert @ v.co for v in mesh.vertices]
    for poly in mesh.polygons:
        tile = palette.data[poly.index].value
        points = [coords[i] for i in poly.vertices]
        center = sum(points, Vector()) / len(points)
        normal = convert.to_3x3() @ poly.normal
        group_names = {obj.vertex_groups[g.group].name for i in poly.vertices for g in mesh.vertices[i].groups if g.weight > .999}
        bone = sorted(group_names)[0]
        arm = any('arm_' in name or 'hand_' in name for name in group_names)
        leg = any('thigh_' in name or 'shin_' in name or 'foot_' in name for name in group_names)
        region = 'dark'
        mode = 'cylinder'
        low, high = min(p.y for p in points), max(p.y for p in points)
        cx, cz = center.x, center.z
        if tile == 13:
            region, low, high = 'red', 1.188, 1.244
        elif tile == 11:
            region, mode = 'brass', 'box'
        elif tile == 10:
            # Keep the existing eye polygons, but map the same facial projection
            # as the underlying head rather than a second pair of black eyes.
            region, mode, low, high = 'face', 'face', 1.535, 1.74
        elif tile in (8, 9):
            region = 'face' if bone == 'head' and center.z > .015 and normal.z > -.05 else 'skin'
            mode = 'face' if region == 'face' else 'cylinder'
            low, high = (1.535, 1.74) if bone == 'head' else (1.44, 1.57)
            cx, cz = 0, .017 if bone == 'head' else 0
        elif tile in (3, 4, 5):
            region = 'helmet' if bone == 'head' else 'shoulder'
            low, high = (1.694, 1.845) if bone == 'head' else (1.305, 1.415)
            cx, cz = (0, 0) if bone == 'head' else ((.275 if center.x > 0 else -.275), 0)
        elif tile in (6, 7):
            if leg:
                region, low, high = 'boot', .004, .44
                cx, cz = .115 if center.x > 0 else -.115, .014
                if 'foot_' in bone: mode = 'box'
            elif arm:
                region, low, high = 'glove', .779, 1.07
                cx, cz = .39 if center.x > 0 else -.39, .015
                if 'hand_' in bone: mode = 'box'
            else:
                region, low, high, cx, cz = 'belt', .995, 1.06, 0, 0
        elif arm:
            region, low, high = 'sleeve', 1.07, 1.32
        elif leg:
            region, low, high = 'trousers', .44, .89
            cx, cz = .115 if center.x > 0 else -.115, .014
        else:
            region = 'front' if center.z >= 0 else 'back'
            mode, low, high, cx, cz = 'torso', .84, 1.45, 0, 0
        regions[region] = regions.get(region, 0) + 1
        raw = []
        for p in points:
            if arm and region in ('red', 'sleeve'):
                # Follow the original bent sleeve center line, including bridge.
                sign = 1 if center.x > 0 else -1
                cx = sign * (.341 + (p.y - 1.17) / .15 * (.285 - .341)) if p.y >= 1.17 else sign * (.367 + (p.y - 1.07) / .10 * (.341 - .367))
                cz = 0 if p.y >= 1.17 else .004 * (1 - (p.y - 1.07) / .10)
            angle = math.atan2(p.x - cx, p.z - cz)
            if mode == 'face':
                u, v = .5 - p.x / .205, (p.y - low) / (high - low)
            elif mode == 'torso':
                u = .5 - angle / math.pi if region == 'front' else ((angle - math.pi / 2) % (2 * math.pi)) / math.pi
                v = (p.y - low) / (high - low)
            elif mode == 'box' or abs(normal.y) > .95:
                # Continuous face projections for box props and ring end caps.
                axis = max(range(3), key=lambda i: abs(normal[i]))
                ax, ay = ((2, 1) if axis == 0 else (0, 2) if axis == 1 else (0, 1))
                amin, amax = min(q[ax] for q in points), max(q[ax] for q in points)
                bmin, bmax = min(q[ay] for q in points), max(q[ay] for q in points)
                u = (p[ax] - amin) / max(amax - amin, 1e-6)
                v = (p[ay] - bmin) / max(bmax - bmin, 1e-6)
                if axis == 2: u = 1 - u
            else:
                # Ring meshes have a vertex on +X, so put the wrap seam there;
                # a seam halfway across a polygon would smear a whole tile.
                u, v = (.25 - angle / (2 * math.pi)) % 1, (p.y - low) / max(high - low, 1e-6)
            raw.append([u, v])
        if mode == 'cylinder' and max(q[0] for q in raw) - min(q[0] for q in raw) > .5:
            for q in raw:
                if q[0] < .5: q[0] += 1
        for loop, (u, v) in zip(poly.loop_indices, raw):
            uv.data[loop].uv = rectangle_uv(region, u, v)
    obj['dl64_uv_layout'] = 'painted-guard-v1'
    obj['dl64_uv_rectangles'] = json.dumps(REGIONS)
    mesh.update()
    return regions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--atlas', type=Path, required=True)
    parser.add_argument('--preview', type=Path)
    parser.add_argument('--save', type=Path, default=ROOT / 'art/guard.blend')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    scene = bpy.context.scene
    obj = next(o for o in scene.objects if o.type == 'MESH' and o.get('dl64_character_id') == 'basic-guard')
    character_path = ROOT / 'content/assets/guard/guard.character.json'
    original = json.loads(character_path.read_text(encoding='utf-8'))
    before = geometry_signature(obj)
    regions = fitted_uvs(obj)
    assert geometry_signature(obj) == before, 'UV editing changed geometry/shading/binding'
    material = obj.data.materials[0]
    node = next(n for n in material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image)
    image = bpy.data.images.load(str(args.atlas.resolve()), check_existing=False)
    image.name = 'Guard | PAINT THIS painted atlas source'
    image.filepath = bpy.path.relpath(str(args.atlas.resolve()), start=str(args.save.resolve().parent))
    image.pack()
    node.image = image
    node.interpolation = 'Closest'
    material['dl64_texture_export_name'] = 'guard-atlas-painted.png'
    material['dl64_texture_recipe'] = '64x64 CI4; refresh cooked preview through the level cooker'
    if args.preview:
        preview = bpy.data.images.load(str(args.preview.resolve()), check_existing=False)
        preview.name = 'Guard | TARGET PREVIEW 64px CI4 (generated; do not paint)'
        preview.pack()
        preview.use_fake_user = True
    bpy.ops.wm.save_as_mainfile(filepath=str(args.save.resolve()))
    helpers = runpy.run_path(str(ROOT / 'tools/blender/guard_character.py'), run_name='guard_uv_export')
    result = helpers['export'](scene, ROOT / 'content/assets/guard')
    revised = json.loads(character_path.read_text(encoding='utf-8'))
    for key in ('bones', 'clips', 'sockets'):
        assert original[key] == revised[key], 'UV edit changed ' + key
    def corners(character):
        mesh = character['mesh']
        return [(mesh['vertices'][i], mesh['normals'][i], mesh['joints'][i]) for i in mesh['indices']]
    assert corners(original) == corners(revised), 'UV edit changed expanded triangle geometry or bindings'
    report = {'passed': True, 'geometry_binding_sha256': before, 'geometry_binding_unchanged': True,
              'bones_clips_sockets_identical': True, 'expanded_triangle_positions_normals_joints_identical': True,
              'original_vertices': len(original['mesh']['vertices']), 'original_triangles': len(original['mesh']['indices']) // 3,
              'source': str(args.save), 'regions': REGIONS, 'polygons_by_region': regions, 'export': result}
    out = ROOT / 'build/guard-atlas-study/uv-report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
