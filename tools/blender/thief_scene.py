"""Attach source topology and face provenance to a DarkLantern scene checkout."""
import json
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def prepare(work, part):
    work = Path(work)
    scene = bpy.context.scene
    report = json.loads((work/"report.json").read_bytes())
    checkout = json.loads(scene['dl64_scene_checkout'])
    expected = 'thief_bafford.json' if part == 'full' else 'thief_bafford_entry.json'
    if checkout['level'] != expected:
        raise ValueError(f"Wrong scene checkout: {checkout['level']}; expected {expected}")
    if scene.get('thief_source_sha256') == report['source_sha256']:
        return
    annotation = json.loads((work/"face-annotations.json").read_bytes())
    for name in ("report.json", "world-topology.json", "source-objects.json"):
        text = bpy.data.texts.new("THIEF_"+name.upper())
        text.write((work/name).read_text(encoding='utf-8'))
    readme = bpy.data.texts.new("READ ME | Bafford conversion")
    readme.write("Lord Bafford's Manor, the first campaign mission (miss2.mis).\n"
        "Architecture, original texture pixels/UVs, source IDs, portal graph and BSP are retained.\n"
        "DarkLantern > Export level changes stages edits for the project importer.\n"
        "The full mission is a host-only scale study. The separate entry probe can run on N64.\n"
        "Collision contains only exact box-shaped faces; water, ladders, doors and scripts are omitted.\n"
        "Lights/guards/loot are reference markers here; author gameplay in DarkLantern.\n"
        "THIEF_REPORT.JSON records the exact counts and adaptations.\n"
        "Terrain materials have dl64_source containing original Material sound tags.\n"
        "Mesh face attributes thief_cell_id / thief_polygon_id identify original WR polygons.\n")
    regions = {}
    architecture = next(c for c in scene.collection.children if c.name.startswith('Architecture'))
    for obj in list(architecture.objects):
        model = obj.get('dl64_model_id')
        if not model or model not in annotation:
            continue
        mesh = obj.data
        attrs = annotation[model]
        if len(mesh.polygons) != len(attrs):
            raise ValueError(f"{model}: face provenance does not match checkout topology")
        for axis, name in enumerate(('thief_cell_id','thief_polygon_id')):
            attr = mesh.attributes.new(name, 'INT', 'FACE')
            for item, value in zip(attr.data, attrs):
                item.value = value[axis]
        meta = json.loads(obj.get('dl64_source','{}'))
        region = tuple(meta.get('region', (0,0,0)))
        if region not in regions:
            collection = bpy.data.collections.new('Region ' + ', '.join(map(str,region)))
            architecture.children.link(collection)
            regions[region] = collection
        architecture.objects.unlink(obj)
        regions[region].objects.link(obj)
    data = json.loads(scene['dl64_scene_checkout'])
    context = {row['id']:row for row in data['context']}
    for obj in scene.objects:
        ref = obj.get('dl64_reference_only')
        if ref not in context:
            continue
        row = context[ref]
        obj['dl64_source'] = json.dumps(row.get('source',{}))
        if row['kind'] == 'light':
            obj.color = (*row.get('color',[1,1,1]),1)
            obj.empty_display_size = .15
        elif row['kind'] == 'guard':
            obj.color = (.8,.16,.06,1)
            obj.empty_display_size = .5
        elif row['kind'] == 'loot':
            obj.color = (1,.7,.05,1)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                space.clip_end = 500
                space.shading.type = 'MATERIAL'
                space.region_3d.view_distance = 130 if part == 'full' else 18
                space.region_3d.view_location = (0,0,4) if part == 'full' else (-6.52,-49.8,0)
    scene['thief_source_mission'] = report['source_mission']
    scene['thief_source_sha256'] = report['source_sha256']
    scene['thief_conversion_part'] = part
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath, compress=True)


if __name__ == '__main__':
    prepare(*sys.argv[sys.argv.index('--')+1:])
