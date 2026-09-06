"""Run with Blender --background --factory-startup --python-exit-code 1 --python.

An isolated fixture verifies evaluated geometry, winding, units, UVs, image
publication and re-export through the actual game OBJ parser. It retains its
files under build/blender-export-smoke/ for host texture-cooker verification.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools/blender"), str(ROOT / "tools")]
from darklantern64_export import ExportError, export_pack, register, unregister, _obj
from compile_level import read_obj


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    parent = ROOT / "build/blender-export-smoke"
    parent.mkdir(parents=True, exist_ok=True)
    content = parent / ("fixture-" + uuid.uuid4().hex)
    content.mkdir()
    output = content / "assets/test"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.context.scene.unit_settings.scale_length = .5

    image = bpy.data.images.new("export-smoke-image", width=32, height=32, alpha=True)
    image.colorspace_settings.name = "sRGB"
    pixels = [value for y in range(32) for x in range(32)
              for value in (x / 31, y / 31, .25, 1)]
    image.pixels.foreach_set(pixels)
    image.update()
    material = bpy.data.materials.new("Export smoke atlas")
    material["dl64_material_id"] = "mat-smoke-atlas"
    material.use_nodes = True
    material.use_backface_culling = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Emission Color"].default_value = (.1, .05, 0, 1)
    shader.inputs["Emission Strength"].default_value = 2
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])

    mesh = bpy.data.meshes.new("Pyramid")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (.5, .5, 1)], [],
                     [(3, 2, 1, 0), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)])
    mesh.materials.append(material)
    uv = mesh.uv_layers.new(name="UVMap")
    for index, loop in enumerate(uv.data):
        loop.uv = (.125 + .25 * (index % 3), .75 if index % 2 else .25)
    obj = bpy.data.objects.new("Smoke pyramid", mesh)
    obj["dl64_asset_id"] = "smoke-pyramid"
    bpy.context.collection.objects.link(obj)
    obj.location = (20, -30, 7)
    obj.rotation_euler = (0, 0, math.pi / 2)
    obj.scale = (-2, 3, .5)
    obj.modifiers.new(name="Evaluated triangulation", type="TRIANGULATE")
    bpy.context.view_layer.update()

    # Registration catches Blender RNA/property compatibility errors too.
    register()
    assert hasattr(bpy.ops.export_scene, "darklantern64_pack")
    unregister()
    source_state = (image.filepath_raw, image.file_format, image.is_dirty, len(bpy.data.images))
    pack = export_pack(bpy.context, content, output, objects=[obj])
    assert not any(line.startswith('vn ') for line in (output / 'smoke-pyramid.obj').read_text().splitlines())
    assert (image.filepath_raw, image.file_format, image.is_dirty, len(bpy.data.images)) == source_state
    assert pack["materials"][0]["double_sided"] is False
    assert abs(pack["materials"][0]["emissive"][0] - .2) < 1e-6
    model = read_obj(output / "smoke-pyramid.obj", textured=True)
    vertices, indices = model["vertices"], model["indices"]
    assert len(indices) == 18, len(indices)
    for axis, expected in enumerate([(-1.5, 0), (0, .25), (0, 1)]):
        actual = (min(v[axis] for v in vertices), max(v[axis] for v in vertices))
        assert all(abs(actual[i] - expected[i]) < 1e-5 for i in (0, 1)), (axis, actual, expected)
    volume6 = 0
    for index in range(0, len(indices), 3):
        a, b, c = (vertices[indices[index + corner]] for corner in range(3))
        cross = (b[1]*c[2]-b[2]*c[1], b[2]*c[0]-b[0]*c[2], b[0]*c[1]-b[1]*c[0])
        volume6 += sum(a[j] * cross[j] for j in range(3))
    assert abs(volume6 / 6 - .125) < 1e-5, volume6 / 6
    assert {uv[1] for uv in model["uvs"]} == {.25, .75}
    assert any(uv == [.125, .75] for uv in model["uvs"])
    saved = bpy.data.images.load(str(output / "mat-smoke-atlas.png"), check_existing=False)
    assert tuple(saved.size) == (32, 32)
    # PNG preserves the current image buffer (not a stale pre-paint copy).
    for index in (0, 128, 1020, 4092):
        assert abs(saved.pixels[index] - pixels[index]) < .006
    bpy.data.images.remove(saved)

    before = {path.name: (digest(path), path.stat().st_mtime_ns) for path in output.iterdir() if path.is_file()}
    export_pack(bpy.context, content, output, objects=[obj])
    after = {path.name: (digest(path), path.stat().st_mtime_ns) for path in output.iterdir() if path.is_file()}
    assert before == after, "Unchanged re-export rewrote an artifact"
    mesh.vertices[4].co.z = 1.2
    mesh.update()
    bpy.context.view_layer.update()
    export_pack(bpy.context, content, output, objects=[obj])
    assert digest(output / "smoke-pyramid.obj") != before["smoke-pyramid.obj"][0]
    before_rejection = {path.name: digest(path) for path in output.iterdir() if path.is_file()}
    mesh.materials.append(material)
    try:
        export_pack(bpy.context, content, output, objects=[obj])
        raise AssertionError("Multiple material slots were accepted")
    except ExportError as error:
        assert "one material slot" in str(error), str(error)
    assert before_rejection == {path.name: digest(path) for path in output.iterdir() if path.is_file()}
    mesh.materials.pop(index=1)
    image.pack()
    blend_path = content / "fixture.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False)
    lazy_image = bpy.data.images.get("export-smoke-image")
    lazy_before = lazy_image.has_data
    export_pack(bpy.context, content, output, objects=[bpy.data.objects["Smoke pyramid"]])
    assert before_rejection == {path.name: digest(path) for path in output.iterdir() if path.is_file()}
    obj = bpy.data.objects['Smoke pyramid']
    mesh = obj.data
    for polygon in mesh.polygons:
        polygon.use_smooth = polygon.index != 0
    for edge in mesh.edges:
        edge.use_edge_sharp = all(index < 4 for index in edge.vertices)
    mesh.update()
    bpy.context.view_layer.update()
    export_pack(bpy.context, content, output, objects=[obj])
    lines = (output / 'smoke-pyramid.obj').read_text().splitlines()
    normals = [list(map(float, line.split()[1:])) for line in lines if line.startswith('vn ')]
    faces = [[tuple(int(part) for part in ref.split('/')) for ref in line.split()[1:]]
             for line in lines if line.startswith('f ')]
    assert normals and all(abs(sum(v*v for v in normal) - 1) < 1e-5 for normal in normals)
    assert all(len(ref) == 3 for face in faces for ref in face)
    corner_zero = [normals[ref[2]-1] for face in faces for ref in face if ref[0] == 1]
    # At vertex 0 the two equal pyramid side normals sum to (-1.2,-1.2,1).
    # Inverse-transpose scale(-2,3,.5), 90-degree Z rotation and game-axis
    # conversion give (.4,2,-.6), independent of the exporter implementation.
    expected = [.4, 2, -.6]
    length = math.sqrt(sum(v*v for v in expected))
    expected = [v / length for v in expected]
    assert any(all(abs(normal[j]-expected[j]) < 1e-5 for j in range(3)) for normal in corner_zero), (corner_zero, expected)
    assert any(all(abs(normal[j]-[0,-1,0][j]) < 1e-5 for j in range(3)) for normal in corner_zero)
    assert len({tuple(normal) for normal in corner_zero}) == 2, 'Base and smooth sides lost their hard boundary'
    untextured = _obj(bpy.context, obj, False).decode('ascii')
    assert all('//' in ref for line in untextured.splitlines() if line.startswith('f ') for ref in line.split()[1:])
    report = {"passed": True, "blender_version": bpy.app.version_string, "content_root": str(content),
              "pack": str(output / "pack.json"), "triangles": len(indices) // 3,
              "runtime_vertices": len(vertices), "reflected_volume_m3": volume6 / 6,
              "packed_image_loaded_before_export": lazy_before,
              "normal_directions": len(normals),
              "checks": ["add-on registration", "evaluated modifiers", "rotation and reflected scale",
                         "local origin and metre conversion", "outward triangle winding", "UV seams and V conversion",
                         "current texture pixels", "source image state preserved", "emission and backface flags",
                         "unchanged export cache", "changed mesh re-export", "failed export preserves files",
                         "packed image export after reopening blend", "flat models omit normals",
                         "smooth corner normals use inverse-transpose reflected nonuniform scale",
                         "hard boundaries split normals", "untextured smooth OBJ indices"]}
    (ROOT / "build/blender-export-smoke.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    run()
