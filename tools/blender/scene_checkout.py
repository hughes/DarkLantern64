"""Build a new editable Blender source from a host-prepared canonical level."""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Euler, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from darklantern64_export.scene_bridge import AXIS, collect, digest


def matrix(row):
    t = row["transform"]
    game = Matrix.LocRotScale(Vector(t["position"]), Euler([math.radians(x) for x in t["rotation"]], 'XYZ').to_quaternion(), Vector(t["scale"]))
    return AXIS.inverted() @ game @ AXIS


def checkout(data):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    collections = {}
    for name in ("Architecture | edit meshes and placement", "Collision | edit boxes and floor triangles", "Gameplay | reference only"):
        collection = bpy.data.collections.new(name)
        scene.collection.children.link(collection)
        collections[name.split(' |')[0]] = collection
    materials, meshes = {}, {}
    for ident, spec in data["materials"].items():
        mat = bpy.data.materials.new(ident)
        mat["dl64_material_id"] = ident
        if 'source' in spec:
            mat['dl64_source'] = json.dumps(spec['source'])
        mat.diffuse_color = spec["color"]
        mat.use_nodes = True
        shader = mat.node_tree.nodes.get('Principled BSDF')
        shader.inputs['Base Color'].default_value = spec["color"]
        mat.use_backface_culling = not spec.get("double_sided", True)
        if "texture" in spec:
            node = mat.node_tree.nodes.new('ShaderNodeTexImage')
            node.image = bpy.data.images.load(str(Path(data["project"]) / 'content' / spec["texture"]["uri"]), check_existing=True)
            node.image.pack()
            node.interpolation = 'Closest'
            mat.node_tree.links.new(node.outputs['Color'], shader.inputs['Base Color'])
        materials[ident] = mat
    for ident, spec in data["meshes"].items():
        source = spec["mesh"]
        mesh = bpy.data.meshes.new(ident)
        # Cooker vertices split at UV/normal seams. Rejoin their positions for
        # normal mesh editing; keep UVs and normals on face corners below.
        positions, by_position, remap = [], {}, []
        for x,y,z in source["vertices"]:
            position = (x,-z,y)
            if position not in by_position:
                by_position[position] = len(positions)
                positions.append(position)
            remap.append(by_position[position])
        corners = source["indices"]
        mesh.from_pydata(positions, [],
                         [[remap[index] for index in corners[i:i+3]] for i in range(0,len(corners),3)])
        if "uvs" in source:
            uv = mesh.uv_layers.new(name='UVMap')
            for loop in mesh.loops:
                u,v = source["uvs"][corners[loop.index]]
                uv.data[loop.index].uv = (u, 1-v)
        if "normals" in source:
            for face in mesh.polygons:
                face.use_smooth = True
            mesh.normals_split_custom_set([(source['normals'][index][0],-source['normals'][index][2],source['normals'][index][1]) for index in corners])
        meshes[ident] = mesh
    for row in data["entities"]:
        mesh = meshes.get(row.get("model"))
        if mesh and len(mesh.materials) == 0:
            mesh.materials.append(materials[row["material"]])
        obj = bpy.data.objects.new(row["id"], mesh)
        collections['Architecture'].objects.link(obj)
        obj.matrix_world = matrix(row)
        obj["dl64_entity_id"] = row["id"]
        if 'source' in row:
            obj['dl64_source'] = json.dumps(row['source'])
        if 'collision_authoring' in row:
            obj['dl64_collision_authoring'] = json.dumps(row['collision_authoring'])
        if mesh:
            obj["dl64_model_id"] = row["model"]
            obj["dl64_scene_material_id"] = row["material"]
            obj.material_slots[0].link = 'OBJECT'
            obj.material_slots[0].material = materials[row["material"]]
        else:
            obj.empty_display_type = 'PLAIN_AXES'
            obj.empty_display_size = .15
        if "collider" in row:
            proxy = bpy.data.objects.new(row["id"] + ' | box', None)
            collections['Collision'].objects.link(proxy)
            proxy.parent = obj
            proxy.empty_display_type = 'CUBE'
            proxy.empty_display_size = 1
            proxy.show_in_front = True
            proxy.color = (.2, .8, 1, 1)
            proxy["dl64_collision_proxy"] = True
            x,y,z = row["collider"]["center"]
            proxy.location = (x,-z,y)
            x,y,z = row["collider"]["half_size"]
            proxy.scale = (x,z,y)
        if "floor" in row:
            floor_mesh = bpy.data.meshes.new(row["id"] + ' | floor')
            floor_mesh.from_pydata([(x,-z,y) for x,y,z in row["floor"]["vertices"]], [], [(0,1,2)])
            proxy = bpy.data.objects.new(row["id"] + ' | floor', floor_mesh)
            collections['Collision'].objects.link(proxy)
            proxy.parent = obj
            proxy.display_type = 'WIRE'
            proxy.show_in_front = True
            proxy.color = (.2, .8, 1, 1)
            proxy["dl64_floor_proxy"] = True
    for row in data["context"]:
        obj = bpy.data.objects.new(row['kind'] + ' | ' + row['id'], None)
        collections['Gameplay'].objects.link(obj)
        obj.matrix_world = matrix(row)
        obj.empty_display_type = 'ARROWS' if row['kind'] in ('guard','spawn') else 'SPHERE'
        obj.empty_display_size = .3
        obj.show_name = True
        obj.hide_select = True
        obj['dl64_reference_only'] = row['id']
        if 'source' in row:
            obj['dl64_source'] = json.dumps(row['source'])
    data['baseline'] = {row['id']: {k:row[k] for k in ('transform','model','material','collider','floor','collision_authoring') if k in row} for row in data.pop('entities')}
    bpy.context.view_layer.update()
    _, payloads = collect(bpy.context, data)
    for ident, spec in data['meshes'].items():
        spec['baseline_export_sha256'] = digest(payloads[ident])
        del spec['mesh']
    scene['dl64_scene_checkout'] = json.dumps(data)
    from darklantern64_export.collision_tools import restore_relations
    restore_relations(scene)
    # Hide wires by per-object visibility, which preserves dependency graph
    # evaluation. The artist can toggle the collection with the Outliner eye.
    for obj in collections['Collision'].objects:
        obj.hide_set(True)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.type = 'SOLID'
                area.spaces.active.shading.color_type = 'TEXTURE'
                area.spaces.active.clip_end = 500
                area.spaces.active.region_3d.view_distance = 28
                area.spaces.active.region_3d.view_location = (0,0,4)
    bpy.ops.wm.save_as_mainfile(filepath=data['destination'], compress=True)


if __name__ == '__main__':
    prepared = Path(sys.argv[sys.argv.index('--')+1])
    checkout(json.loads(prepared.read_bytes()))
