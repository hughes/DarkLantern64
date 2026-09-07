"""Deterministic, editable low-poly Sponza interpretation (metres, Y up).

Run with Python to export OBJ + geometry.json. Run the same script in a separate
factory-startup Blender background process with --blend to create art/sponza.blend.
Never run --blend through the user's interactive Blender connection.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'content/assets/sponza'

def sub(a,b):return tuple(x-y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def unit(a):
    length=math.sqrt(dot(a,a))
    if length<1e-9:raise ValueError('Degenerate authored face')
    return tuple(v/length for v in a)
def transform(position=(0,0,0),rotation=(0,0,0),scale=(1,1,1)):
    return {'position':list(position),'rotation':list(rotation),'scale':list(scale)}

class Mesh:
    def __init__(self,name,origin=(0,0,0)):
        self.name=name;self.origin=origin;self.vertices=[];self.uvs=[];self.normals=[];self.indices=[];self._keys={}
    def face(self,points,normal=None,uvs=None,normals=None,center=False):
        points=list(points)
        direction=unit(cross(sub(points[1],points[0]),sub(points[2],points[0])))
        if normal is not None and dot(direction,normal)<0:
            points.reverse()
            if uvs is not None:uvs=list(reversed(uvs))
            if normals is not None:normals=list(reversed(normals))
            direction=tuple(-v for v in direction)
        if uvs is None:
            axis=max(range(3),key=lambda i:abs(direction[i]))
            axes=((2,1),(0,2),(0,1))[axis]
            uvs=[(p[axes[0]]*.5,p[axes[1]]*.5) for p in points]
        if normals is None:normals=[direction]*len(points)
        def add(p,uv,n):
            p=sub(p,self.origin);n=unit(n)
            key=tuple(round(x,7) for x in (*p,*uv,*n))
            if key not in self._keys:
                self._keys[key]=len(self.vertices)
                self.vertices.append(p);self.uvs.append(uv);self.normals.append(n)
            return self._keys[key]
        corners=[add(p,uv,n) for p,uv,n in zip(points,uvs,normals)]
        if center:
            count=len(points)
            middle=add(tuple(sum(p[i] for p in points)/count for i in range(3)),
                       tuple(sum(uv[i] for uv in uvs)/count for i in range(2)),
                       tuple(sum(n[i] for n in normals)/count for i in range(3)))
            for i in range(count):self.indices.extend((middle,corners[i],corners[(i+1)%count]))
        else:
            for i in range(1,len(corners)-1):self.indices.extend((corners[0],corners[i],corners[i+1]))
    def box(self,lo,hi,faces='xyzXYZ'):
        x,y,z=lo;X,Y,Z=hi
        specs={
            'x':([(x,y,z),(x,Y,z),(x,Y,Z),(x,y,Z)],(-1,0,0)),
            'X':([(X,y,z),(X,y,Z),(X,Y,Z),(X,Y,z)],(1,0,0)),
            'y':([(x,y,z),(x,y,Z),(X,y,Z),(X,y,z)],(0,-1,0)),
            'Y':([(x,Y,z),(X,Y,z),(X,Y,Z),(x,Y,Z)],(0,1,0)),
            'z':([(x,y,z),(X,y,z),(X,Y,z),(x,Y,z)],(0,0,-1)),
            'Z':([(x,y,Z),(x,Y,Z),(X,Y,Z),(X,y,Z)],(0,0,1))}
        for key in faces:self.face(*specs[key])
    def horizontal(self,x0,x1,z0,z1,y,nx=1,nz=1,up=True):
        for ix in range(nx):
            for iz in range(nz):
                a=x0+(x1-x0)*ix/nx;b=x0+(x1-x0)*(ix+1)/nx
                c=z0+(z1-z0)*iz/nz;d=z0+(z1-z0)*(iz+1)/nz
                self.face([(a,y,c),(b,y,c),(b,y,d),(a,y,d)],(0,1 if up else -1,0))
    def cylinder(self,rings,sides=6,offset=(0,0,0)):
        ox,oy,oz=offset
        slopes=[(hi[1]-lo[1])/(hi[0]-lo[0]) for lo,hi in zip(rings,rings[1:])]
        ring_slopes=[slopes[0]]+[(a+b)/2 for a,b in zip(slopes,slopes[1:])]+[slopes[-1]]
        for ring_index,(lo,hi) in enumerate(zip(rings,rings[1:])):
            for i in range(sides):
                a=2*math.pi*i/sides;b=2*math.pi*(i+1)/sides
                points=[(ox+math.cos(a)*lo[1],oy+lo[0],oz+math.sin(a)*lo[1]),
                    (ox+math.cos(b)*lo[1],oy+lo[0],oz+math.sin(b)*lo[1]),
                    (ox+math.cos(b)*hi[1],oy+hi[0],oz+math.sin(b)*hi[1]),
                    (ox+math.cos(a)*hi[1],oy+hi[0],oz+math.sin(a)*hi[1])]
                normals=[(math.cos(t),-ring_slopes[r],math.sin(t))
                         for t,r in ((a,ring_index),(b,ring_index),(b,ring_index+1),(a,ring_index+1))]
                self.face(points,(math.cos((a+b)/2),0,math.sin((a+b)/2)),
                    [(i/sides,lo[0]*.5),((i+1)/sides,lo[0]*.5),((i+1)/sides,hi[0]*.5),(i/sides,hi[0]*.5)],normals)
        for ring,normal in ((rings[0],(0,-1,0)),(rings[-1],(0,1,0))):
            self.face([(ox+math.cos(2*math.pi*i/sides)*ring[1],oy+ring[0],oz+math.sin(2*math.pi*i/sides)*ring[1]) for i in range(sides)],normal)
    def arch(self,half_width,spring,rise,top,depth,segments=6,offset=0):
        # Spandrels and reveal form a real arch-shaped opening. Top/end faces
        # are enclosed by adjoining slab/columns, so no hidden duplicate caps.
        points=[(offset-half_width*math.cos(math.pi*i/segments),spring+rise*math.sin(math.pi*i/segments)) for i in range(segments+1)]
        for (x,y),(X,Y) in zip(points,points[1:]):
            for z,normal in ((-depth/2,(0,0,-1)),(depth/2,(0,0,1))):
                self.face([(x,y,z),(X,Y,z),(X,top,z),(x,top,z)],normal)
            self.face([(x,y,-depth/2),(x,y,depth/2),(X,Y,depth/2),(X,Y,-depth/2)],(0,-1,0))
    def export(self,path):
        def row(prefix,values):return prefix+' '+' '.join(f'{v:.7f}'.rstrip('0').rstrip('.') if abs(v)>1e-10 else '0' for v in values)
        lines=['# DarkLantern64 low-poly Sponza interpretation; metres, Y-up.','o '+self.name]
        lines += [row('v',v) for v in self.vertices]
        lines += [row('vt',v) for v in self.uvs]
        lines += [row('vn',v) for v in self.normals]
        for i in range(0,len(self.indices),3):lines.append('f '+' '.join(f'{v+1}/{v+1}/{v+1}' for v in self.indices[i:i+3]))
        path.write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')

def build():
    meshes={};instances=[];colliders=[]
    def mesh(name,origin=(0,0,0)):
        value=Mesh(name,origin);meshes[name]=value;return value
    def instance(name,material,position=None,rotation=(0,0,0),ident=None):
        value=meshes[name]
        instances.append({'id':ident or 'sponza-'+name,'model':'mesh-sponza-'+name,
            'material':'mat-sponza-'+material,'transform':transform(position if position is not None else value.origin,rotation)})
    def collider(name,lo,hi):
        center=[(a+b)/2 for a,b in zip(lo,hi)]
        colliders.append({'id':'sponza-collision-'+name.replace('.','p'),'transform':transform(center),
            'collider':{'shape':'box','center':[0,0,0],'half_size':[(b-a)/2 for a,b in zip(lo,hi)]}})

    # Six-sided shaft surrounds its square collision proxy. The old narrow
    # shaft put every normal-offset lighting sample inside the opaque blocker.
    m=mesh('ground-column');m.cylinder([(0,.44),(.20,.40),(2.83,.39),(3.05,.45)])
    for side,z in (('north',-3.1),('south',3.1)):
        for i,x in enumerate((-8,-4,0,4,8)):
            instance(m.name,'trim',(x,0,z),ident=f'sponza-column-{side}-{i}')
            collider(f'column-{side}-{i}',(x-.34,0,z-.34),(x+.34,3.05,z+.34))
    m=mesh('ground-arch');m.arch(2,3.0,1.85,5.10,.38,6)
    for side,z in (('north',-3.1),('south',3.1)):
        for i,x in enumerate((-6,-2,2,6)):
            instance(m.name,'stone',(x,0,z),ident=f'sponza-arch-{side}-{i}')
    m=mesh('ground-end-arch');m.arch(3.1,2.3,2.55,5.10,.38,6)
    for side,x in (('west',-8),('east',8)):
        instance(m.name,'stone',(x,0,0),(0,90,0),ident='sponza-arch-'+side)

    # Ground tiles are subdivided for light interpolation and split into bays.
    for i,(x0,x1) in enumerate(((-11.4,-8),(-8,-4),(-4,0),(0,4),(4,8),(8,11.4))):
        m=mesh('ground-floor-'+str(i),((x0+x1)/2,0,0));m.horizontal(x0,x1,-6,6,0,2,4)
        instance(m.name,'floor');collider(m.name,(x0,-.2,-6),(x1,0,6))

    # Gallery top, underside and parapets share footprints above the aisles.
    for side,z0,z1 in (('north',-6,-2.78),('south',2.78,6)):
        for i,(x0,x1) in enumerate(((-8,-4),(-4,0),(0,4),(4,8))):
            m=mesh(f'gallery-{side}-{i}',((x0+x1)/2,5.3,(z0+z1)/2));m.horizontal(x0,x1,z0,z1,5.3,2,2)
            instance(m.name,'floor');collider(m.name,(x0,5.1,z0),(x1,5.3,z1))
        m=mesh('ceiling-'+side,(0,5.1,(z0+z1)/2));m.horizontal(-8,8,z0,z1,5.1,8,1,False)
        instance(m.name,'plaster')
        m=mesh('upper-ceiling-'+side,(0,10.5,(z0+z1)/2));m.horizontal(-8,8,z0,z1,10.5,4,1,False)
        instance(m.name,'plaster');collider(m.name,(-8,10.5,z0),(8,10.7,z1))
        m=mesh('parapet-'+side,(0,5.7,(-2.91 if side=='north' else 2.91)))
        z=-2.91 if side=='north' else 2.91
        m.box((-8,5.3,z-.13),(8,6.1,z+.13),faces='xzXYZ')
        instance(m.name,'trim');collider(m.name,(-8,5.3,z-.13),(8,6.1,z+.13))
    for side,x0,x1 in (('west',-11.4,-8),('east',8,11.4)):
        m=mesh('gallery-'+side,((x0+x1)/2,5.3,0));m.horizontal(x0,x1,-6,6,5.3,2,4)
        instance(m.name,'floor');collider(m.name,(x0,5.1,-6),(x1,5.3,6))
        m=mesh('ceiling-'+side,((x0+x1)/2,5.1,0));m.horizontal(x0,x1,-6,6,5.1,2,2,False)
        instance(m.name,'plaster')
        m=mesh('upper-ceiling-'+side,((x0+x1)/2,10.5,0));m.horizontal(x0,x1,-6,6,10.5,1,2,False)
        instance(m.name,'plaster');collider(m.name,(x0,10.5,-6),(x1,10.7,6))
        x=-7.9 if side=='west' else 7.9
        m=mesh('parapet-'+side,(x,5.7,0));m.box((x-.13,5.3,-2.78),(x+.13,6.1,2.78),faces='xzXYZ')
        instance(m.name,'trim');collider(m.name,(x-.13,5.3,-2.78),(x+.13,6.1,2.78))

    # Upper posts are paired by bay; twenty posts total includes end centers.
    m=mesh('upper-post-pair')
    for x in (-2,0):m.cylinder([(0,.20),(2.62,.20)],offset=(x,0,0))
    for side,z in (('north',-3.1),('south',3.1)):
        for i,x in enumerate((-6,-2,2,6)):
            instance(m.name,'trim',(x,6.1,z),ident=f'sponza-upper-posts-{side}-{i}')
    m=mesh('upper-post-single');m.cylinder([(0,.20),(2.62,.20)])
    for i,(x,z) in enumerate(((8,-3.1),(8,3.1),(-8,0),(8,0))):
        instance(m.name,'trim',(x,6.1,z),ident='sponza-upper-post-end-'+str(i))
    m=mesh('upper-arch-pair')
    for x in (-1,1):m.arch(1,8.72,1.70,11.2,.30,4,x)
    for side,z in (('north',-3.1),('south',3.1)):
        for i,x in enumerate((-6,-2,2,6)):
            instance(m.name,'stone',(x,0,z),ident=f'sponza-upper-arches-{side}-{i}')
    m=mesh('upper-end-arch-pair')
    for x in (-1.55,1.55):m.arch(1.55,8.72,1.70,11.2,.30,4,x)
    for side,x in (('west',-8),('east',8)):
        instance(m.name,'stone',(x,0,0),(0,90,0),ident='sponza-upper-arches-'+side)

    # Exterior aisle walls, upper clerestory and simple open-center roofs.
    for side,z in (('north',-6),('south',6)):
        for i,(x0,x1) in enumerate(((-11.4,-4),(-4,4),(4,11.4))):
            m=mesh(f'aisle-wall-{side}-{i}',((x0+x1)/2,5.25,z))
            inside=z+(.1 if z<0 else -.1)
            for y0,y1 in ((0,3),(3,5.1),(5.3,8.4),(8.4,10.5)):
                m.face([(x0,y0,inside),(x1,y0,inside),(x1,y1,inside),(x0,y1,inside)],(0,0,1 if z<0 else -1),center=True)
            instance(m.name,'plaster');collider(m.name,(x0,0,z-.1),(x1,10.5,z+.1))
    for side,z in (('north',-3.1),('south',3.1)):
        for i,(x0,x1) in enumerate(((-8,0),(0,8))):
            m=mesh(f'clerestory-{side}-{i}',((x0+x1)/2,13.45,z))
            m.face([(x0,11.2,z),(x1,11.2,z),(x1,15.7,z),(x0,15.7,z)],(0,0,1 if z<0 else -1))
            instance(m.name,'plaster')
            m=mesh(f'roof-{side}-{i}',((x0+x1)/2,16.5,z))
            sign=-1 if z<0 else 1
            m.face([(x0,15.7,z),(x1,15.7,z),(x1,17.8,sign*5),(x0,17.8,sign*5)],(0,1,0))
            m.face([(x0,17.8,sign*5),(x1,17.8,sign*5),(x1,16.5,sign*6.8),(x0,16.5,sign*6.8)],(0,1,0))
            inner=sign*2.78
            m.face([(x0,15.55,inner),(x1,15.55,inner),(x1,15.55,z),(x0,15.55,z)],(0,-1,0))
            m.face([(x0,15.55,inner),(x1,15.55,inner),(x1,15.85,inner),(x0,15.85,inner)],(0,0,-sign))
            instance(m.name,'roof')
    for side,x in (('west',-8),('east',8)):
        m=mesh('clerestory-'+side,(x,13.45,0))
        m.face([(x,11.2,-3.1),(x,15.7,-3.1),(x,15.7,3.1),(x,11.2,3.1)],(1 if x<0 else -1,0,0))
        instance(m.name,'plaster')
        m=mesh('roof-'+side,(x,16.5,0))
        X=x+(-3.4 if x<0 else 3.4)
        m.face([(x,15.7,-3.1),(X,17.2,-3.1),(X,17.2,3.1),(x,15.7,3.1)],(0,1,0))
        inner=x+(.32 if x<0 else -.32)
        m.face([(x,15.55,-3.1),(inner,15.55,-3.1),(inner,15.55,3.1),(x,15.55,3.1)],(0,-1,0))
        m.face([(inner,15.55,-3.1),(inner,15.85,-3.1),(inner,15.85,3.1),(inner,15.55,3.1)],(1 if x<0 else -1,0,0))
        instance(m.name,'roof')

    # End walls leave true ground doorways and full-height stair portals.
    for side,x,ranges,headers in (
        ('east',11.4,[(-6,-1.25),(1.25,2.8),(5.2,6)],[(-1.25,1.25,3.1),(2.8,5.2,7.4)]),
        ('west',-11.4,[(-6,-5.2),(-2.8,-2.5),(2.5,6)],[(-2.5,2.5,4.6),(-5.2,-2.8,7.4)])):
        m=mesh('end-wall-'+side,(x,5.25,0))
        inside=x+(.1 if x<0 else -.1)
        for i,(z0,z1,y0) in enumerate([(a,b,0) for a,b in ranges]+headers):
            m.face([(inside,y0,z0),(inside,10.5,z0),(inside,10.5,z1),(inside,y0,z1)],(1 if x<0 else -1,0,0))
            collider(f'end-wall-{side}-{i}',(x-.1,y0,z0),(x+.1,10.5,z1))
        instance(m.name,'brick')

    for side,x0,x1,z0,z1,ceiling in (('entry',11.4,19.5,-1.25,1.25,3.8),('niche',-15.5,-11.4,-2.5,2.5,4.9)):
        m=mesh(side+'-floor',((x0+x1)/2,0,0));m.horizontal(x0,x1,z0,z1,0,3,2)
        instance(m.name,'floor');collider(m.name,(x0,-.2,z0),(x1,0,z1))
        m=mesh(side+'-shell',((x0+x1)/2,ceiling/2,0))
        for z,normal in ((z0,(0,0,1)),(z1,(0,0,-1))):
            inside=z+normal[2]*.1
            m.face([(x0,0,inside),(x1,0,inside),(x1,ceiling,inside),(x0,ceiling,inside)],normal)
            collider(f'{side}-wall-{z}',(x0,0,z-.1),(x1,ceiling,z+.1))
        X=x0 if side=='niche' else x1
        inside=X+(.1 if side=='niche' else -.1)
        m.face([(inside,0,z0),(inside,ceiling,z0),(inside,ceiling,z1),(inside,0,z1)],(1 if side=='niche' else -1,0,0))
        collider(side+'-back',(X-.1,0,z0),(X+.1,ceiling,z1))
        m.horizontal(x0,x1,z0,z1,ceiling,1,1,False)
        collider(side+'-ceiling',(x0,ceiling,z0),(x1,ceiling+.2,z1))
        instance(m.name,'brick')

    stair_routes={}
    for side,sign in (('east',1),('west',-1)):
        # Reflection in X and Z preserves winding; both stairwells are identical.
        def p(x,y,z):return(sign*x,y,sign*z)
        def stair_box(name,lo,hi):
            a=p(*lo);b=p(*hi);collider(side+'-'+name,tuple(min(v,w) for v,w in zip(a,b)),tuple(max(v,w) for v,w in zip(a,b)))
        m=mesh('stairs-'+side,p(13.65,2.65,4.0));rise=5.3/22
        for flight in range(2):
            z0,z1=(2.9,3.9) if flight==0 else (4.1,5.1)
            for i in range(11):
                xa,xb=(11.4+i*.3,11.4+(i+1)*.3) if flight==0 else (14.7-(i+1)*.3,14.7-i*.3)
                y=(i+1+flight*11)*rise;old=y-rise
                m.face([p(xa,y,z0),p(xb,y,z0),p(xb,y,z1),p(xa,y,z1)],(0,1,0),center=True)
                edge=xa if flight==0 else xb
                m.face([p(edge,old,z0),p(edge,y,z0),p(edge,y,z1),p(edge,old,z1)],(-sign if flight==0 else sign,0,0))
                stair_box(f'step-{flight}-{i}',(xa,-.1,z0),(xb,y,z1))
        m.face([p(14.7,2.65,2.9),p(15.9,2.65,2.9),p(15.9,2.65,5.1),p(14.7,2.65,5.1)],(0,1,0))
        stair_box('mid-landing',(14.7,-.1,2.9),(15.9,2.65,5.1))
        instance(m.name,'stone')
        m=mesh('stair-shell-'+side,p(13.65,3.8,4.0))
        for z,normal in ((2.8,(0,0,sign)),(5.2,(0,0,-sign))):
            inside=z+(.1 if z==2.8 else -.1)
            m.face([p(11.4,0,inside),p(15.9,0,inside),p(15.9,7.6,inside),p(11.4,7.6,inside)],normal)
            stair_box(f'outer-wall-{z}',(11.4,0,z-.1),(15.9,7.6,z+.1))
        m.face([p(15.9,0,2.8),p(15.9,7.6,2.8),p(15.9,7.6,5.2),p(15.9,0,5.2)],(-sign,0,0))
        stair_box('back-wall',(15.9,0,2.8),(16.1,7.6,5.2))
        # Solid divider masks the cheap stair side faces and prevents shortcut falls.
        for z,normal in ((3.9,(0,0,-sign)),(4.1,(0,0,sign))):
            m.face([p(11.4,0,z),p(14.7,0,z),p(14.7,6.4,z),p(11.4,6.4,z)],normal)
        m.face([p(11.4,6.4,3.9),p(14.7,6.4,3.9),p(14.7,6.4,4.1),p(11.4,6.4,4.1)],(0,1,0))
        m.face([p(14.7,0,3.9),p(14.7,6.4,3.9),p(14.7,6.4,4.1),p(14.7,0,4.1)],(sign,0,0))
        stair_box('divider',(11.4,0,3.9),(14.7,6.4,4.1))
        m.face([p(11.4,7.6,2.8),p(15.9,7.6,2.8),p(15.9,7.6,5.2),p(11.4,7.6,5.2)],(0,-1,0))
        stair_box('ceiling',(11.4,7.6,2.8),(15.9,7.8,5.2))
        instance(m.name,'brick')
        stair_routes[side]=[list(p(*v)) for v in ((10.9,0,3.4),(15.25,2.65,3.4),
            (15.25,2.65,4.6),(10.9,5.3,4.6))]

    # The trim derivative repeats horizontally and maps its moulding strip once
    # vertically. Keep caps on one row rather than wrap across unrelated rows.
    trim_meshes={i['model'].removeprefix('mesh-sponza-') for i in instances if i['material']=='mat-sponza-trim'}
    for name in trim_meshes:
        m=meshes[name];low=min(v[1] for v in m.vertices);high=max(v[1] for v in m.vertices)
        m.uvs=[(uv[0],(v[1]-low)/(high-low)) for v,uv in zip(m.vertices,m.uvs)]
    return meshes,{'version':1,'assets':[{'id':'mesh-sponza-'+name,'uri':'assets/sponza/models/'+name+'.obj'} for name in meshes],
        'instances':instances,'colliders':colliders,'stair_routes':stair_routes,
        'landmarks':{'entry':[18.2,0,0],'upper_control':[-5.8,6.3,-5.5],'lower_objective':[-14.7,.9,0],
            'gallery_y':5.3,'ground_loop':[[10.2,0,4.7],[-10.2,0,4.7],[-10.2,0,-4.7],[10.2,0,-4.7]],
            'upper_loop':[[10.2,5.3,4.7],[-10.2,5.3,4.7],[-10.2,5.3,-4.7],[10.2,5.3,-4.7]],
            'reference_bounds':{'atrium_x':[-8,8],'atrium_z':[-3.1,3.1],'roof_y':17.8}},
        'source_reference':'NewSponza_Main_glTF_003.gltf; measured and rebuilt silhouettes; see content credits'}

def export(meshes,manifest):
    sys.path.insert(0,str(ROOT))
    from tools.compile_level import read_obj
    (DEST/'models').mkdir(parents=True,exist_ok=True)
    records={}
    for name,mesh in meshes.items():
        path=DEST/'models'/(name+'.obj');mesh.export(path);cooked=read_obj(path,textured=True)
        records['mesh-sponza-'+name]={'vertices':len(cooked['vertices']),'triangles':len(cooked['indices'])//3,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    totals={'vertices':sum(records[i['model']]['vertices'] for i in manifest['instances']),
        'triangles':sum(records[i['model']]['triangles'] for i in manifest['instances']),
        'models':len(manifest['instances']),'colliders':len(manifest['colliders'])}
    manifest['geometry_report']={'instanced':totals,'meshes':records,
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (DEST/'geometry.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(totals,indent=2),flush=True)
    return totals

def blender_save(meshes,manifest):
    import bpy
    from mathutils import Vector
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    collection=bpy.data.collections.new('Sponza | editable low-poly architecture');bpy.context.scene.collection.children.link(collection)
    palette={'stone':(.56,.52,.44,1),'trim':(.64,.60,.52,1),'plaster':(.61,.61,.55,1),
        'brick':(.39,.34,.28,1),'wood':(.25,.16,.08,1),'floor':(.39,.40,.38,1),'relief':(.48,.45,.37,1),'roof':(.31,.25,.20,1)}
    mats={}
    for name,color in palette.items():
        mat=bpy.data.materials.new('mat-sponza-'+name);mat.diffuse_color=color;mat.use_backface_culling=True
        mat['dl64_material_id']='mat-sponza-'+name;mats['mat-sponza-'+name]=mat
    texture_manifest=DEST/'textures.json'
    if texture_manifest.is_file():
        for definition in json.loads(texture_manifest.read_text())['materials']:
            mat=mats[definition['id']];mat.use_nodes=True
            node=mat.node_tree.nodes.new('ShaderNodeTexImage')
            node.image=bpy.data.images.load(str(ROOT/'content'/definition['texture']['uri']),check_existing=True)
            node.image.filepath='//../content/'+definition['texture']['uri']
            node.interpolation='Linear';mat.node_tree.nodes.active=node
            mat.node_tree.links.new(node.outputs['Color'],mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
    for instance in manifest['instances']:
        source=meshes[instance['model'].removeprefix('mesh-sponza-')]
        data=bpy.data.meshes.get('dl64:'+source.name)
        if data is None:
            data=bpy.data.meshes.new('dl64:'+source.name)
            data.from_pydata([(x,-z,y) for x,y,z in source.vertices],[],[source.indices[i:i+3] for i in range(0,len(source.indices),3)])
            data.uv_layers.new(name='UVMap')
            for poly in data.polygons:
                poly.use_smooth=True
                for loop in poly.loop_indices:data.uv_layers[0].data[loop].uv=source.uvs[data.loops[loop].vertex_index]
            data.normals_split_custom_set_from_vertices([(x,-z,y) for x,y,z in source.normals])
            data.materials.append(mats[instance['material']])
        obj=bpy.data.objects.new(instance['id'],data);collection.objects.link(obj)
        x,y,z=instance['transform']['position'];obj.location=(x,-z,y)
        obj.rotation_euler.z=math.radians(instance['transform']['rotation'][1])
        obj['dl64_asset_id']=instance['model'];obj['dl64_material_id']=instance['material']
    collider_collection=bpy.data.collections.new('Collision | toggle wire proxies');bpy.context.scene.collection.children.link(collider_collection)
    for row in manifest['colliders']:
        obj=bpy.data.objects.new(row['id'],None);obj.empty_display_type='CUBE';collider_collection.objects.link(obj)
        x,y,z=row['transform']['position'];a,b,c=row['collider']['half_size'];obj.location=(x,-z,y);obj.scale=(a,c,b)
    collider_collection.hide_viewport=True;collider_collection.hide_render=True
    camera_data=bpy.data.cameras.new('Courtyard reference camera');camera=bpy.data.objects.new('Courtyard reference camera',camera_data)
    bpy.context.scene.collection.objects.link(camera);bpy.context.scene.camera=camera
    camera.location=(10.5,0,1.75);target=Vector((-1.5,0,6.3));camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler();camera_data.lens=20
    scene=bpy.context.scene;scene.render.engine='BLENDER_WORKBENCH';scene.render.resolution_x=800;scene.render.resolution_y=600;scene.render.resolution_percentage=100
    scene.display.shading.light='STUDIO';scene.display.shading.color_type='MATERIAL';scene.display.shading.show_shadows=True;scene.display.shading.show_cavity=True
    scene.world.color=(.12,.15,.20)
    destination=ROOT/'art/sponza.blend';destination.parent.mkdir(exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(destination))
    output=ROOT/'build/sponza-authored-views';output.mkdir(exist_ok=True)
    for ident,position,target in (('courtyard-axis',(10.5,1.75,0),(-1.5,6.3,0)),('upper-gallery',(6.2,6.95,4.7),(-3.2,5.5,-1.5)),('east-stairs',(11.0,1.65,3.4),(15.5,3.0,3.4))):
        camera.location=(position[0],-position[2],position[1]);target=Vector((target[0],-target[2],target[1]))
        camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=str(output/(ident+'.png'));bpy.ops.render.render(write_still=True)
    scene.display.shading.color_type='TEXTURE'
    for ident,position,target in (('courtyard-textured',(10.5,1.75,0),(-1.5,6.3,0)),('upper-textured',(6.2,6.95,4.7),(-3.2,5.5,-1.5))):
        camera.location=(position[0],-position[2],position[1]);target=Vector((target[0],-target[2],target[1]))
        camera.rotation_euler=(target-camera.location).to_track_quat('-Z','Y').to_euler()
        scene.render.filepath=str(output/(ident+'.png'));bpy.ops.render.render(write_still=True)
    camera.location=(10.5,0,1.75);camera.rotation_euler=(Vector((-1.5,0,6.3))-camera.location).to_track_quat('-Z','Y').to_euler()
    bpy.ops.wm.save_as_mainfile(filepath=str(destination))

def blender_verify(meshes,manifest):
    """Read a saved .blend and compare its editable data with the cooked source."""
    import bpy
    from mathutils import Matrix
    # Hidden proxy collections are absent from Blender's dependency graph until
    # enabled. This verification process never saves these visibility changes.
    for collection in bpy.data.collections:collection.hide_viewport=False
    bpy.context.view_layer.update()
    worst_position=worst_uv=worst_normal=0.0
    worst_instance_matrix=worst_proxy_matrix=0.0
    shared={}
    for instance in manifest['instances']:
        obj=bpy.data.objects.get(instance['id'])
        if obj is None or obj.type!='MESH':raise ValueError('Missing saved architecture: '+instance['id'])
        expected=meshes[instance['model'].removeprefix('mesh-sponza-')]
        wanted_position=instance['transform']['position']
        x,y,z=wanted_position
        expected_matrix=Matrix.Translation((x,-z,y)) @ Matrix.Rotation(math.radians(instance['transform']['rotation'][1]),4,'Z')
        worst_instance_matrix=max(worst_instance_matrix,max(abs(obj.matrix_world[r][c]-expected_matrix[r][c]) for r in range(4) for c in range(4)))
        actual_position=(obj.location.x,obj.location.z,-obj.location.y)
        if max(abs(a-b) for a,b in zip(actual_position,wanted_position))>1e-5:raise ValueError('Saved instance position differs')
        if max(abs(v-1) for v in obj.scale)>1e-6:raise ValueError('Saved instance scale differs')
        if abs(obj.rotation_euler.z-math.radians(instance['transform']['rotation'][1]))>1e-6:raise ValueError('Saved instance rotation differs')
        if len(obj.data.vertices)!=len(expected.vertices):raise ValueError('Saved topology differs: '+instance['id'])
        if obj.data.materials[0].get('dl64_material_id')!=instance['material']:raise ValueError('Saved material differs')
        if instance['model'] in shared and obj.data!=shared[instance['model']]:raise ValueError('Repeated assets are not linked mesh data')
        shared[instance['model']]=obj.data
        for actual,wanted in zip(obj.data.vertices,expected.vertices):
            found=(actual.co.x,actual.co.z,-actual.co.y)
            worst_position=max(worst_position,max(abs(a-b) for a,b in zip(found,wanted)))
        for poly in obj.data.polygons:
            for index in poly.loop_indices:
                vertex=obj.data.loops[index].vertex_index
                uv=obj.data.uv_layers[0].data[index].uv
                worst_uv=max(worst_uv,max(abs(a-b) for a,b in zip(uv,expected.uvs[vertex])))
                normal=obj.data.corner_normals[index].vector
                found=(normal.x,normal.z,-normal.y)
                worst_normal=max(worst_normal,max(abs(a-b) for a,b in zip(found,expected.normals[vertex])))
    if worst_position>1e-5 or worst_uv>1e-5 or worst_normal>2e-4:raise ValueError(f'Saved attributes differ: {worst_position}, {worst_uv}, {worst_normal}')
    for row in manifest['colliders']:
        obj=bpy.data.objects.get(row['id'])
        if obj is None or obj.type!='EMPTY':raise ValueError('Missing saved collider proxy')
        x,y,z=row['transform']['position'];a,b,c=row['collider']['half_size']
        expected_matrix=Matrix.Translation((x,-z,y)) @ Matrix.Diagonal((a,c,b,1))
        worst_proxy_matrix=max(worst_proxy_matrix,max(abs(obj.matrix_world[r][c]-expected_matrix[r][c]) for r in range(4) for c in range(4)))
    if max(worst_instance_matrix,worst_proxy_matrix)>1e-5:raise ValueError('Saved instance or collider world bounds differ')
    result={'passed':True,'instances':len(manifest['instances']),'shared_mesh_datablocks':len(shared),
        'collision_proxies':len(manifest['colliders']),'max_position_error_m':worst_position,
        'max_uv_error':worst_uv,'max_normal_component_error':worst_normal,
        'max_instance_matrix_error':worst_instance_matrix,'max_proxy_matrix_error':worst_proxy_matrix,
        'blend_sha256':hashlib.sha256(Path(bpy.data.filepath).read_bytes()).hexdigest()}
    destination=ROOT/'build/sponza-blender-verification.json'
    destination.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--blend',action='store_true');mode.add_argument('--verify-blend',action='store_true')
    options=parser.parse_args(args);meshes,manifest=build()
    if options.verify_blend:blender_verify(meshes,manifest)
    else:
        export(meshes,manifest)
        if options.blend:blender_save(meshes,manifest)
