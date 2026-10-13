"""Explicit ownership, simultaneous wire overlays and shared shape controls."""
import hashlib
import json
import math

import bpy
from bpy.props import StringProperty
from mathutils import Matrix, Vector

from .core import require
from .shapes import staircase, rectangular_prism


def spec(obj):
    return json.loads(obj.get('dl64_collision_authoring', '{}'))


def put(obj, data):
    obj['dl64_collision_authoring'] = json.dumps(data, sort_keys=True)


def objects(scene):
    return {o.get('dl64_entity_id'): o for o in scene.objects if o.get('dl64_entity_id')}


def visual(context):
    obj = context.active_object
    while obj:
        if obj.type == 'MESH' and obj.get('dl64_entity_id'):
            return obj
        obj = obj.parent
    raise ValueError('Select a visual object or one of its collision children')


def proxies(scene, obj):
    rows = objects(scene)
    owners = [obj] + [rows[i] for i in spec(obj).get('colliders', []) if i in rows]
    return [c for o in owners for c in o.children if c.get('dl64_collision_proxy')]


def fingerprint(obj):
    # Vertex edits, topology and modifier output affect collision review. UV and
    # material edits don't. An object transform moves its parented boxes too.
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        data = [[list(v.co) for v in mesh.vertices], [list(p.vertices) for p in mesh.polygons]]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()
    finally:
        evaluated.to_mesh_clear()


def box_signature(scene, obj):
    inverse = obj.matrix_world.inverted()
    return [[round(v,5) for row in inverse @ p.matrix_world for v in row] for p in proxies(scene,obj)]


def mark_reviewed(scene, obj):
    data = spec(obj)
    data['review_mesh'] = fingerprint(obj)
    data['review_boxes'] = box_signature(scene,obj)
    put(obj,data)


def review_status(scene, obj):
    data = spec(obj)
    if data.get('mode')=='stairs':
        if any(obj.get('dl64_stair_'+key) != value for key,value in data.get('parameters',{}).items() if key not in ('origin','direction')):
            return 'Parameters changed: apply staircase'
    if data.get('mode')=='box':
        if any(list(obj.get('dl64_prism_'+key,[])) != data.get('parameters',{}).get(key) for key in ('size','center')):
            return 'Parameters changed: apply prism'
    if not data.get('colliders') and not proxies(scene,obj):
        return 'No linked collision'
    if data.get('review_mesh') != fingerprint(obj):
        return 'Geometry changed: review collision'
    if data.get('review_boxes') != box_signature(scene,obj):
        return 'Collision changed: review fit'
    return 'Geometry and collision reviewed together'


def parent_preserving_world(child, parent, collection=None):
    world = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = Matrix.Identity(4)
    child.matrix_world = world
    if collection:
        if child.name not in collection.objects:
            collection.objects.link(child)
        for old in list(child.users_collection):
            if old != collection:
                old.objects.unlink(child)


def link_collision(scene, obj, owners):
    require(obj.type == 'MESH', 'Collision owner must be a visual mesh')
    collection = obj.users_collection[0]
    data = spec(obj)
    ids = set(data.get('colliders', []))
    for owner in owners:
        require(owner != obj and owner.type == 'EMPTY' and owner.get('dl64_entity_id'), 'Choose collision objects to link')
        require(any(c.get('dl64_collision_proxy') for c in owner.children), 'Object has no box proxy')
        ancestor = obj
        while ancestor:
            require(ancestor != owner, 'Parenting would create a cycle')
            ancestor = ancestor.parent
    for owner in owners:
        # Relinking explicitly removes the previous relationship.
        previous = owner.parent
        if previous and previous.type == 'MESH' and previous != obj:
            old = spec(previous)
            old['colliders'] = [i for i in old.get('colliders', []) if i != owner['dl64_entity_id']]
            put(previous,old)
        parent_preserving_world(owner,obj,collection)
        owner['dl64_visual_owner'] = obj['dl64_entity_id']
        owner.name = 'Collision | ' + owner['dl64_entity_id'].removeprefix('sponza-collision-')
        owner.empty_display_size = .04
        for proxy in owner.children:
            if proxy.get('dl64_collision_proxy'):
                parent_preserving_world(proxy,owner,collection)
                proxy.name = 'Box | ' + owner['dl64_entity_id'].removeprefix('sponza-collision-')
                proxy.color = (.05,1,.35,1)
                proxy.show_in_front = True
        ids.add(owner['dl64_entity_id'])
    data.update(version=1, mode=data.get('mode','manual'), colliders=sorted(ids))
    put(obj,data)
    bpy.context.view_layer.update()
    if len(ids)==1 and 'dl64_prism_size' not in obj:
        corners=[Vector(p) for p in obj.bound_box]
        low=Vector([min(v[i] for v in corners) for i in range(3)])
        high=Vector([max(v[i] for v in corners) for i in range(3)])
        obj['dl64_prism_size']=list(high-low)
        obj['dl64_prism_center']=list((high+low)*.5)
        obj.id_properties_ui('dl64_prism_size').update(min=.002,max=128)
        if data.get('mode')=='box':
            for key in ('size','center'):
                obj['dl64_prism_'+key]=data['parameters'][key]


def organize_sponza(scene):
    """A deterministic migration of known Sponza IDs; no proximity guesses."""
    rows = objects(scene)
    groups = {}
    for ident, owner in rows.items():
        if not ident.startswith('sponza-collision-'):
            continue
        # Persisted/manual relationships take precedence over the initial
        # Sponza naming migration when a saved level is checked out again.
        if owner.get('dl64_visual_owner'):
            continue
        tail = ident.removeprefix('sponza-collision-')
        target = 'sponza-'+tail
        for side in ('east','west'):
            if tail.startswith(side+'-step-') or tail == side+'-mid-landing':
                target = 'sponza-stairs-'+side
            elif tail.startswith(side+'-outer-wall-') or tail in (side+'-back-wall',side+'-divider',side+'-ceiling'):
                target = 'sponza-stair-shell-'+side
        if target in rows and rows[target].type == 'MESH':
            groups.setdefault(target,[]).append(owner)
    for ident, owners in groups.items():
        obj = rows[ident]
        link_collision(scene,obj,owners)
        if 'review_mesh' not in spec(obj):
            mark_reviewed(scene,obj)
    for side in ('east','west'):
        root,shell = rows.get('sponza-stairs-'+side),rows.get('sponza-stair-shell-'+side)
        if root and shell:
            parent_preserving_world(shell,root,root.users_collection[0])
            shell['dl64_assembly_root'] = root['dl64_entity_id']
            root['dl64_stair_shell'] = shell['dl64_entity_id']
            defaults = dict(steps=11,rise=5.3,tread=.3,width=1.0,landing=1.2,gap=.2,headroom=2.3)
            saved = spec(root).get('parameters',defaults)
            for key,value in saved.items():
                if key in defaults:
                    root['dl64_stair_'+key] = value
                    root.id_properties_ui('dl64_stair_'+key).update(min=1 if key=='steps' else .05,max=32 if key=='steps' else 64)
    bpy.context.view_layer.update()
    return sum(len(v) for v in groups.values())


def restore_relations(scene):
    rows = objects(scene)
    for obj in list(rows.values()):
        if obj.type == 'MESH' and spec(obj).get('colliders'):
            require(all(i in rows for i in spec(obj)['colliders']), f'{obj.name}: linked collider is missing')
            link_collision(scene,obj,[rows[i] for i in spec(obj)['colliders']])
    organize_sponza(scene)


def show_collision(scene, obj, enabled=True):
    related = set(proxies(scene,obj))
    if obj.get('dl64_stair_shell'):
        shell = objects(scene).get(obj['dl64_stair_shell'])
        if shell:
            related.update(proxies(scene,shell))
    for p in scene.objects:
        if p.get('dl64_collision_proxy'):
            p.hide_set(not (enabled and p in related))
            p.show_in_front = True
    return related


def fit_bounds(context, obj):
    require(context.mode == 'OBJECT', 'Return to Object Mode before fitting collision')
    require(spec(obj).get('mode') != 'stairs', 'Use Apply staircase for stairs; a single box would fill the stairwell')
    boxes = proxies(context.scene,obj)
    require(len(boxes)==1, 'Fit bounds requires exactly one linked box; link a box to this object first')
    from .scene_bridge import transform
    t = transform(obj.matrix_world,obj.name)
    require(abs(t['rotation'][0])<1e-4 and abs(t['rotation'][2])<1e-4, 'Box collision needs upright yaw')
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        require(len(mesh.vertices)>0, 'Mesh is empty')
        low = Vector([min(v.co[i] for v in mesh.vertices) for i in range(3)])
        high = Vector([max(v.co[i] for v in mesh.vertices) for i in range(3)])
    finally:
        evaluated.to_mesh_clear()
    half = (high-low)*.5
    require(all(v>=.001 for v in half), 'A flat surface has no box thickness; author a solid prism or a manual box')
    proxy = boxes[0]
    if proxy.parent != obj:
        proxy.parent.matrix_world = obj.matrix_world.copy()
    proxy.matrix_parent_inverse = Matrix.Identity(4)
    proxy.matrix_basis = Matrix.Translation((low+high)*.5) @ Matrix.Diagonal((*half,1))
    context.view_layer.update()
    data = spec(obj)
    data['mode'] = 'bounds'
    put(obj,data)
    mark_reviewed(context.scene,obj)
    show_collision(context.scene,obj)


def mesh_from_faces(obj, faces, mapping=None):
    """Replace shape geometry explicitly, with UVs and normals on face corners."""
    vertices, normals, uvs, triangles, lookup = [], [], [], [], {}
    def vertex(point):
        key = tuple(point)
        if key not in lookup:
            lookup[key] = len(vertices)
            v = Vector((point[0],-point[2],point[1]))
            vertices.append(tuple(mapping @ v if mapping else v))
        return lookup[key]
    for face in faces:
        points = face['points']
        axis = max(range(3),key=lambda i:abs(face['normal'][i]))
        axes = ((2,1),(0,2),(0,1))[axis]
        normal = Vector((face['normal'][0],-face['normal'][2],face['normal'][1]))
        if mapping:
            normal = (mapping.to_3x3().inverted().transposed() @ normal).normalized()
        if face.get('center'):
            center = tuple(sum(p[i] for p in points)/len(points) for i in range(3))
            pieces = [(center,points[i],points[(i+1)%len(points)]) for i in range(len(points))]
        else:
            pieces = [(points[0],points[i],points[i+1]) for i in range(1,len(points)-1)]
        for tri in pieces:
            triangles.append([vertex(p) for p in tri])
            uvs.extend([(p[axes[0]]*.5,p[axes[1]]*.5) for p in tri])
            normals.extend([tuple(normal)]*3)
    material = obj.material_slots[0].material
    old = obj.data
    mesh = bpy.data.meshes.new(old.name+' shape')
    mesh.from_pydata(vertices,[],triangles)
    mesh.materials.append(material)
    uv = mesh.uv_layers.new(name='UVMap')
    for loop in mesh.loops:
        uv.data[loop.index].uv = uvs[loop.index]
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.normals_split_custom_set(normals)
    obj.data = mesh
    obj.material_slots[0].material = material
    if old.users == 0:
        bpy.data.meshes.remove(old)


def apply_stairs(context, root):
    require(context.mode == 'OBJECT', 'Return to Object Mode before applying a staircase')
    rows = objects(context.scene)
    shell = rows.get(root.get('dl64_stair_shell'))
    require(shell is not None, 'Select a staircase with its linked shell')
    require(not root.modifiers and not shell.modifiers, 'Remove modifiers before rebuilding a generated staircase')
    require(root.data.users==1 and shell.data.users==1, 'Generated staircase meshes must have a single instance')
    from .scene_bridge import transform
    pose = transform(root.matrix_world,root.name)
    require(abs(pose['rotation'][0])<1e-4 and abs(pose['rotation'][2])<1e-4, 'Staircases must remain upright')
    side = 'east' if root['dl64_entity_id'].endswith('-east') else 'west'
    p = {key:root['dl64_stair_'+key] for key in ('steps','rise','tread','width','landing','gap','headroom')}
    p.update(direction=1 if side=='east' else -1,origin=[-2.25,-2.65,-1.1])
    shape = staircase(p)
    suffix = {'outer-wall-near':'outer-wall-2p8','outer-wall-far':'outer-wall-5p2'}
    planned = {'sponza-collision-'+side+'-'+suffix.get(name,name):(name,box) for name,box in shape['boxes'].items()}
    old_ids = set(spec(root).get('colliders',[])) | set(spec(shell).get('colliders',[]))
    for ident in planned:
        require(ident not in rows or ident in old_ids, f'{ident}: ID belongs to another object')
    for ident in old_ids:
        require(ident in rows and len(rows[ident].children)==1 and rows[ident].children[0].get('dl64_collision_proxy'),
                f'{ident}: generated collision was removed or has additional children; resolve it before rebuilding')
    # Validate before replacing any mesh; the operator is also undoable in Blender.
    mesh_from_faces(root,shape['treads'])
    mapping = shell.matrix_world.inverted() @ root.matrix_world
    mesh_from_faces(shell,shape['shell'],mapping)
    root_owners,shell_owners = [],[]
    for ident,(name,box) in planned.items():
        target = root if name.startswith('step-') or name=='mid-landing' else shell
        owner = rows.get(ident)
        if owner is None:
            owner = bpy.data.objects.new(ident,None)
            root.users_collection[0].objects.link(owner)
            owner['dl64_entity_id'] = ident
            proxy = bpy.data.objects.new(ident+' box',None)
            root.users_collection[0].objects.link(proxy)
            proxy.parent = owner
            proxy['dl64_collision_proxy'] = True
            proxy.empty_display_type = 'CUBE'
            proxy.empty_display_size = 1
        else:
            proxy = next(c for c in owner.children if c.get('dl64_collision_proxy'))
        c,h = box['center'],box['half_size']
        owner.matrix_world = root.matrix_world @ Matrix.Translation((c[0],-c[2],c[1]))
        proxy.matrix_parent_inverse = Matrix.Identity(4)
        proxy.matrix_basis = Matrix.Diagonal((h[0],h[2],h[1],1))
        (root_owners if target==root else shell_owners).append(owner)
    retired = {r['id']:r for r in json.loads(context.scene.get('dl64_generated_removals','[]'))}
    for ident in old_ids-set(planned):
        owner = rows.get(ident)
        if owner:
            for child in list(owner.children):
                bpy.data.objects.remove(child,do_unlink=True)
            bpy.data.objects.remove(owner,do_unlink=True)
        retired[ident] = dict(id=ident,owner=root['dl64_entity_id'])
    for ident in planned:
        retired.pop(ident,None)
    context.scene['dl64_generated_removals'] = json.dumps(list(retired.values()))
    put(root,dict(version=1,mode='stairs',colliders=[],parameters=p,shell=shell['dl64_entity_id']))
    put(shell,dict(version=1,mode='manual',colliders=[]))
    context.view_layer.update()
    link_collision(context.scene,root,root_owners)
    link_collision(context.scene,shell,shell_owners)
    mark_reviewed(context.scene,root)
    mark_reviewed(context.scene,shell)
    show_collision(context.scene,root)
    return shape['step_rise']


def apply_prism(context,obj):
    require(context.mode=='OBJECT','Return to Object Mode before applying a prism')
    require(len(proxies(context.scene,obj))==1,'A prism needs exactly one linked collision box')
    require(not obj.modifiers and obj.data.users==1,'Generated prisms need a single instance and no modifiers')
    from .scene_bridge import transform
    pose=transform(obj.matrix_world,obj.name)
    require(abs(pose['rotation'][0])<1e-4 and abs(pose['rotation'][2])<1e-4,'Collision prisms must be upright')
    size=list(obj['dl64_prism_size']);center=list(obj['dl64_prism_center'])
    faces=rectangular_prism([size[0],size[2],size[1]],[center[0],center[2],-center[1]])
    mesh_from_faces(obj,faces)
    context.view_layer.update()
    fit_bounds(context,obj)
    data=spec(obj);data.update(mode='box',parameters=dict(size=size,center=center));put(obj,data)
    mark_reviewed(context.scene,obj)


class DL64_OT_collision(bpy.types.Operator):
    bl_idname = 'dl64.collision_action'
    bl_label = 'Collision tools'
    bl_options = {'REGISTER','UNDO'}
    action: StringProperty()

    def execute(self,context):
        try:
            if self.action == 'organize':
                count = organize_sponza(context.scene)
                self.report({'INFO'},f'Linked {count} collision objects without changing their world positions')
                return {'FINISHED'}
            obj = visual(context)
            if self.action == 'show':
                show_collision(context.scene,obj)
            elif self.action == 'hide':
                show_collision(context.scene,obj,False)
            elif self.action in ('select_collision','select_visual'):
                require(context.mode=='OBJECT','Return to Object Mode to change object selection')
                targets = list(show_collision(context.scene,obj)) if self.action=='select_collision' else [obj]
                require(targets,'No collision is linked to this object')
                bpy.ops.object.select_all(action='DESELECT')
                for target in targets:
                    target.select_set(True)
                context.view_layer.objects.active = targets[0]
            elif self.action == 'link':
                selected = set(context.selected_objects)
                owners = {o.parent if o.get('dl64_collision_proxy') else o for o in selected if o!=obj}
                link_collision(context.scene,obj,list(owners))
                show_collision(context.scene,obj)
            elif self.action == 'fit':
                fit_bounds(context,obj)
            elif self.action == 'review':
                mark_reviewed(context.scene,obj)
            elif self.action == 'prism':
                apply_prism(context,obj)
            elif self.action == 'stairs':
                obj = objects(context.scene).get(obj.get('dl64_assembly_root'),obj)
                step = apply_stairs(context,obj)
                self.report({'INFO'},f'Rebuilt staircase and collision; each rise is {step:.3f} m. Review connections to the level before import')
            else:
                raise ValueError('Unknown collision action')
        except (ValueError,RuntimeError,KeyError) as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def button(layout,text,action):
    layout.operator(DL64_OT_collision.bl_idname,text=text).action=action


def draw(layout,context):
    try:
        obj = visual(context)
    except ValueError:
        return
    box = layout.box()
    box.label(text='Collision: '+obj.name)
    box.label(text=review_status(context.scene,obj))
    row = box.row(align=True)
    button(row,'Show related','show');button(row,'Hide','hide')
    row = box.row(align=True)
    button(row,'Select collision','select_collision');button(row,'Select visual','select_visual')
    button(box,'Fit collision to mesh bounds','fit')
    button(box,'Mark collision reviewed','review')
    button(box,'Link selected collision to active visual','link')
    if len(proxies(context.scene,obj))==1 and 'dl64_prism_size' in obj:
        box=layout.box()
        box.label(text='Rectangular prism (replaces mesh)')
        box.prop(obj,'["dl64_prism_size"]',text='Size XYZ')
        box.prop(obj,'["dl64_prism_center"]',text='Centre XYZ')
        button(box,'Apply prism and collision','prism')
    root = objects(context.scene).get(obj.get('dl64_assembly_root'),obj)
    if root.get('dl64_stair_shell'):
        box = layout.box()
        box.label(text='Staircase dimensions')
        for key,label in (('steps','Steps per flight'),('rise','Total rise'),('tread','Tread depth'),('width','Flight width'),('landing','Landing depth'),('gap','Centre gap'),('headroom','Upper headroom')):
            box.prop(root,'["dl64_stair_'+key+'"]',text=label)
        box.label(text=f"Step rise: {root['dl64_stair_rise']/(2*max(1,root['dl64_stair_steps'])):.3f} m / maximum 0.280 m")
        button(box,'Apply staircase and collision','stairs')
        box.label(text='Rebuild replaces generated surfaces.')
