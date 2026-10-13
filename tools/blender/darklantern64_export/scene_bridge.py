"""Blender UI and immutable staged payloads for the DarkLantern scene exchange."""
import hashlib
import json
import math
from pathlib import Path
import uuid

import bpy
from bpy.props import BoolProperty
from mathutils import Matrix, Vector

from . import _obj, _replace_bytes, TriangleExportError
from .core import identifier, require

AXIS = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def metadata(scene):
    require("dl64_scene_checkout" in scene, "Open a DarkLantern level checkout first")
    return json.loads(scene["dl64_scene_checkout"])


def transform(matrix, label, baseline=None):
    game = AXIS @ matrix @ AXIS.inverted()
    position, rotation, scale = game.decompose()
    require(all(math.isfinite(v) and v > 0 for v in scale), f"{label}: apply mirrored/zero scale to mesh geometry first")
    rebuilt = Matrix.LocRotScale(position, rotation, scale)
    require(max(abs(game[r][c]-rebuilt[r][c]) for r in range(4) for c in range(4)) < 1e-5,
            f"{label}: sheared transforms cannot be represented; apply the shear to geometry")
    result = dict(position=list(position), rotation=[math.degrees(x) for x in rotation.to_euler('XYZ')], scale=list(scale))
    # Preserve canonical values on unchanged imports despite float32 matrices.
    for key, values in result.items():
        if baseline and all(abs(a-b) < 1e-5 for a,b in zip(values, baseline[key])):
            result[key] = baseline[key]
        else:
            result[key] = [round(x, 7) for x in values]
    return result


def collect(context, data):
    require(abs(context.scene.unit_settings.scale_length - 1) < 1e-8, "Level scene uses metres; keep Unit Scale at 1")
    context.view_layer.update()
    baseline = data["baseline"]
    entities, meshes = {}, {}
    seen = set()
    # Blender's Object.children scans the scene. Imported source studies can
    # have tens of thousands of independent proxies; index parents once.
    collision_proxies, floor_proxies = {}, {}
    for candidate in context.scene.objects:
        if candidate.get('dl64_collision_proxy'):
            collision_proxies.setdefault(candidate.parent, []).append(candidate)
        if candidate.get('dl64_floor_proxy'):
            floor_proxies.setdefault(candidate.parent, []).append(candidate)
    for obj in context.scene.objects:
        if not obj.get("dl64_entity_id"):
            continue
        ident = identifier(obj["dl64_entity_id"], obj.name + " object ID")
        require(ident.casefold() not in seen, f"{ident}: duplicate ID; select the duplicate and use New instance ID")
        seen.add(ident.casefold())
        if obj.get('dl64_visual_owner'):
            require(obj.parent and obj.parent.get('dl64_entity_id')==obj['dl64_visual_owner'],
                    f'{ident}: linked visual parent is missing or changed; use Link selected collision to active visual to repair the relationship')
        ancestor = obj
        while ancestor is not None:
            require(not ancestor.animation_data and not ancestor.constraints,
                    f"{ident}: animated/constrained object or parent is not static architecture")
            ancestor = ancestor.parent
        base = baseline.get(ident, {})
        row = {"transform": transform(obj.matrix_world, ident, base.get("transform"))}
        if obj.type == "MESH":
            if obj.get('dl64_collision_authoring'):
                row['collision_authoring'] = json.loads(obj['dl64_collision_authoring'])
            model = obj.get("dl64_model_id")
            require(model in data["meshes"], f"{ident}: unknown model; this scene bridge edits existing models")
            material = obj.get("dl64_scene_material_id")
            require(material in data["materials"], f"{ident}: unknown material")
            require(len(obj.material_slots) == 1 and obj.material_slots[0].material and
                    obj.material_slots[0].material.get("dl64_material_id") == material,
                    f"{ident}: keep its assigned game material; material editing belongs to the asset workflow")
            payload = _obj(context, obj, data["meshes"][model]["textured"], local_space=True)
            require(model not in meshes or meshes[model] == payload,
                    f"{model}: instances contain different geometry/modifiers; edit the linked mesh consistently")
            meshes[model] = payload
            row.update(model=model, material=material)
        else:
            require(obj.type == "EMPTY", f"{ident}: architecture must be mesh or collision empty")
        proxies = collision_proxies.get(obj, [])
        require(len(proxies) <= 1, f"{ident}: only one box proxy per object is supported")
        if proxies:
            proxy = proxies[0]
            require(not proxy.constraints and not proxy.animation_data, f"{ident}: animated collision is unsupported")
            local = transform(obj.matrix_world.inverted() @ proxy.matrix_world, ident + " collision")
            require(all(abs(x) < 1e-4 for x in local["rotation"]), f"{ident}: rotate the parent, not the local collision box")
            row["collider"] = dict(shape="box", center=local["position"], half_size=local["scale"])
            if "collider" in base:
                for field in ("center", "half_size"):
                    if all(abs(a-b) < 1e-5 for a,b in zip(row["collider"][field], base["collider"][field])):
                        row["collider"][field] = base["collider"][field]
            require(abs(row["transform"]["rotation"][0]) < 1e-4 and abs(row["transform"]["rotation"][2]) < 1e-4,
                    f"{ident}: game box collision requires upright yaw")
        floors = floor_proxies.get(obj, [])
        require(len(floors) <= 1, f"{ident}: only one floor triangle per object is supported")
        require(not (proxies and floors), f"{ident}: floor triangle and opaque box cannot share an object")
        if floors:
            proxy = floors[0]
            require(proxy.type == 'MESH' and not proxy.constraints and not proxy.animation_data,
                    f"{ident}: floor must be a static triangle mesh")
            evaluated = proxy.evaluated_get(context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                require(len(mesh.vertices) == 3 and len(mesh.polygons) == 1 and len(mesh.polygons[0].vertices) == 3,
                        f"{ident}: floor proxy must contain exactly one triangle")
                local = AXIS @ obj.matrix_world.inverted() @ proxy.matrix_world
                vertices = [list(local @ vertex.co) for vertex in mesh.vertices]
                require(all(math.isfinite(v) for point in vertices for v in point), f"{ident}: non-finite floor vertex")
                old = base.get('floor', {}).get('vertices', [])
                vertices = [[old[i][axis] if i < len(old) and abs(value-old[i][axis]) < 1e-5
                             else round(value,7) for axis,value in enumerate(point)]
                            for i,point in enumerate(vertices)]
                row['floor'] = dict(vertices=vertices)
            finally:
                evaluated.to_mesh_clear()
        require("model" in row or "collider" in row or "floor" in row, f"{ident}: object has no mesh or collision")
        entities[ident] = row
    require(entities, "Scene has no tagged architecture")
    # Accidental unparenting must not silently remove collision from an object.
    for parent, proxies in list(collision_proxies.items()) + list(floor_proxies.items()):
        for obj in proxies:
            require(parent and parent.get("dl64_entity_id") in entities,
                    f"{obj.name}: collision proxy lost its architecture parent")
    return entities, meshes


def export_scene(context):
    data = metadata(context.scene)
    entities, payloads = collect(context, data)
    from .collision_tools import review_status
    reviews = []
    for obj in context.scene.objects:
        if obj.type == 'MESH' and obj.get('dl64_collision_authoring'):
            status = review_status(context.scene,obj)
            if status != 'Geometry and collision reviewed together':
                reviews.append(dict(id=obj['dl64_entity_id'],message=status))
    target = Path(data["exchange"]).resolve()
    require(target.is_relative_to(Path(data["project"]).resolve() / "build"), "Exchange path must stay in project build/")
    models = {}
    for ident, payload in payloads.items():
        spec = data["meshes"][ident]
        models[ident] = {key: spec[key] for key in ("uri", "source_sha256", "baseline_export_sha256")}
        models[ident]["export_sha256"] = digest(payload)
    # Immutable payload names ensure a reader of the previous manifest cannot
    # see half of a newer export. Publishing the manifest is the only commit.
    result = dict(version=1, scene_id=data["scene_id"], level=data["level"],
                  baseline=data["baseline"], entities=entities, meshes=models,
                  revision=data.get("revision", 0)+1,
                  generated_removals=json.loads(context.scene.get('dl64_generated_removals','[]')),
                  collision_review=reviews)
    for ident, payload in payloads.items():
        destination = target.parent / "meshes" / (digest(payload) + ".obj")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            _replace_bytes(destination, payload)
        else:
            require(destination.read_bytes() == payload, "Existing exported payload was modified")
    _replace_bytes(target, (json.dumps(result, indent=2)+"\n").encode())
    data["revision"] = result["revision"]
    context.scene["dl64_scene_checkout"] = json.dumps(data)
    context.scene['dl64_collision_warnings'] = json.dumps(reviews)
    return result


class DL64_OT_scene_export(bpy.types.Operator):
    bl_idname = "dl64.export_level_changes"
    bl_label = "Export level changes"
    bl_description = "Stage geometry and placement edits; then use Import Blender changes in the game editor"

    def execute(self, context):
        context.scene.pop("dl64_export_problem", None)
        try:
            result = export_scene(context)
        except (ValueError, OSError, RuntimeError) as error:
            if isinstance(error, TriangleExportError):
                context.scene["dl64_export_problem"] = json.dumps(error.problem)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        reviews = len(result.get('collision_review',[]))
        self.report({'WARNING'} if reviews else {'INFO'}, f"Staged {len(result['entities'])} objects; {reviews} need collision review. Import in DarkLantern, then save your .blend")
        return {'FINISHED'}


class DL64_OT_select_problem(bpy.types.Operator):
    bl_idname = "dl64.select_problem_face"
    bl_label = "Select problem face"
    bl_description = "Select the face from the last geometry error without changing its geometry"
    vertices_only: BoolProperty(name="Triangle vertices", default=False)

    def execute(self, context):
        import bmesh
        try:
            require("dl64_export_problem" in context.scene, "Export first to identify a problem face")
            problem = json.loads(context.scene["dl64_export_problem"])
            obj = context.scene.objects.get(problem["object"])
            require(obj is not None and obj.type == 'MESH', "Object was removed or renamed; export again")
            require(not obj.modifiers, "The error refers to evaluated modifier geometry; source-face selection is unavailable while modifiers are present")
            require(context.mode in ('OBJECT', 'EDIT_MESH'), "Leave the current mode before selecting the problem face")
            if context.mode == 'EDIT_MESH':
                bpy.ops.object.mode_set(mode='OBJECT')
            index = problem['face']
            require(index < len(obj.data.polygons) and list(obj.data.polygons[index].vertices) == problem['polygon_vertices'],
                    "Topology changed since the error; export again to locate the current problem")
            require(not obj.hide_select, "Enable selection for this object in the Outliner")
            bpy.ops.object.select_all(action='DESELECT')
            obj.hide_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode='EDIT')
            context.tool_settings.mesh_select_mode = (True, False, False) if self.vertices_only else (False, False, True)
            mesh = bmesh.from_edit_mesh(obj.data)
            mesh.faces.ensure_lookup_table()
            mesh.verts.ensure_lookup_table()
            for face in mesh.faces:
                face.select_set(False)
            for edge in mesh.edges:
                edge.select_set(False)
            for vertex in mesh.verts:
                vertex.select_set(False)
            if self.vertices_only:
                for vertex in problem['vertices']:
                    mesh.verts[vertex].select_set(True)
            else:
                mesh.faces[index].select_set(True)
                mesh.faces.active = mesh.faces[index]
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            if context.area and context.area.type == 'VIEW_3D':
                region = next(r for r in context.area.regions if r.type == 'WINDOW')
                with context.temp_override(region=region):
                    bpy.ops.view3d.view_selected(use_all_regions=False)
        except (ValueError, RuntimeError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DL64_OT_new_instance(bpy.types.Operator):
    bl_idname = "dl64.new_instance_id"
    bl_label = "New instance ID"
    bl_description = "Give selected duplicates new identities; linked mesh geometry remains shared"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .collision_tools import spec, put
        selected = [obj for obj in context.selected_objects if obj.get('dl64_entity_id')]
        try:
            require(selected, 'Select the duplicated object hierarchy')
            old = {obj['dl64_entity_id']:obj for obj in selected}
            require(len(old)==len(selected), 'Select only the duplicate hierarchy, not its originals')
            for obj in selected:
                require(not obj.get('dl64_stair_shell') and not obj.get('dl64_assembly_root'),
                        'Generated staircases cannot be duplicated with New instance ID yet')
                for ident in spec(obj).get('colliders',[]):
                    require(ident in old and old[ident].parent==obj,
                            'Select the duplicate visual and all its duplicated collision children together')
                if obj.get('dl64_visual_owner'):
                    require(obj.parent in selected,
                            'Select the duplicate collision and its duplicated visual parent together')
            mapping = {ident:'instance-'+uuid.uuid4().hex for ident in old}
            for ident,obj in old.items():
                obj['dl64_entity_id'] = mapping[ident]
            for obj in selected:
                if obj.get('dl64_visual_owner'):
                    obj['dl64_visual_owner'] = obj.parent['dl64_entity_id']
                if obj.get('dl64_collision_authoring'):
                    data = spec(obj)
                    data['colliders'] = [mapping[i] for i in data.get('colliders',[])]
                    put(obj,data)
        except ValueError as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class DL64_PT_scene(bpy.types.Panel):
    bl_label = "DarkLantern level"
    bl_idname = "DL64_PT_scene"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "DarkLantern"

    def draw(self, context):
        layout = self.layout
        if "dl64_export_problem" in context.scene:
            problem = json.loads(context.scene['dl64_export_problem'])
            box = layout.box()
            box.label(text=problem['object'], icon='ERROR')
            box.label(text=f"Face {problem['face']} / triangle {problem['triangle']}")
            box.label(text="Vertices: " + ', '.join(str(v) for v in problem['vertices']))
            box.label(text=f"Area: {problem['area_m2']:.6g} m² (indices start at 0)")
            box.operator(DL64_OT_select_problem.bl_idname, icon='FACESEL')
            box.operator(DL64_OT_select_problem.bl_idname, text='Select triangle vertices', icon='VERTEXSEL').vertices_only = True
        if "dl64_scene_checkout" not in context.scene:
            layout.label(text="Open a level checkout to begin.")
            return
        data = metadata(context.scene)
        layout.label(text=data["level"])
        if data.get('authoring_profile') == 'external-scale-study':
            layout.label(text='Source study: editor preview only')
        warnings = json.loads(context.scene.get('dl64_collision_warnings','[]'))
        if warnings:
            layout.label(text=f'Last export: {len(warnings)} collision reviews',icon='ERROR')
        layout.operator(DL64_OT_scene_export.bl_idname)
        layout.operator(DL64_OT_new_instance.bl_idname)
        layout.label(text="Import in DarkLantern to update.")
        layout.label(text="Gameplay stays in the level editor.")
        obj = context.active_object
        if obj and obj.get("dl64_entity_id"):
            layout.label(text="ID: " + obj["dl64_entity_id"])
            if obj.get("dl64_model_id"):
                layout.label(text="Shared: " + obj["dl64_model_id"])
        from . import collision_tools
        collision_tools.draw(layout,context)


CLASSES = (DL64_OT_scene_export, DL64_OT_select_problem, DL64_OT_new_instance, DL64_PT_scene)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    from .collision_tools import DL64_OT_collision
    bpy.utils.register_class(DL64_OT_collision)


def unregister():
    from .collision_tools import DL64_OT_collision
    bpy.utils.unregister_class(DL64_OT_collision)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
