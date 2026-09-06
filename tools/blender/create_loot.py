"""Create the original DarkLantern64 low-poly loot study in Blender.

Run with Blender --background --factory-startup --python this_file, or execute
in an empty Blender session. Creates its own scene, preserving existing scenes.
All five export meshes are authored in metres with their origin on the floor.
"""
from pathlib import Path
import math
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
RUBY_TILES = {6: (.96, .07, .16), 7: (1, .30, .38), 14: (.56, .025, .065)}


def atlas_uv(mesh, polygon, tile):
    """Inset each face inside one palette cell to resist bilinear bleeding."""
    cx, cy = tile % 4, tile // 4
    uv = mesh.uv_layers.active
    count = len(polygon.loop_indices)
    for index, loop in enumerate(polygon.loop_indices):
        if count == 4:
            x, y = ((.18, .18), (.82, .18), (.82, .82), (.18, .82))[index]
        else:
            angle = 2 * math.pi * index / count
            x, y = .5 + .32 * math.cos(angle), .5 + .32 * math.sin(angle)
        uv.data[loop].uv = ((cx + x) / 4, (cy + y) / 4)


def _sharp_smoothing_boundaries(mesh):
    adjacent = {}
    for polygon in mesh.polygons:
        for edge in polygon.edge_keys:
            adjacent.setdefault(tuple(sorted(edge)), []).append(polygon.use_smooth)
    for edge in mesh.edges:
        faces = adjacent.get(tuple(sorted(edge.vertices)), [])
        edge.use_edge_sharp = not faces or not all(faces)
    mesh.update()


def refine_loot(objects, update_ruby=True):
    """Targeted visual edits; retain all vertex positions and scene edits.

    This also supports refining the live .blend without regenerating it. The
    body/cuff distinction uses existing topology and relative profile heights.
    """
    by_id = {obj.get('dl64_asset_id'): obj for obj in objects}
    for ident in ('loot-goblet', 'loot-purse'):
        obj = by_id[ident]
        mesh = obj.data
        low, high = min(v.co.z for v in mesh.vertices), max(v.co.z for v in mesh.vertices)
        height = high - low
        if height <= 0:
            raise ValueError(ident + ': expected an upright authored profile')
        body = set(range(len(mesh.vertices)))
        if ident == 'loot-purse':
            # The largest connected component is the leather bag. Its cuff
            # remains part of that mesh; tie ring and cords are separate parts.
            neighbors = {v.index: set() for v in mesh.vertices}
            for edge in mesh.edges:
                a, b = edge.vertices
                neighbors[a].add(b)
                neighbors[b].add(a)
            remaining, components = set(neighbors), []
            while remaining:
                stack, component = [next(iter(remaining))], set()
                while stack:
                    vertex = stack.pop()
                    if vertex in component:
                        continue
                    component.add(vertex)
                    stack.extend(neighbors[vertex] - component)
                remaining -= component
                components.append(component)
            body = max(components, key=len)
        for polygon in mesh.polygons:
            heights = [(mesh.vertices[index].co.z - low) / height for index in polygon.vertices]
            vertical_span = max(heights) - min(heights)
            if ident == 'loot-goblet':
                # The base below 0.052 m, horizontal lip and inner/bottom caps
                # retain hard edges; stem and both bowl walls shade smoothly.
                polygon.use_smooth = vertical_span > 1e-5 and min(heights) >= .125
            else:
                polygon.use_smooth = (set(polygon.vertices).issubset(body) and
                                      vertical_span > 1e-5 and max(heights) <= .892)
        _sharp_smoothing_boundaries(mesh)
        if ident == 'loot-purse':
            # A consistent leather cell keeps profile rings from reading as
            # horizontal stripes; geometry normals supply the body shading.
            for polygon in mesh.polygons:
                if polygon.use_smooth:
                    atlas_uv(mesh, polygon, 10)
            mesh.update()
        obj['dl64_surface_style'] = ('Smooth stem/bowl; crisp base/lip' if ident == 'loot-goblet'
                                     else 'Smooth leather body; crisp cuff/ties/cords')

    jewel = by_id['loot-jewel']
    for index, polygon in enumerate(jewel.data.polygons):
        polygon.use_smooth = False
        tile = 6 if len(polygon.vertices) != 4 else (7 if index % 5 == 0 else (6 if index % 2 else 14))
        atlas_uv(jewel.data, polygon, tile)
    jewel.data.update()
    jewel['dl64_surface_style'] = 'Flat ruby facets; red-only palette including dark facets'
    if update_ruby:
        material = jewel.material_slots[0].material
        shader = next(node for node in material.node_tree.nodes if node.type == 'BSDF_PRINCIPLED')
        image = shader.inputs['Base Color'].links[0].from_node.image
        pixels = list(image.pixels[:])
        if tuple(image.size) != (32, 32) or len(pixels) != 32 * 32 * 4:
            raise ValueError('Ruby refinement expects the original 32x32 loot atlas')
        for tile, color in RUBY_TILES.items():
            for v in range(8):
                for u in range(8):
                    x, y = (tile % 4) * 8 + u, (tile // 4) * 8 + v
                    factor = .87 + .18 * v / 7 + .05 * math.sin((u + v) * 2.4)
                    offset = (y * 32 + x) * 4
                    pixels[offset:offset+4] = [*[min(1, channel * factor) for channel in color], 1]
        image.pixels.foreach_set(pixels)
        image.update()
        image.pack()
    return [{'id': ident, 'smooth_faces': sum(p.use_smooth for p in by_id[ident].data.polygons),
             'sharp_edges': sum(e.use_edge_sharp for e in by_id[ident].data.edges)}
            for ident in ('loot-goblet', 'loot-purse', 'loot-jewel')]


class MeshBuilder:
    def __init__(self):
        self.vertices, self.faces, self.tiles = [], [], []

    def face(self, indices, tile):
        self.faces.append(indices)
        self.tiles.append(tile)

    def lathe(self, rings, sides=8, tile=0, offset=(0, 0, 0), caps=True, phase=0):
        """radius/Z profile; reversed inner profiles make a hollow vessel."""
        base = len(self.vertices)
        for radius, z in rings:
            for i in range(sides):
                angle = phase + i * 2 * math.pi / sides
                self.vertices.append((offset[0] + radius * math.cos(angle),
                                      offset[1] + radius * math.sin(angle), offset[2] + z))
        for j in range(len(rings)-1):
            for i in range(sides):
                a, b = base+j*sides+i, base+j*sides+(i+1)%sides
                t = tile[j % len(tile)] if isinstance(tile, list) else tile
                self.face((a, b, b+sides, a+sides), t)
        if caps:
            t = tile[0] if isinstance(tile, list) else tile
            self.face(tuple(base+i for i in reversed(range(sides))), t)
            t = tile[-1] if isinstance(tile, list) else tile
            self.face(tuple(base+(len(rings)-1)*sides+i for i in range(sides)), t)

    def tube(self, points, radius, sides=4, tile=3):
        base = len(self.vertices)
        for j, point in enumerate(points):
            tangent = Vector(points[min(j+1,len(points)-1)]) - Vector(points[max(j-1,0)])
            tangent.normalize()
            reference = Vector((0,0,1)) if abs(tangent.z) < .9 else Vector((0,1,0))
            u = tangent.cross(reference).normalized()
            v = tangent.cross(u).normalized()
            for i in range(sides):
                angle = i * 2 * math.pi / sides
                self.vertices.append(tuple(Vector(point) + radius*(math.cos(angle)*u + math.sin(angle)*v)))
        for j in range(len(points)-1):
            for i in range(sides):
                a, b = base+j*sides+i, base+j*sides+(i+1)%sides
                self.face((a,b,b+sides,a+sides),tile)
        self.face(tuple(base+i for i in reversed(range(sides))),tile)
        self.face(tuple(base+(len(points)-1)*sides+i for i in range(sides)),tile)

    def object(self, ident, label, material, collection, location):
        mesh = bpy.data.meshes.new(ident)
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.update()
        uv = mesh.uv_layers.new(name='UVMap')
        for polygon, tile in zip(mesh.polygons, self.tiles):
            atlas_uv(mesh, polygon, tile)
        mesh.materials.append(material)
        obj = bpy.data.objects.new(label, mesh)
        collection.objects.link(obj)
        obj.location = location
        obj['dl64_asset_id'] = ident
        obj['authoring_note'] = 'Original procedural mesh; origin at base; metres; shared 32x32 atlas.'
        return obj


def make_atlas():
    image = bpy.data.images.new('loot_atlas', width=32, height=32, alpha=True)
    image.colorspace_settings.name = 'sRGB'
    # RGB values are deliberately bright; actual game light multiplies them.
    colors = [(0.80,.47,.10),(1,.81,.31),(.46,.24,.045),(.96,.71,.20),
              (.75,.86,.94),(.27,.41,.55),(.85,.055,.16),(1,.31,.40),
              (.12,.54,.73),(.36,.87,.94),(.31,.105,.037),(.52,.23,.071),
              (.72,.43,.15),(.14,.038,.016),(.22,.018,.048),(.91,.95,1)]
    for tile, color in RUBY_TILES.items():
        colors[tile] = color
    pixels = []
    for y in range(32):
        for x in range(32):
            tile = (y//8)*4+x//8
            u,v = x%8,y%8
            factor = .87 + .18*v/7 + .05*math.sin((u+v)*2.4)
            if tile in (0,1,3):
                # Tiny engraved cross/diamond makes the coin faces readable.
                if abs(u-3.5)+abs(v-3.5)<2 or u in (1,6) or v in (1,6): factor*=.83
            if tile in (10,11,13):
                factor = .83 + ((u*17+v*29)%11)*.023
            if tile == 12 and (u in (2,5) and v%3 == 0): factor=.55
            pixels.extend((*[min(1,c*factor) for c in colors[tile]],1))
    image.pixels.foreach_set(pixels)
    image.update()
    image.pack()
    mat = bpy.data.materials.new('Loot | gilded metal, ruby and leather atlas')
    mat.use_nodes = True
    mat.use_backface_culling = True
    mat['dl64_material_id'] = 'mat-loot-atlas'
    nodes = mat.node_tree.nodes
    shader = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    shader.inputs['Metallic'].default_value = 0
    shader.inputs['Roughness'].default_value = .7
    tex = nodes.new('ShaderNodeTexImage')
    tex.image, tex.interpolation = image, 'Linear'
    mat.node_tree.links.new(tex.outputs['Color'], shader.inputs['Base Color'])
    return mat


def create(source=None, output=None):
    source=Path(source or ROOT/'art/loot.blend')
    output=Path(output or ROOT/'content/assets/loot')
    if 'DarkLantern64 Loot' in bpy.data.scenes:
        raise RuntimeError('Loot scene already exists. Open a fresh file before regenerating; existing work is preserved.')
    scene = bpy.data.scenes.new('DarkLantern64 Loot')
    bpy.context.window.scene = scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    assets = bpy.data.collections.new('EXPORT | five game props')
    scene.collection.children.link(assets)
    material = make_atlas()
    objects=[]

    b=MeshBuilder()
    for x,y,z,r,phase in [(-.075,-.025,0,.085,0),(.08,.015,0,.087,.2),(-.012,.053,.026,.083,.1)]:
        b.lathe([(r*.92,0),(r,.007),(r,.025)],8,[2,0,1],(x,y,z),phase=phase)
    objects.append(b.object('loot-coins','01 | Crown coins',material,assets,(-1.0,0,0)))

    b=MeshBuilder()
    b.lathe([(.093,0),(.105,.015),(.037,.052),(.023,.19),(.09,.22),(.132,.32),
             (.137,.405),(.12,.405),(.112,.33),(.072,.265)],8,[0,1,2,0,0,1,3,2,2],caps=True)
    objects.append(b.object('loot-goblet','02 | Gilded goblet',material,assets,(-.52,0,0)))

    b=MeshBuilder()
    b.lathe([(.026,0),(.11,.063),(.115,.088),(.065,.16)],8,[6,7,6],caps=True,phase=math.pi/8)
    # Facet-specific palette, no transparent blend or sorting cost.
    for i in range(len(b.tiles)-2): b.tiles[i] = 7 if i%5==0 else (6 if i%2 else 14)
    objects.append(b.object('loot-jewel','03 | Cut garnet',material,assets,(0,0,0)))

    b=MeshBuilder()
    b.lathe([(.065,0),(.13,.045),(.14,.16),(.09,.255),(.04,.285),(.068,.32)],8,[10,10,10,10,12])
    b.tiles[-2] = 13  # Keep the separately shaded bottom cap under the bag.
    for i,(x,y,z) in enumerate(b.vertices): b.vertices[i]=(x,y*.8,z)
    b.lathe([(.051,.262),(.049,.28)],8,3)
    b.tube([(-.036,-.052,.277),(-.08,-.084,.255),(-.086,-.10,.17)],.009,tile=12)
    b.tube([(.036,-.052,.277),(.07,-.095,.24),(.048,-.111,.185)],.009,tile=12)
    objects.append(b.object('loot-purse','04 | Drawstring purse',material,assets,(.48,0,0)))

    b=MeshBuilder()
    b.lathe([(.032,0),(.044,.03),(.022,.064),(.018,.52),(.043,.545),(.028,.57),
             (.051,.64),(.051,.67)],6,[3,0,13,0,1,0,3])
    b.lathe([(.02,.644),(.105,.735),(.04,.805)],6,[6,7])
    for i in range(4):
        a=i*math.pi/2
        b.tube([(.038*math.cos(a),.038*math.sin(a),.64),
                (.098*math.cos(a),.098*math.sin(a),.713),
                (.085*math.cos(a),.085*math.sin(a),.777)],.012,3,1)
    objects.append(b.object('loot-scepter','05 | Magistrate scepter',material,assets,(1.0,0,0)))
    refine_loot(objects, update_ruby=False)

    # Separate presentation objects never enter the export collection.
    presentation=bpy.data.collections.new('PRESENTATION | excluded from game export')
    scene.collection.children.link(presentation)
    floor_material=bpy.data.materials.new('Studio charcoal')
    floor_material.diffuse_color=(.016,.027,.039,1)
    floor_material.use_nodes=True
    floor_shader=next(n for n in floor_material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    floor_shader.inputs['Base Color'].default_value=(.016,.027,.039,1)
    floor_shader.inputs['Roughness'].default_value=.9
    mesh=bpy.data.meshes.new('Studio floor')
    mesh.from_pydata([(-200,-200,-.006),(200,-200,-.006),(200,200,-.006),(-200,200,-.006)],[],[(0,1,2,3)])
    mesh.materials.append(floor_material)
    floor=bpy.data.objects.new('Studio floor (not exported)',mesh)
    presentation.objects.link(floor)
    for name,location,power,size,color in [('Warm softbox',(-2,-3,4),420,3,(1,.79,.52)),
                                          ('Moon rim',(1.5,1,3),500,2,(.45,.66,1)),
                                          ('Front fill',(1,-4,2),160,2,(.8,.9,1))]:
        data=bpy.data.lights.new(name,'AREA'); data.energy=power; data.shape='DISK';data.size=size;data.color=color
        obj=bpy.data.objects.new(name,data);presentation.objects.link(obj);obj.location=location
        obj.rotation_euler=(Vector((0,0,.25))-obj.location).to_track_quat('-Z','Y').to_euler()
    data=bpy.data.cameras.new('Asset lineup'); camera=bpy.data.objects.new('Asset lineup',data)
    presentation.objects.link(camera);camera.location=(1,-3.8,2.1)
    camera.rotation_euler=(Vector((0,0,.32))-camera.location).to_track_quat('-Z','Y').to_euler()
    data.type='ORTHO';data.ortho_scale=2.65;scene.camera=camera
    scene.render.engine='CYCLES';scene.cycles.samples=32
    scene.render.resolution_x=1440;scene.render.resolution_y=720;scene.render.resolution_percentage=100
    scene.world=bpy.data.worlds.new('Night studio');scene.world.color=(.08,.08,.08)
    scene.view_settings.view_transform='Standard'
    scene.render.image_settings.file_format='PNG'
    scene.render.filepath=str(ROOT/'docs/images/loot-blender.png')
    scene['README']='Select the five meshes in EXPORT, File > Export > DarkLantern64 Asset Pack. One unit is one metre; origins rest on surfaces. Studio cameras/lights/floor are excluded. Game has base texture and geometric lighting, no PBR specular.'
    for obj in bpy.context.view_layer.objects: obj.select_set(False)
    for obj in objects: obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[1]
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                area.spaces.active.region_3d.view_distance=3
                area.spaces.active.region_3d.view_location=(0,0,.3)
                area.spaces.active.shading.type='MATERIAL'
    bpy.context.view_layer.update()
    source.parent.mkdir(parents=True,exist_ok=True)
    (ROOT/'docs/images').mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(ROOT/'tools/blender'))
    from darklantern64_export import export_pack
    manifest=export_pack(bpy.context,ROOT/'content',output,objects=objects)
    bpy.ops.wm.save_as_mainfile(filepath=str(source),compress=True)
    return {'blend':str(source),'assets':len(manifest['assets']),
            'meshes':[{ 'id':o['dl64_asset_id'],'vertices':len(o.data.vertices),
                        'triangles':sum(len(p.vertices)-2 for p in o.data.polygons)} for o in objects]}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    result=create(args.source,args.output)
    print(result)
