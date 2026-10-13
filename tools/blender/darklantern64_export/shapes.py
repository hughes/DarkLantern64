"""Small authoring shapes: one definition produces surfaces and collision boxes.

Coordinates are metres, Y up. Blender and the runtime consume the same output.
No Blender dependency, so dimensional and walkability checks run on the host.
"""
import math

from .core import require


def rectangular_prism(size, center):
    require(len(size)==3 and len(center)==3 and all(math.isfinite(v) for v in (*size,*center)), 'Prism dimensions must be finite XYZ vectors')
    require(all(.002 <= v <= 128 for v in size), 'Prism dimensions must be .002..128 metres')
    x,y,z = [c-s/2 for c,s in zip(center,size)]
    X,Y,Z = [c+s/2 for c,s in zip(center,size)]
    return [dict(points=points,normal=normal) for points,normal in (
        ([(x,y,z),(x,y,Z),(x,Y,Z),(x,Y,z)],(-1,0,0)),
        ([(X,y,Z),(X,y,z),(X,Y,z),(X,Y,Z)],(1,0,0)),
        ([(x,y,z),(X,y,z),(X,y,Z),(x,y,Z)],(0,-1,0)),
        ([(x,Y,Z),(X,Y,Z),(X,Y,z),(x,Y,z)],(0,1,0)),
        ([(X,y,z),(x,y,z),(x,Y,z),(X,Y,z)],(0,0,-1)),
        ([(x,y,Z),(X,y,Z),(X,Y,Z),(x,Y,Z)],(0,0,1))) ]


def staircase(p):
    count = p['steps']
    require(type(count) is int and 1 <= count <= 32, 'Steps per flight must be 1..32')
    for key in ('rise', 'tread', 'width', 'landing', 'gap', 'headroom'):
        require(type(p[key]) in (int, float) and math.isfinite(p[key]) and .05 <= p[key] <= 64,
                f'{key}: expected .05..64 metres')
    step = p['rise'] / (2 * count)
    require(step <= .28 + 1e-7, f'Each step rises {step:.3f} m; the controller supports at most 0.28 m. Add steps or reduce total rise')
    require(p['tread'] >= .2, 'Treads must be at least 0.20 m deep')
    require(p['width'] >= .4 and p['landing'] >= .4, 'Width and landing depth must be at least 0.40 m')
    x0, y0, z0 = p.get('origin', [-2.25, -2.65, -1.1])
    sign = p.get('direction', 1)
    require(sign in (-1, 1), 'Stair direction must be -1 or 1')
    run = count * p['tread']
    end = x0 + run
    far = end + p['landing']
    zfar = z0 + 2*p['width'] + p['gap']
    top = y0 + p['rise'] + p['headroom']
    tread_faces, shell_faces, boxes = [], [], {}

    def point(v):
        return (sign*v[0], v[1], sign*v[2])

    def face(target, points, normal, center=False):
        # Quad winding must survive the proper X/Z rotation used by the west stair.
        a,b,c = points[:3]
        u,v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        cross = (u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
        if sum(cross[i]*normal[i] for i in range(3)) < 0:
            points = list(reversed(points))
        target.append(dict(points=[point(v) for v in points], normal=point(normal), center=center))

    def box(name, lo, hi):
        a,b = point(lo),point(hi)
        boxes[name] = dict(center=[(v+w)/2 for v,w in zip(a,b)], half_size=[abs(w-v)/2 for v,w in zip(a,b)])

    for flight in range(2):
        za = z0 + flight*(p['width']+p['gap'])
        zb = za + p['width']
        for i in range(count):
            xa = x0+i*p['tread'] if flight == 0 else end-(i+1)*p['tread']
            xb = xa+p['tread']
            height = y0+(i+1+flight*count)*step
            face(tread_faces, [(xa,height,za),(xb,height,za),(xb,height,zb),(xa,height,zb)], (0,1,0), True)
            edge = xa if flight == 0 else xb
            face(tread_faces, [(edge,height-step,za),(edge,height,za),(edge,height,zb),(edge,height-step,zb)],
                 (-1 if flight == 0 else 1,0,0))
            box(f'step-{flight}-{i}', (xa,y0-.1,za), (xb,height,zb))
    middle = y0+p['rise']/2
    face(tread_faces, [(end,middle,z0),(far,middle,z0),(far,middle,zfar),(end,middle,zfar)], (0,1,0))
    box('mid-landing', (end,y0-.1,z0), (far,middle,zfar))
    for suffix,z,inside,normal in (('near',z0-.1,z0,(0,0,1)), ('far',zfar+.1,zfar,(0,0,-1))):
        face(shell_faces, [(x0,y0,inside),(far,y0,inside),(far,top,inside),(x0,top,inside)], normal)
        box('outer-wall-'+suffix, (x0,y0,z-.1), (far,top,z+.1))
    face(shell_faces, [(far,y0,z0-.1),(far,top,z0-.1),(far,top,zfar+.1),(far,y0,zfar+.1)], (-1,0,0))
    box('back-wall', (far,y0,z0-.1), (far+.2,top,zfar+.1))
    divider_top = y0+p['rise']+1.1
    a,b = z0+p['width'],z0+p['width']+p['gap']
    for z,normal in ((a,(0,0,-1)),(b,(0,0,1))):
        face(shell_faces, [(x0,y0,z),(end,y0,z),(end,divider_top,z),(x0,divider_top,z)], normal)
    face(shell_faces, [(x0,divider_top,a),(end,divider_top,a),(end,divider_top,b),(x0,divider_top,b)], (0,1,0))
    face(shell_faces, [(end,y0,a),(end,divider_top,a),(end,divider_top,b),(end,y0,b)], (1,0,0))
    box('divider', (x0,y0,a), (end,divider_top,b))
    face(shell_faces, [(x0,top,z0-.1),(far,top,z0-.1),(far,top,zfar+.1),(x0,top,zfar+.1)], (0,-1,0))
    box('ceiling', (x0,top,z0-.1), (far,top+.2,zfar+.1))
    return dict(treads=tread_faces, shell=shell_faces, boxes=boxes, step_rise=step)
