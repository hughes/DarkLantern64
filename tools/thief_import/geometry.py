"""Geometry adaptation: Z-up feet to Y-up metres, convex faces and box proxies."""
from __future__ import annotations

import math

METRES_PER_UNIT = 0.3048


def game_point(point):
    x, y, z = point
    return [x * METRES_PER_UNIT, z * METRES_PER_UNIT, -y * METRES_PER_UNIT]


def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def clean_polygon(points, uvs):
    """Remove redundant straight-edge vertices without changing a convex face.

    The source portal compiler splits edges at adjacent cells, leaving many
    collinear vertices. A naive triangle fan emits collapsed triangles.
    """
    corners = list(zip(points, uvs))
    changed = True
    while changed and len(corners) > 3:
        changed = False
        for i, (point, _) in enumerate(corners):
            a = sub(point, corners[i-1][0])
            b = sub(corners[(i+1) % len(corners)][0], point)
            if dot(a, a) < 1e-10 or dot(b, b) < 1e-10 or (
                    dot(cross(a, b), cross(a, b)) <= 1e-10 * max(dot(a, a), dot(b, b))
                    and dot(a, b) >= 0):
                del corners[i]
                changed = True
                break
    if len(corners) < 3:
        return None
    normal = cross(sub(corners[1][0], corners[0][0]), sub(corners[2][0], corners[0][0]))
    length = math.sqrt(dot(normal, normal))
    if length <= 1e-6:
        return None
    return [list(p) for p, _ in corners], [list(uv) for _, uv in corners], [v/length for v in normal]


def polygon_box(points, normal, thickness=0.04):
    """Exact thin box for axis-aligned rectangular faces; no concave bounds fill.

    Missing slopes and nonrectangular boundaries are recorded by the caller.
    Thickening extends away from the walkable cell, leaving floor height intact.
    """
    axis = max(range(3), key=lambda i: abs(normal[i]))
    if abs(normal[axis]) < 0.99999:
        return None
    flat_axes = [i for i in range(3) if i != axis]
    lower = [min(p[i] for p in points) for i in range(3)]
    upper = [max(p[i] for p in points) for i in range(3)]
    a, b = flat_axes
    area = sum(dot(cross(sub(points[i], points[0]), sub(points[i+1], points[0])), normal)
               for i in range(1, len(points)-1)) / 2
    rectangle_area = (upper[a]-lower[a]) * (upper[b]-lower[b])
    if rectangle_area < 1e-8 or abs(abs(area)-rectangle_area) > max(1e-6, rectangle_area*1e-5):
        return None
    center = [(lower[i]+upper[i])/2 for i in range(3)]
    half = [(upper[i]-lower[i])/2 for i in range(3)]
    center[axis] -= normal[axis] * thickness/2
    half[axis] = thickness/2
    if any(v < .001 for v in half):
        return None
    return center, half


def point_in_floor(point, polygon):
    """Convex containment in XZ, including polygon boundaries."""
    signs = []
    for i, a in enumerate(polygon):
        b = polygon[(i+1) % len(polygon)]
        signs.append((b[0]-a[0])*(point[2]-a[2])-(b[2]-a[2])*(point[0]-a[0]))
    return all(v >= -1e-6 for v in signs) or all(v <= 1e-6 for v in signs)


def floor_position(point, floors, max_drop=3.0):
    candidates = [face["points"][0][1] for face in floors
                  if face["normal"][1] > .99999
                  and -0.12 <= point[1]-face["points"][0][1] <= max_drop
                  and point_in_floor(point, face["points"])]
    return [point[0], max(candidates), point[2]] if candidates else None


def obj_payload(faces, origin):
    lines, offset = ["# Thief portal surfaces; generated locally from installed mission"], 0
    for normal_index, face in enumerate(faces, 1):
        for point in face["points"]:
            lines.append("v " + " ".join(format(point[i]-origin[i], ".9g") for i in range(3)))
        for uv in face["uvs"]:
            lines.append("vt " + " ".join(format(v, ".9g") for v in uv))
        lines.append("vn " + " ".join(format(v, ".9g") for v in face["normal"]))
        for i in range(1, len(face["points"])-1):
            indices = [offset + j + 1 for j in (0, i, i+1)]
            lines.append("f " + " ".join(f"{j}/{j}/{normal_index}" for j in indices))
        offset += len(face["points"])
    return "\n".join(lines) + "\n"
