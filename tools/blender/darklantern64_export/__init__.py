"""Export small, reusable Blender models into DarkLantern64's native cooker.

Meshes with smooth polygons include evaluated OBJ corner normals, preserving
authored sharp boundaries. Entirely flat meshes retain the compact legacy path.
"""

bl_info = {
    "name": "DarkLantern64 Asset Pack",
    "author": "DarkLantern64 contributors",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Export > DarkLantern64 Asset Pack",
    "description": "Export selected models, UVs and simple materials for the N64 pipeline",
    "category": "Import-Export",
}

import json
import math
import os
from pathlib import Path
import tempfile
import uuid

import bpy
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from .core import ExportError, bounded, identifier, relative_uri, require, texture_size, validate_publication


def _socket(node, *names):
    return next((node.inputs.get(name) for name in names if node.inputs.get(name) is not None), None)


def _material(material, root, output):
    ident = identifier(material.get("dl64_material_id"), f"Material {material.name}.dl64_material_id")
    result = {"id": ident, "color": [1.0, 1.0, 1.0, 1.0],
              "double_sided": not material.use_backface_culling}
    image = None
    if not material.use_nodes:
        result["color"] = bounded(material.diffuse_color, 4, ident + " color")
    else:
        tree = material.node_tree
        outputs = [node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output]
        require(len(outputs) == 1, f"{ident}: expected one active Material Output")
        output_node = outputs[0]
        require(not output_node.inputs["Volume"].is_linked and not output_node.inputs["Displacement"].is_linked,
                f"{ident}: volumes/displacement must be baked into geometry or Base Color")
        links = output_node.inputs["Surface"].links
        require(len(links) == 1 and links[0].from_node.type == "BSDF_PRINCIPLED",
                f"{ident}: Surface must connect directly to one Principled BSDF")
        shader = links[0].from_node
        for socket in shader.inputs:
            require(not socket.is_linked or socket.name == "Base Color",
                    f"{ident}: linked {socket.name} is unsupported; bake the appearance into Base Color")
        base = shader.inputs["Base Color"]
        if base.is_linked:
            node = base.links[0].from_node
            require(node.type == "TEX_IMAGE" and base.links[0].from_socket.name == "Color",
                    f"{ident}: Base Color must use a direct Image Texture Color connection")
            require(node.projection == "FLAT" and node.extension == "REPEAT",
                    f"{ident}: Image Texture must use Flat projection and Repeat extension")
            vector = node.inputs["Vector"]
            if vector.is_linked:
                source = vector.links[0]
                require((source.from_node.type == "TEX_COORD" and source.from_socket.name == "UV") or
                        source.from_node.type == "UVMAP",
                        f"{ident}: Image Texture coordinates must use UVs; bake other mappings")
                if source.from_node.type == "UVMAP":
                    result_uv = source.from_node.uv_map
                    require(not result_uv, f"{ident}: use the active UV map, not a named UV Map override")
            image = node.image
            require(image is not None and image.source in ("FILE", "GENERATED") and image.type != "MULTILAYER",
                    f"{ident}: expected a still, single-image texture; bake tiled/movie/render textures")
            # Blender lazily loads packed/file textures when the pixel buffer
            # is first requested. has_data alone is false after reopening an
            # otherwise valid .blend, so force that read before validating it.
            pixels = list(image.pixels[:])
            require(image.has_data, f"{ident}: image has no loaded pixels; load or pack its source image")
            width, height = map(int, image.size)
            texture_size(width, height)
            require(len(pixels) == width * height * 4 and all(math.isfinite(p) and 0 <= p <= 1 for p in pixels),
                    f"{ident}: image must contain RGBA pixels in 0..1; tone-map HDR sources before export")
            require(all(a >= .99999 for a in pixels[3::4]),
                    f"{ident}: transparent textures are not supported by this opaque model exporter")
            result["texture"] = {"uri": relative_uri(output / (ident + ".png"), root),
                                 "width": width, "height": height, "format": "RGBA16"}
        else:
            result["color"] = bounded(base.default_value, 4, ident + " Base Color")
        require(abs(shader.inputs["Alpha"].default_value - 1) < .00001,
                f"{ident}: material Alpha must be 1; this export path is opaque")
        emission = _socket(shader, "Emission Color", "Emission")
        strength = _socket(shader, "Emission Strength")
        energy = strength.default_value if strength is not None else 1.0
        emissive = [float(channel) * energy for channel in emission.default_value[:3]] if emission else [0, 0, 0]
        bounded(emissive, 3, ident + " emission × strength (runtime range)")
        if any(emissive):
            result["emissive"] = emissive
        for name in ("Transmission Weight", "Transmission", "Subsurface Weight", "Subsurface",
                     "Coat Weight", "Clearcoat", "Sheen Weight", "Sheen"):
            socket = _socket(shader, name)
            require(socket is None or abs(socket.default_value) < .00001,
                    f"{ident}: {name} cannot be represented; bake it into Base Color")
    require(abs(result["color"][3] - 1) < .00001, f"{ident}: color alpha must be 1")
    return result, image


def _obj(context, obj, textured):
    require(obj.mode == "OBJECT", f"{obj.name}: leave Edit Mode before exporting")
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=context.evaluated_depsgraph_get())
    try:
        require(mesh is not None and len(mesh.vertices) >= 3, f"{obj.name}: no evaluated mesh")
        require(len(mesh.materials) == 1 and mesh.materials[0] is not None,
                f"{obj.name}: use exactly one material slot; combine parts with a UV atlas")
        mesh.calc_loop_triangles()
        require(1 <= len(mesh.loop_triangles) <= 4096, f"{obj.name}: expected 1-4096 triangles")
        uv_layer = mesh.uv_layers.active
        require(not textured or uv_layer is not None, f"{obj.name}: textured models require an active UV map")
        # Parent rotation/scale and modifiers are baked. Remove only world
        # translation, so every model retains its own authored origin/pivot.
        linear = evaluated.matrix_world.to_3x3()
        determinant = linear.determinant()
        require(math.isfinite(determinant) and abs(determinant) > 1e-12,
                f"{obj.name}: zero or invalid object scale")
        export_normals = any(polygon.use_smooth for polygon in mesh.polygons)
        normal_matrix = linear.inverted().transposed() if export_normals else None
        corner_normals = mesh.corner_normals if export_normals else None
        require(not export_normals or len(corner_normals) == len(mesh.loops),
                f"{obj.name}: evaluated mesh is missing corner normals")
        metres = float(context.scene.unit_settings.scale_length)
        require(math.isfinite(metres) and metres > 0, "Scene unit scale must be positive")
        vertices = []
        for vertex in mesh.vertices:
            x, y, z = linear @ vertex.co
            vertices.append(bounded((x * metres, z * metres, -y * metres), 3,
                                    obj.name + " vertex", -1024, 1024))
        require(len(vertices) <= 4096, f"{obj.name}: mesh exceeds 4096 source vertices")
        uvs, uv_indices, normals, normal_indices, faces, seam_vertices = [], {}, [], {}, [], set()
        for triangle in mesh.loop_triangles:
            require(triangle.material_index == 0, f"{obj.name}: found a second material on an evaluated face")
            loops = list(triangle.loops)
            if determinant < 0:
                loops.reverse()
            face = []
            for loop_index in loops:
                vertex_index = mesh.loops[loop_index].vertex_index
                uv_index, normal_index = None, None
                if textured:
                    uv = tuple(bounded(uv_layer.data[loop_index].uv, 2, obj.name + " UV", -1024, 1024))
                    if uv not in uv_indices:
                        uv_indices[uv] = len(uvs) + 1
                        uvs.append(uv)
                    uv_index = uv_indices[uv]
                if export_normals:
                    normal = normal_matrix @ corner_normals[loop_index].vector
                    require(all(math.isfinite(v) for v in normal) and normal.length_squared > 1e-12,
                            f"{obj.name}: invalid evaluated corner normal")
                    normal.normalize()
                    # Axis conversion is a proper rotation. Reflected object
                    # scales reverse face order above; inverse-transpose alone
                    # gives their outward normal, without another sign flip.
                    key = tuple(0.0 if abs(v) < 1e-8 else float(format(v, '.9g'))
                                for v in (normal.x, normal.z, -normal.y))
                    if key not in normal_indices:
                        normal_indices[key] = len(normals) + 1
                        normals.append(key)
                    normal_index = normal_indices[key]
                if export_normals:
                    face.append(f"{vertex_index + 1}/{uv_index or ''}/{normal_index}")
                elif textured:
                    face.append(f"{vertex_index + 1}/{uv_index}")
                else:
                    face.append(str(vertex_index + 1))
                seam_vertices.add((vertex_index, uv_index, normal_index))
            points = [vertices[mesh.loops[index].vertex_index] for index in loops]
            a, b = [[points[i][j] - points[0][j] for j in range(3)] for i in (1, 2)]
            cross = (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
            require(sum(c*c for c in cross) > 1e-12,
                    f"{obj.name}: degenerate or too-small triangle; remove it before export")
            faces.append("f " + " ".join(face))
        require(len(seam_vertices) <= 4096,
                f"{obj.name}: UV/normal seams expand the model past 4096 runtime vertices")
        lines = ["# DarkLantern64: metres, +Y up, local origin; Blender (x,z,-y).",
                 "# Evaluated geometry; scale/rotation baked; triangles preserve outward winding."]
        lines += ["v " + " ".join(format(v, ".9g") for v in point) for point in vertices]
        lines += ["vt " + " ".join(format(v, ".9g") for v in uv) for uv in uvs]
        lines += ["vn " + " ".join(format(v, ".9g") for v in normal) for normal in normals]
        return ("\n".join(lines + faces) + "\n").encode("ascii")
    finally:
        evaluated.to_mesh_clear()


def _replace_bytes(destination, payload):
    # Python 3.13 TemporaryDirectory uses a private Windows DACL. Moving a
    # staged file from it would retain that ACL and hide exports from the
    # sandboxed editor/cooker. Create the final temporary file directly in the
    # public output directory so it inherits the content directory's ACL.
    temporary = destination.with_name(".dl64-publish-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_pack(context, content_root, output_dir, objects=None):
    """Export selected meshes; return the version-1 manifest written to pack.json.

    Paths may be str or Path and must be absolute (the UI resolves // paths).
    dl64_asset_id and dl64_material_id custom properties supply stable IDs.
    No files are published until every object, material and image validates.
    """
    require(Path(content_root).is_absolute() and Path(output_dir).is_absolute(), "Export API paths must be absolute")
    root, output = Path(content_root).resolve(), Path(output_dir).resolve()
    require(root.is_dir(), "Content root must be an existing directory")
    relative_uri(output / "pack.json", root)
    chosen = list(objects) if objects is not None else [obj for obj in context.selected_objects if obj.type == "MESH"]
    require(chosen and all(obj.type == "MESH" for obj in chosen), "Select at least one mesh object")
    require(len(chosen) <= 64, "An asset pack supports at most 64 models")
    manifest = {"version": 1, "assets": [], "materials": [], "prefabs": []}
    payloads, images, materials = {}, {}, {}
    for obj in sorted(chosen, key=lambda item: str(item.get("dl64_asset_id", item.name))):
        ident = identifier(obj.get("dl64_asset_id"), f"Object {obj.name}.dl64_asset_id")
        asset_id = identifier("mesh-" + ident, "Prefixed asset ID")
        require(len(obj.material_slots) == 1 and obj.material_slots[0].material is not None,
                f"{obj.name}: use exactly one material slot; join parts and use a UV atlas")
        material = obj.material_slots[0].material
        material_spec, image = _material(material, root, output)
        material_id = material_spec["id"]
        if material_id in materials:
            require(materials[material_id] == material,
                    f"Material ID {material_id} occurs on different Blender materials")
        else:
            materials[material_id] = material
            manifest["materials"].append(material_spec)
            if image:
                images[Path(material_spec["texture"]["uri"]).name] = image
        filename = ident + ".obj"
        require(filename.casefold() not in {key.casefold() for key in payloads}, f"Duplicate asset ID: {ident}")
        payloads[filename] = _obj(context, obj, image is not None)
        manifest["assets"].append({"id": asset_id, "uri": relative_uri(output / filename, root)})
        manifest["prefabs"].append({"id": ident, "model": asset_id, "material": material_id, "scale": [1, 1, 1]})
    manifest["materials"].sort(key=lambda item: item["id"])
    validate_publication(manifest, root, output)
    payloads["pack.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dl64-export-", dir=output) as temporary:
        stage = Path(temporary)
        for filename, image in images.items():
            # Saving the source image would clear its dirty flag and could lose
            # the user's unsaved paint on closing Blender. Image.copy() alone
            # may reload its old file buffer, so explicitly copy current pixels.
            exported_image = image.copy()
            try:
                exported_image.pixels.foreach_set(image.pixels[:])
                exported_image.update()
                exported_image.filepath_raw = str(stage / filename)
                exported_image.file_format = "PNG"
                exported_image.save()
                payloads[filename] = (stage / filename).read_bytes()
            finally:
                bpy.data.images.remove(exported_image)
        order = sorted(name for name in payloads if name != "pack.json") + ["pack.json"]
        # Individual file replacements are atomic; restore any earlier changes
        # if a later replacement fails. The manifest is always published last.
        backups, published = {}, []
        try:
            for filename in order:
                destination = output / filename
                backups[filename] = destination.read_bytes() if destination.exists() else None
                if backups[filename] == payloads[filename]:
                    continue
                _replace_bytes(destination, payloads[filename])
                published.append(filename)
        except OSError:
            for filename in reversed(published):
                if backups[filename] is None:
                    (output / filename).unlink()
                else:
                    _replace_bytes(output / filename, backups[filename])
            raise
    return manifest


class DL64_OT_export_pack(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.darklantern64_pack"
    bl_label = "Export DarkLantern64 Asset Pack"
    bl_options = {"PRESET"}
    filename_ext = ".json"

    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    content_root: StringProperty(name="Content Root", subtype="DIR_PATH", default="//../content/")

    def execute(self, context):
        try:
            path = Path(bpy.path.abspath(self.filepath))
            require(path.name == "pack.json", "Name the exported manifest pack.json")
            manifest = export_pack(context, bpy.path.abspath(self.content_root), path.parent)
        except (ExportError, OSError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(manifest['assets'])} models; material roughness/metallic are authoring-only")
        return {"FINISHED"}


def _menu(self, context):
    self.layout.operator(DL64_OT_export_pack.bl_idname, text="DarkLantern64 Asset Pack (.json)")


def register():
    bpy.utils.register_class(DL64_OT_export_pack)
    bpy.types.TOPBAR_MT_file_export.append(_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(_menu)
    bpy.utils.unregister_class(DL64_OT_export_pack)


if __name__ == "__main__":
    register()
