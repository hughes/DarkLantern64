"""Create/export the editable articulated guard animation study in Blender.

Run through Blender's Python API. Existing scenes are preserved. Character
geometry is authored in game axes then converted once to Blender's Z-up axes.
The exporter samples saved Actions, not the procedural authoring functions.
"""
from pathlib import Path
import json
import math
import os

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
TO_BLENDER = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
TO_GAME = TO_BLENDER.inverted()
PALETTE = [(0.19,.25,.19),(.28,.34,.24),(.105,.15,.13),(.39,.45,.46),
           (.22,.28,.31),(.57,.60,.57),(.22,.13,.075),(.34,.23,.12),
           (.62,.43,.29),(.77,.59,.40),(.035,.045,.042),(.68,.51,.19),
           (.83,.77,.58),(.33,.075,.055),(.14,.18,.18),(.46,.34,.19)]


def _json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8', newline='\n')
    os.replace(temporary, path)


class MeshBuilder:
    def __init__(self):
        self.vertices, self.faces, self.joints, self.tiles, self.smooth = [], [], [], [], []

    def part(self, vertices, faces, bone, tile, smooth=False):
        offset = len(self.vertices)
        self.vertices.extend(vertices)
        self.joints.extend(bone if isinstance(bone, list) else [bone] * len(vertices))
        self.faces.extend([tuple(offset + i for i in face) for face in faces])
        self.tiles.extend([tile] * len(faces))
        self.smooth.extend([smooth] * len(faces))

    def box(self, center, half, bone, tile):
        x,y,z = center; a,b,c = half
        vertices = [(x+sx*a,y+sy*b,z+sz*c) for sx,sy,sz in
                    [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                     (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]]
        self.part(vertices, [(0,3,2,1),(4,5,6,7),(0,1,5,4),
                             (3,7,6,2),(0,4,7,3),(1,2,6,5)], bone, tile)

    def rings(self, rings, bone, tile, sides=6, smooth=True, caps=(True,True)):
        # Ring entries: center XYZ and radii X/Z; side winding points outward.
        vertices = [(x + rx*math.cos(2*math.pi*i/sides), y,
                     z + rz*math.sin(2*math.pi*i/sides))
                    for x,y,z,rx,rz in rings for i in range(sides)]
        faces = []
        for ring in range(len(rings)-1):
            for i in range(sides):
                j = (i+1)%sides; a = ring*sides; b = a+sides
                faces.append((a+i,b+i,b+j,a+j))
        self.part(vertices, faces, bone, tile, smooth)
        if caps[0]: self.part(vertices[:sides], [tuple(range(sides))], bone, tile)
        if caps[1]: self.part(vertices[-sides:], [tuple(reversed(range(sides)))], bone, tile)

    def bridge(self, lower, upper, lower_bone, upper_bone, tile, sides=6):
        vertices = [(x+rx*math.cos(2*math.pi*i/sides), y, z+rz*math.sin(2*math.pi*i/sides))
                    for x,y,z,rx,rz in (lower,upper) for i in range(sides)]
        faces = [(i,sides+i,sides+(i+1)%sides,(i+1)%sides) for i in range(sides)]
        self.part(vertices, faces, [lower_bone]*sides+[upper_bone]*sides, tile, True)

    def object(self, collection, material, bones):
        mesh = bpy.data.meshes.new('Guard | articulated bind mesh')
        mesh.from_pydata([TO_BLENDER @ Vector(v) for v in self.vertices], [], self.faces)
        mesh.materials.append(material)
        uv = mesh.uv_layers.new(name='Atlas')
        for p, tile, smooth in zip(mesh.polygons, self.tiles, self.smooth):
            p.use_smooth = smooth
            # Every polygon occupies its palette tile; no transparent faces.
            for j, loop in enumerate(p.loop_indices):
                angle = 2*math.pi*j/len(p.loop_indices)
                u,v = .5+.30*math.cos(angle), .5+.30*math.sin(angle)
                uv.data[loop].uv = ((tile%4+u)/4, (tile//4+v)/4)
        mesh.update()
        obj = bpy.data.objects.new('GUARD | single-weight character', mesh)
        collection.objects.link(obj)
        for name in bones:
            group = obj.vertex_groups.new(name=name)
            selected = [i for i, joint in enumerate(self.joints) if joint == name]
            if selected: group.add(selected, 1.0, 'REPLACE')
        obj['dl64_character_id'] = 'basic-guard'
        return obj


def _atlas(output):
    image = bpy.data.images.new('Guard | 32px field uniform atlas', width=32, height=32, alpha=True)
    pixels = []
    for y in range(32):
        for x in range(32):
            color = PALETTE[(y//8)*4+x//8]
            noise = (((x*13+y*7+x*y*3)%11)-5)*.007
            edge = -.025 if x%8 in (0,7) or y%8 in (0,7) else 0
            pixels.extend([max(0,min(1,c+noise+edge)) for c in color]+[1])
    image.pixels.foreach_set(pixels)
    image.filepath_raw = str(output/'guard-atlas.png'); image.file_format = 'PNG'; image.save(); image.pack()
    material = bpy.data.materials.new('Guard | worn olive, leather and steel')
    material.use_nodes = True
    shader = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    shader.inputs['Roughness'].default_value = .82
    texture = material.node_tree.nodes.new('ShaderNodeTexImage'); texture.image = image
    texture.interpolation = 'Closest'
    material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
    material['dl64_material_id'] = 'mat-guard-atlas'
    return material


def _armature(collection):
    definitions = [('root',None,(0,0,0),(0,.2,0)),
        ('pelvis','root',(0,.88,0),(0,1.03,0)),('spine','pelvis',(0,1.03,0),(0,1.2,0)),
        ('chest','spine',(0,1.2,0),(0,1.44,0)),('neck','chest',(0,1.44,0),(0,1.54,0)),
        ('head','neck',(0,1.54,0),(0,1.82,0))]
    for suffix, sign in [('l',1),('r',-1)]:
        definitions += [(f'clavicle_{suffix}','chest',(0,1.4,0),(sign*.25,1.4,0)),
            (f'upper_arm_{suffix}',f'clavicle_{suffix}',(sign*.25,1.4,0),(sign*.36,1.12,0)),
            (f'forearm_{suffix}',f'upper_arm_{suffix}',(sign*.36,1.12,0),(sign*.39,.90,.015)),
            (f'hand_{suffix}',f'forearm_{suffix}',(sign*.39,.90,.015),(sign*.39,.79,.02))]
    for suffix, sign in [('l',1),('r',-1)]:
        definitions += [(f'thigh_{suffix}','pelvis',(sign*.105,.88,0),(sign*.115,.50,.018)),
            (f'shin_{suffix}',f'thigh_{suffix}',(sign*.115,.50,.018),(sign*.115,.13,0)),
            (f'foot_{suffix}',f'shin_{suffix}',(sign*.115,.13,0),(sign*.115,.065,.19))]
    data = bpy.data.armatures.new('Guard | humanoid-v1 | 20 bones')
    rig = bpy.data.objects.new('RIG | Guard humanoid-v1', data); collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig; rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for name,parent,head,tail in definitions:
        bone = data.edit_bones.new(name); bone.head = TO_BLENDER @ Vector(head); bone.tail = TO_BLENDER @ Vector(tail)
        if parent: bone.parent = data.edit_bones[parent]
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.show_in_front = True; data.display_type = 'STICK'; rig['dl64_skeleton_id'] = 'humanoid-v1'
    return rig, [d[0] for d in definitions]


def _geometry(collection, material, bones):
    b = MeshBuilder()
    b.rings([(0,.84,0,.20,.13),(0,1.01,0,.18,.13)],'pelvis',2,6)
    b.rings([(0,.99,0,.175,.125),(0,1.20,0,.21,.15)],'spine',0,6)
    b.rings([(0,1.18,0,.21,.15),(0,1.40,0,.25,.14),(0,1.45,0,.16,.12)],'chest',1,6)
    b.rings([(0,.995,0,.185,.137),(0,1.06,0,.193,.141)],'spine',6,6,False)
    b.box((0,1.031,.148),(.040,.029,.012),'spine',11)
    b.rings([(0,1.44,0,.060,.06),(0,1.57,0,.061,.06)],'neck',8,6)
    b.rings([(0,1.535,.02,.073,.078),(0,1.66,.017,.102,.098),(0,1.755,.00,.09,.08)],'head',9,6)
    b.rings([(0,1.70,0,.12,.13),(0,1.80,-.005,.104,.10),(0,1.845,-.008,.02,.026)],'head',4,8)
    b.rings([(0,1.694,0,.148,.16),(0,1.717,0,.145,.157)],'head',3,8,False)
    # A nose and two tiny eyes make forward direction immediately legible.
    b.part([(-.018,1.61,.103),(.018,1.61,.103),(0,1.64,.136),(0,1.67,.103)],
           [(0,1,2),(1,3,2),(3,0,2)],'head',8)
    for x in (-.046,.046):
        b.part([(x-.018,1.655,.107),(x+.018,1.655,.107),(x+.018,1.670,.107),(x-.018,1.670,.107)],[(0,1,2,3)],'head',10)
    for suffix, sign in [('l',1),('r',-1)]:
        b.rings([(sign*.275,1.305,0,.096,.105),(sign*.25,1.415,0,.106,.108)],f'upper_arm_{suffix}',4,6)
        elbow_top=(sign*.341,1.17,0,.074,.078)
        elbow_bottom=(sign*.367,1.07,.004,.071,.075)
        b.rings([elbow_top,(sign*.285,1.32,0,.08,.09)],f'upper_arm_{suffix}',0,6,caps=(False,True))
        b.rings([(sign*.39,.91,.015,.065,.071),elbow_bottom],f'forearm_{suffix}',6,6,caps=(True,False))
        b.bridge(elbow_bottom,elbow_top,f'forearm_{suffix}',f'upper_arm_{suffix}',0)
        b.box((sign*.39,.847,.028),(.060,.068,.065),f'hand_{suffix}',7)
        knee_top=(sign*.115,.56,.018,.080,.085)
        knee_bottom=(sign*.115,.44,.014,.077,.082)
        b.rings([knee_top,(sign*.105,.89,0,.091,.10)],f'thigh_{suffix}',2,6,caps=(False,True))
        b.rings([(sign*.115,.14,.0,.071,.075),knee_bottom],f'shin_{suffix}',6,6,caps=(True,False))
        b.bridge(knee_bottom,knee_top,f'shin_{suffix}',f'thigh_{suffix}',2)
        b.box((sign*.115,.069,.074),(.079,.065,.142),f'foot_{suffix}',6)
    # One asymmetrical badge and red armband aid parity checks between targets.
    b.box((-.125,1.335,.131),(.027,.043,.018),'chest',11)
    b.rings([(-.326,1.188,0,.079,.083),(-.309,1.244,0,.082,.086)],'upper_arm_r',13,6,False)
    return b.object(collection, material, bones)


def upgrade_connected_joints(output=None):
    """Replace only the generated study mesh, retaining its original as backup."""
    scene=bpy.context.scene
    old=next(o for o in scene.objects if o.type=='MESH' and o.get('dl64_character_id')=='basic-guard')
    rig=old.find_armature();collection=old.users_collection[0]
    new=_geometry(collection,old.data.materials[0],[b.name for b in rig.data.bones])
    new.name='GUARD | connected elbow and knee mesh'
    new.parent=rig;modifier=new.modifiers.new('Armature | one influence, connected joints','ARMATURE');modifier.object=rig
    del old['dl64_character_id']
    old.name='BASELINE | detached parts | excluded from export';old.hide_render=True;old.hide_set(True)
    new['dl64_connected_joints']=True
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath,compress=True)
    return export(scene,output)


def _pose_rotation(rig, name, x=0, y=0, z=0):
    bone = rig.pose.bones[name]
    model_rotation = Quaternion(Vector((0,1,0)), y) @ Quaternion(Vector((1,0,0)), x) @ Quaternion(Vector((0,0,1)), z)
    q = rig.data.bones[name].matrix_local.to_quaternion()
    conversion = TO_BLENDER.to_quaternion()
    bone.rotation_quaternion = q.inverted() @ conversion @ model_rotation @ conversion.inverted() @ q


def _actions(scene, rig):
    specs = [('idle',2.0,True,0),('walk',1.2,True,1.0),('run',.8,True,1.4),
             ('notice',1.4,False,0),('turn',1.6,False,0)]
    rig.animation_data_create()
    for name,duration,loop,stride in specs:
        action = bpy.data.actions.new('Guard | '+name); action.use_fake_user = True
        action['dl64_clip_id'] = name; action['dl64_loop'] = loop; action['dl64_stride_length'] = stride
        action['dl64_frame_start'] = 1; action['dl64_frame_end'] = round(duration*30)+1
        rig.animation_data.action = action
        for frame in range(1, round(duration*30)+2):
            t = (frame-1)/(duration*30); scene.frame_set(frame)
            for bone in rig.pose.bones:
                bone.rotation_mode = 'QUATERNION'; bone.rotation_quaternion = (1,0,0,0); bone.location = (0,0,0)
            if name in ('walk','run'):
                run = name == 'run'; phase = 2*math.pi*t
                lower = .078 if not run else .10
                rig.pose.bones['pelvis'].location = (0,-lower+.008*math.cos(phase*2),0)
                _pose_rotation(rig,'chest',x=-.06 if not run else -.16,y=.05*math.sin(phase))
                for suffix,offset in [('l',0),('r',.5)]:
                    p = (t+offset)%1; half_stride = stride*.25
                    if p < .5: z = half_stride - stride*p; lift = 0
                    else:
                        a = (p-.5)*2; z = -half_stride+2*half_stride*(a*a*(3-2*a)); lift = (.12 if not run else .19)*math.sin(math.pi*a)
                    d = .75-lower+.008*math.cos(phase*2)-lift; a,b = .380557,.370437
                    knee = math.acos(max(-1,min(1,(d*d+z*z-a*a-b*b)/(2*a*b))))
                    hip = math.atan2(-z,d)-math.atan2(b*math.sin(knee),a+b*math.cos(knee))
                    rest_hip = math.atan2(-.018,.38); rest_shin = math.atan2(.018,.37)
                    hip_delta = hip-rest_hip; knee_delta = knee-(rest_shin-rest_hip)
                    _pose_rotation(rig,f'thigh_{suffix}',x=hip_delta)
                    _pose_rotation(rig,f'shin_{suffix}',x=knee_delta)
                    _pose_rotation(rig,f'foot_{suffix}',x=-hip_delta-knee_delta)
                    swing = math.sin(2*math.pi*(t+offset))
                    _pose_rotation(rig,f'upper_arm_{suffix}',x=(.32 if not run else .58)*swing)
                    _pose_rotation(rig,f'forearm_{suffix}',x=-.20 if not run else -.75)
                _pose_rotation(rig,'head',x=.03 if not run else .08)
            elif name == 'idle':
                _pose_rotation(rig,'chest',x=.018*math.sin(2*math.pi*t))
                _pose_rotation(rig,'head',y=.045*math.sin(2*math.pi*t))
                _pose_rotation(rig,'forearm_l',x=-.12); _pose_rotation(rig,'forearm_r',x=-.12)
            elif name == 'notice':
                envelope = math.sin(math.pi*t)**2
                _pose_rotation(rig,'head',x=-.14*envelope,y=-.5*math.sin(2*math.pi*t)*envelope)
                _pose_rotation(rig,'chest',x=.04*envelope)
                _pose_rotation(rig,'upper_arm_r',x=-.45*envelope)
                _pose_rotation(rig,'forearm_r',x=-.8*envelope)
            else:
                yaw = .5*math.sin(2*math.pi*t)*math.sin(math.pi*t)
                _pose_rotation(rig,'chest',y=yaw*.4); _pose_rotation(rig,'head',y=yaw)
            for bone in rig.pose.bones:
                bone.keyframe_insert('rotation_quaternion', frame=frame, group=bone.name)
                bone.keyframe_insert('location', frame=frame, group=bone.name)
        if stride:
            for event, frame in [('foot-left',1),('foot-right',round(duration*30/2)+1)]:
                marker = action.pose_markers.new(event); marker.frame = frame
        # These are baked samples, so use explicit linear component curves.
        # Blender's automatic Bezier easing otherwise changes between-key motion.
        _linear_samples(action)
    rig.animation_data.action = next(a for a in bpy.data.actions if a.get('dl64_clip_id') == 'idle')
    scene.frame_start = 1; scene.frame_end = 61; scene.frame_set(1)


def _linear_samples(action):
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    for key in curve.keyframe_points: key.interpolation='LINEAR'


def _studio(scene, collection):
    material = bpy.data.materials.new('Studio | charcoal'); material.diffuse_color = (.027,.039,.042,1)
    mesh = bpy.data.meshes.new('Studio floor'); mesh.from_pydata([(-20,-20,-.012),(20,-20,-.012),(20,20,-.012),(-20,20,-.012)],[],[(0,1,2,3)])
    mesh.materials.append(material); obj = bpy.data.objects.new('Studio floor | excluded',mesh); collection.objects.link(obj)
    for name,pos,power,size,color in [('Warm key',(-2,-3,4),430,3,(1,.83,.63)),('Moon edge',(2,1,3),650,2,(.55,.72,1)),('Fill',(1,-4,1.5),100,2,(.75,.85,1))]:
        data = bpy.data.lights.new(name,'AREA'); data.energy=power; data.size=size; data.color=color
        obj=bpy.data.objects.new(name,data);collection.objects.link(obj);obj.location=pos
        obj.rotation_euler=(Vector((0,0,1))-obj.location).to_track_quat('-Z','Y').to_euler()
    data=bpy.data.cameras.new('Guard portrait');camera=bpy.data.objects.new('Guard portrait',data);collection.objects.link(camera)
    camera.location=(2.8,-5,2.65);camera.rotation_euler=(Vector((0,0,.94))-camera.location).to_track_quat('-Z','Y').to_euler()
    data.type='ORTHO';data.ortho_scale=2.15;scene.camera=camera
    scene.render.engine='CYCLES';scene.cycles.samples=24
    scene.render.resolution_x=600;scene.render.resolution_y=720;scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.view_settings.view_transform='Standard'
    scene.world=bpy.data.worlds.new('Guard studio');scene.world.color=(.07,.07,.07)


def create(source=None, output=None):
    source = Path(source or ROOT/'art/guard.blend').resolve()
    output = Path(output or ROOT/'content/assets/guard').resolve()
    if source.exists(): raise ValueError('Refusing to overwrite existing artist source: '+str(source))
    output.mkdir(parents=True,exist_ok=True)
    scene=bpy.data.scenes.new('DarkLantern64 | Guard Animation Study');bpy.context.window.scene=scene
    scene.render.fps=30;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    collection=bpy.data.collections.new('CHARACTER | exported');scene.collection.children.link(collection)
    rig,bones=_armature(collection);material=_atlas(output);obj=_geometry(collection,material,bones)
    modifier=obj.modifiers.new('Armature | one full-strength bone per vertex','ARMATURE');modifier.object=rig
    obj.parent=rig;_actions(scene,rig)
    studio=bpy.data.collections.new('STUDIO | excluded');scene.collection.children.link(studio);_studio(scene,studio)
    scene['README']='Guard animation study. Select RIG and choose Guard Actions in Action Editor. Clips are editable keyframes at 30 Hz. Re-export saved source with tools/guard_assets.py. Root + pelvis are separate; no cloth or facial rig. Asymmetric red band is on right arm.'
    for ob in bpy.context.view_layer.objects: ob.select_set(False)
    rig.select_set(True);bpy.context.view_layer.objects.active=rig
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                area.spaces.active.region_3d.view_distance=3.2;area.spaces.active.region_3d.view_location=(0,0,.95)
                area.spaces.active.shading.type='MATERIAL'
    source.parent.mkdir(parents=True,exist_ok=True);bpy.ops.wm.save_as_mainfile(filepath=str(source),compress=True)
    result=export(scene,output)
    return {'source':str(source),**result}


def _tr(matrix):
    pos,rot,scale=matrix.decompose()
    if any(abs(s-1)>1e-4 for s in scale): raise ValueError('Animated scale is not supported')
    return [float(v) for v in pos],[float(rot.x),float(rot.y),float(rot.z),float(rot.w)]


def export(scene=None, output=None):
    scene=scene or bpy.context.scene;output=Path(output or ROOT/'content/assets/guard').resolve()
    objects=[o for o in scene.objects if o.type=='MESH' and o.get('dl64_character_id')=='basic-guard']
    if len(objects)!=1: raise ValueError('Expected one basic-guard mesh in the active scene')
    obj=objects[0];rig=obj.find_armature()
    if rig is None: raise ValueError('Character needs its armature modifier')
    if obj.matrix_world != Matrix.Identity(4) or rig.matrix_world != Matrix.Identity(4):
        raise ValueError('Apply character/armature object transforms before exporting')
    bones=list(rig.data.bones);bone_ids={b.name:i for i,b in enumerate(bones)}
    # Blender's list order is not an asset ABI: traverse parent-first explicitly.
    ordered=[]
    def visit(bone):
        ordered.append(bone)
        for child in sorted(bone.children,key=lambda b:b.name): visit(child)
    for bone in sorted((b for b in bones if b.parent is None),key=lambda b:b.name): visit(bone)
    bones=ordered;bone_ids={b.name:i for i,b in enumerate(bones)}
    records=[]
    for bone in bones:
        local=bone.parent.matrix_local.inverted()@bone.matrix_local if bone.parent else bone.matrix_local
        translation,rotation=_tr(TO_GAME@local@TO_BLENDER)
        records.append({'id':bone.name,'parent':bone_ids[bone.parent.name] if bone.parent else -1,'translation':translation,'rotation':rotation})
    mesh=obj.data;mesh.calc_loop_triangles();uv=mesh.uv_layers.active
    verts,normals,uvs,joints,indices=[],[],[],[],[];lookup={}
    for triangle in mesh.loop_triangles:
        for loop in triangle.loops:
            vertex=mesh.vertices[mesh.loops[loop].vertex_index]
            groups=[g for g in vertex.groups if g.weight>1e-6]
            if len(groups)!=1 or abs(groups[0].weight-1)>1e-5: raise ValueError('Exactly one full-strength bone per vertex is required')
            bone=bone_ids[obj.vertex_groups[groups[0].group].name]
            pos=TO_GAME@vertex.co;normal=TO_GAME.to_3x3()@mesh.corner_normals[loop].vector
            tex=list(uv.data[loop].uv)
            key=tuple(round(v,7) for v in (*pos,*normal,*tex))+ (bone,)
            if key not in lookup:
                lookup[key]=len(verts);verts.append(list(pos));normals.append(list(normal));uvs.append(tex);joints.append(bone)
            indices.append(lookup[key])
    previous_action=rig.animation_data.action;previous_frame=scene.frame_current
    clips=[]
    try:
        for action in sorted((a for a in bpy.data.actions if a.get('dl64_clip_id')),key=lambda a:a['dl64_clip_id']):
            rig.animation_data.action=action
            start,end=int(action['dl64_frame_start']),int(action['dl64_frame_end']);frames=[]
            for frame in range(start,end+1):
                scene.frame_set(frame);bpy.context.view_layer.update()
                translations,rotations=[],[]
                for bone in bones:
                    pose=rig.pose.bones[bone.name]
                    local=pose.parent.matrix.inverted()@pose.matrix if pose.parent else pose.matrix
                    pos,rot=_tr(TO_GAME@local@TO_BLENDER);translations.append(pos);rotations.append(rot)
                frames.append({'translations':translations,'rotations':rotations})
            fps=scene.render.fps/scene.render.fps_base
            clips.append({'id':action['dl64_clip_id'],'loop':bool(action['dl64_loop']),'fps':fps,
                'frames':frames,'events':[{'id':m.name,'time':(m.frame-start)/fps} for m in action.pose_markers],
                'stride_length':float(action.get('dl64_stride_length',0))})
    finally:
        rig.animation_data.action=previous_action;scene.frame_set(previous_frame)
    data={'version':1,'id':'basic-guard','skeleton_id':rig.get('dl64_skeleton_id','humanoid-v1'),
        'coordinates':'RH_Y_UP_Z_FORWARD_METERS','bones':records,
        'mesh':{'vertices':verts,'normals':normals,'uvs':uvs,'indices':indices,'joints':joints},
        'clips':clips,'sockets':[{'id':'hand_r','bone':bone_ids['hand_r'],'translation':[0,0,0],'rotation':[0,0,0,1]},
                               {'id':'head','bone':bone_ids['head'],'translation':[0,0,0],'rotation':[0,0,0,1]}]}
    _json_write(output/'guard.character.json',data)
    # Persist packed source texture changes on export without modifying .blend.
    image=next(n.image for n in obj.data.materials[0].node_tree.nodes if n.type=='TEX_IMAGE' and n.image)
    image.filepath_raw=str(output/'guard-atlas.png');image.file_format='PNG';image.save()
    return {'character':str(output/'guard.character.json'),'bones':len(bones),'vertices':len(verts),'triangles':len(indices)//3,
            'clips':[{ 'id':c['id'],'frames':len(c['frames'])} for c in clips]}
