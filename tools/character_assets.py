"""Cook version-1 one-influence character sources for the portable N64 sampler.

All source transforms are rigid, local, parent-first, RH Y-up metres, +Z forward.
Quaternions are XYZW. Mesh positions/normals are model-space bind data; UV V
grows upward in the source and is converted once to the existing target format.
Uniform samples include both endpoints. This deliberately simple format uses
int16 quaternions and 1/4096 metre translations, removes rest tracks and stores
constant tracks once. There is no stream decoder or per-actor clip allocation.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re

FORMAT = "RH_Y_UP_Z_FORWARD_METERS"
TRANSLATION_SCALE = 1 / 4096
MAX_BONES = 32


def require(value, message):
    if not value:
        raise ValueError(message)


def ident(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value),
            label + ": expected a stable ASCII identifier")
    return value


def vec(value, label, n=3, limit=1024):
    require(isinstance(value, list) and len(value) == n and
            all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= limit for v in value),
            label + f": expected {n} finite components in +/-{limit}")
    return [float(v) for v in value]


def quat(value, label):
    values = vec(value, label, 4, 1.001)
    length = math.sqrt(sum(v * v for v in values))
    require(abs(length - 1) < .001, label + ": quaternion must be normalized XYZW")
    return [v / length for v in values]


def qmul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z]


def qrotate(q, v):
    return qmul(qmul(q, [*v, 0]), [-q[0], -q[1], -q[2], q[3]])[:3]


def nlerp(a, b, t):
    sign = -1 if sum(x*y for x, y in zip(a, b)) < 0 else 1
    value = [x+(sign*y-x)*t for x, y in zip(a, b)]
    length = math.sqrt(sum(v*v for v in value))
    return [v/length for v in value]


def compose(a, b):
    return {"translation": [x+y for x, y in zip(a["translation"], qrotate(a["rotation"], b["translation"]))],
            "rotation": qmul(a["rotation"], b["rotation"])}


def inverse(t):
    q = [-t["rotation"][i] if i < 3 else t["rotation"][i] for i in range(4)]
    return {"translation": qrotate(q, [-v for v in t["translation"]]), "rotation": q}


def point(t, p):
    return [x+y for x, y in zip(t["translation"], qrotate(t["rotation"], p))]


def globals_for(bones, transforms):
    result = []
    for bone, local in zip(bones, transforms):
        result.append(local if bone["parent"] == -1 else compose(result[bone["parent"]], local))
    return result


def validate(source):
    require(isinstance(source, dict) and type(source.get("version")) is int and source["version"] == 1,
            "Character version must be 1")
    ident(source.get("id"), "Character id")
    ident(source.get("skeleton_id"), "Skeleton id")
    require(source.get("coordinates") == FORMAT, "Unsupported character coordinates")
    bones = source.get("bones")
    require(isinstance(bones, list) and 1 <= len(bones) <= MAX_BONES, "Character needs 1-32 bones")
    names = set()
    for index, bone in enumerate(bones):
        require(isinstance(bone, dict), "Bone must be an object")
        name = ident(bone.get("id"), "Bone id")
        require(name not in names, "Duplicate bone id")
        names.add(name)
        parent = bone.get("parent")
        require(type(parent) is int and (-1 if index == 0 else 0) <= parent < index,
                name + ": bones need one root and parent-first order")
        bone["translation"] = vec(bone.get("translation"), name + ".translation", limit=7.99)
        bone["rotation"] = quat(bone.get("rotation"), name + ".rotation")
        require("scale" not in bone, name + ": animated/bone scale is unsupported")
    mesh = source.get("mesh")
    require(isinstance(mesh, dict), "Character requires mesh")
    require("weights" not in mesh and "joint_weights" not in mesh,
            "Character source uses one joint index per vertex; extra weights must be rejected or reduced explicitly by the exporter")
    vertices = mesh.get("vertices")
    require(isinstance(vertices, list) and 3 <= len(vertices) <= 4096, "Character needs 3-4096 vertices")
    for i, vertex in enumerate(vertices):
        vec(vertex, f"vertex {i}")
    normals, uvs, joints, indices = (mesh.get(k) for k in ("normals", "uvs", "joints", "indices"))
    require(isinstance(normals, list) and len(normals) == len(vertices), "Character needs one normal per vertex")
    for i, normal in enumerate(normals):
        normal = vec(normal, f"normal {i}", limit=1.001)
        length = math.sqrt(sum(v*v for v in normal))
        require(abs(length-1) < .001, f"normal {i}: must be unit length")
        normals[i] = [v / length for v in normal]
    require(isinstance(uvs, list) and len(uvs) == len(vertices), "Character needs one UV per vertex")
    for uv in uvs:
        vec(uv, "UV", 2)
    require(isinstance(joints, list) and len(joints) == len(vertices) and
            all(type(j) is int and 0 <= j < len(bones) for j in joints), "Character needs one valid joint per vertex")
    require(isinstance(indices, list) and 3 <= len(indices) <= 12288 and len(indices) % 3 == 0 and
            all(type(i) is int and 0 <= i < len(vertices) for i in indices), "Invalid character triangle indices")
    for i in range(0, len(indices), 3):
        a, b, c = (vertices[j] for j in indices[i:i+3])
        u, v = ([p-q for p, q in zip(p2, a)] for p2 in (b, c))
        require(sum(x*x for x in (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])) > 1e-16,
                "Degenerate character triangle")
    clips = source.get("clips")
    require(isinstance(clips, list) and 1 <= len(clips) <= 32, "Character needs 1-32 clips")
    names = set()
    for clip in clips:
        require(isinstance(clip, dict), "Clip must be an object")
        name = ident(clip.get("id"), "Clip id")
        require(name not in names, "Duplicate clip id")
        names.add(name)
        require(type(clip.get("loop")) is bool, name + ": loop must be boolean")
        fps = clip.get("fps")
        require(type(fps) in (int, float) and math.isfinite(fps) and 1 <= fps <= 120, name + ": invalid fps")
        frames = clip.get("frames")
        require(isinstance(frames, list) and 2 <= len(frames) <= 4096, name + ": expected 2-4096 endpoint-inclusive samples")
        duration = (len(frames)-1)/fps
        require(duration <= 120, name + ": duration exceeds 120 seconds")
        stride = clip.get("stride_length", 0)
        require(type(stride) in (int, float) and math.isfinite(stride) and 0 <= stride <= 32, name + ": invalid stride_length")
        for frame in frames:
            require(isinstance(frame, dict), name + ": frame must be an object")
            for key, width in (("translations", 3), ("rotations", 4)):
                values = frame.get(key)
                require(isinstance(values, list) and len(values) == len(bones), name + ": each frame needs all bones")
                for b, value in enumerate(values):
                    values[b] = quat(value, name) if width == 4 else vec(value, name, limit=7.99)
            require("scales" not in frame, name + ": animated scale is unsupported")
        if clip["loop"]:
            for i in range(len(bones)):
                require(max(abs(a-b) for a, b in zip(frames[0]["translations"][i], frames[-1]["translations"][i])) < .0001 and
                        abs(sum(a*b for a, b in zip(frames[0]["rotations"][i], frames[-1]["rotations"][i]))) > .99999,
                        name + ": looping clips must repeat the first pose at the endpoint")
        events = clip.get("events", [])
        require(isinstance(events, list) and len(events) <= 256, name + ": too many events")
        seen_events = set()
        for event in events:
            require(isinstance(event, dict) and event.get("id") in ("foot-left", "foot-right"), name + ": unsupported semantic event")
            timestamp = event.get("time")
            require(type(timestamp) in (int, float) and math.isfinite(timestamp) and 0 <= timestamp < duration,
                    name + ": event time must be in [0,duration); omit duplicated loop endpoint")
            key = (event["id"], round(timestamp/duration*65535))
            require(not clip["loop"] or key[1] < 65535, name + ": event rounds to the loop endpoint; move it earlier or use phase zero")
            require(key not in seen_events, name + ": duplicate quantized event")
            seen_events.add(key)
    sockets = source.get("sockets", [])
    require(isinstance(sockets, list) and len(sockets) <= 32, "Character supports at most 32 sockets")
    names = set()
    for socket in sockets:
        require(isinstance(socket, dict), "Socket must be an object")
        name = ident(socket.get("id"), "Socket id")
        require(name not in names, "Duplicate socket id")
        names.add(name)
        require(type(socket.get("bone")) is int and 0 <= socket["bone"] < len(bones), "Invalid socket bone")
        socket["translation"] = vec(socket.get("translation"), name + ".translation", limit=7.99)
        socket["rotation"] = quat(socket.get("rotation"), name + ".rotation")
    head = source.get("head_bone", next((i for i, b in enumerate(bones) if b["id"] == "head"), -1))
    require(type(head) is int and -1 <= head < len(bones), "Invalid head_bone")
    source["head_bone"] = head
    return source


def source_pose(clip, phase):
    p = max(0, min(1, phase))*(len(clip["frames"])-1)
    a = min(int(p), len(clip["frames"])-2)
    t, f0, f1 = p-a, clip["frames"][a], clip["frames"][a+1]
    return [{"translation": [x+(y-x)*t for x, y in zip(v0, v1)], "rotation": nlerp(q0, q1, t)}
            for v0, v1, q0, q1 in zip(f0["translations"], f1["translations"], f0["rotations"], f1["rotations"])]


def cooked_pose(bones, clip, phase):
    result = [{k: list(b[k]) for k in ("translation", "rotation")} for b in bones]
    p = max(0, min(1, phase))*(clip["sample_count"]-1)
    a, t = min(int(p), clip["sample_count"]-2), p-min(int(p), clip["sample_count"]-2)
    for track in clip["tracks"]:
        n = 4 if track["channel"] == 0 else 3
        offset0 = 0 if track["sample_count"] == 1 else a*n
        offset1 = 0 if track["sample_count"] == 1 else (a+1)*n
        scale = 1/32767 if n == 4 else clip["translation_scale"]
        v0, v1 = ([v*scale for v in track["values"][o:o+n]] for o in (offset0, offset1))
        if n == 4:
            # Match the runtime: interpolate the quantized endpoints, then
            # normalize the result once (including at exact key times).
            result[track["bone"]]["rotation"] = nlerp(v0, v1, t)
        else:
            result[track["bone"]]["translation"] = [x+(y-x)*t for x, y in zip(v0, v1)]
    return result


def encode_clip(bones, clip):
    tracks, omitted, constants = [], 0, 0
    for bone_index, bone in enumerate(bones):
        for channel, key, restkey, scale in ((0, "rotations", "rotation", 32767), (1, "translations", "translation", 4096)):
            values, previous = [], bone[restkey]
            for frame in clip["frames"]:
                value = frame[key][bone_index]
                if channel == 0 and sum(a*b for a, b in zip(previous, value)) < 0:
                    value = [-v for v in value]
                previous = value
                values.append([max(-32767, min(32767, round(v*scale))) for v in value])
            rest = [round(v*scale) for v in bone[restkey]]
            if all(v == rest for v in values):
                omitted += 1
                continue
            constant = all(v == values[0] for v in values)
            constants += int(constant)
            tracks.append({"bone": bone_index, "channel": channel, "sample_count": 1 if constant else len(values),
                           "values": [c for row in (values[:1] if constant else values) for c in row]})
    duration = (len(clip["frames"])-1)/clip["fps"]
    events = sorted(({"phase": round(e["time"]/duration*65535), "kind": int(e["id"] == "foot-right")}
                     for e in clip.get("events", [])), key=lambda e: (e["phase"], e["kind"]))
    return {"id": clip["id"], "duration": duration, "stride_length": clip.get("stride_length", 0),
            "translation_scale": TRANSLATION_SCALE, "sample_count": len(clip["frames"]), "loop": clip["loop"],
            "tracks": tracks, "events": events, "omitted_rest_tracks": omitted, "constant_tracks": constants}


def deformation_error(source, clip, cooked, inverse_bind):
    maximum_vertex, maximum_socket, sum_squared, count = 0., 0., 0., 0
    extrema_min, extrema_max = [math.inf]*3, [-math.inf]*3
    # Every source key and midpoint; error is measured in model-space metres.
    samples = (len(clip["frames"])-1)*2+1
    for i in range(samples):
        phase = i/(samples-1)
        reference = globals_for(source["bones"], source_pose(clip, phase))
        decoded = globals_for(source["bones"], cooked_pose(source["bones"], cooked, phase))
        ref_skin = [compose(a, b) for a, b in zip(reference, inverse_bind)]
        dst_skin = [compose(a, b) for a, b in zip(decoded, inverse_bind)]
        for vertex, joint in zip(source["mesh"]["vertices"], source["mesh"]["joints"]):
            a, b = point(ref_skin[joint], vertex), point(dst_skin[joint], vertex)
            squared = sum((x-y)**2 for x, y in zip(a, b))
            maximum_vertex = max(maximum_vertex, math.sqrt(squared))
            sum_squared += squared
            count += 1
            for axis in range(3):
                extrema_min[axis] = min(extrema_min[axis], a[axis], b[axis])
                extrema_max[axis] = max(extrema_max[axis], a[axis], b[axis])
        for socket in source.get("sockets", []):
            a = point(reference[socket["bone"]], socket["translation"])
            b = point(decoded[socket["bone"]], socket["translation"])
            maximum_socket = max(maximum_socket, math.sqrt(sum((x-y)**2 for x, y in zip(a, b))))
    return {"tested_pose_samples": samples, "max_vertex_error_m": maximum_vertex,
            "rms_vertex_error_m": math.sqrt(sum_squared/count), "max_socket_error_m": maximum_socket,
            "bounds_min": extrema_min, "bounds_max": extrema_max,
            "error_scope": "All source keys and their midpoints; sampled evidence, not a continuous-time error bound."}


def clip_sizes(bones, clip, cooked):
    payload = sum(len(t["values"])*2 for t in cooked["tracks"])
    return {"dense_float_key_bytes": len(bones)*len(clip["frames"])*7*4,
            "dense_int16_key_bytes": len(bones)*len(clip["frames"])*7*2,
            "constant_stripped_key_bytes": payload,
            "track_descriptor_bytes_n64": len(cooked["tracks"])*8,
            "event_bytes_n64": len(cooked["events"])*4,
            "clip_descriptor_bytes_n64": 36,
            "omitted_rest_tracks": cooked["omitted_rest_tracks"], "constant_tracks": cooked["constant_tracks"],
            "animated_tracks": sum(t["sample_count"] != 1 for t in cooked["tracks"])}


def cook_source(source, *, compare_15hz=True):
    source = validate(copy.deepcopy(source))
    bind_global = globals_for(source["bones"], source["bones"])
    inverse_bind = [inverse(t) for t in bind_global]
    bones = [{**bone, "inverse_bind": inv} for bone, inv in zip(source["bones"], inverse_bind)]
    clips, reports = [], []
    for clip in source["clips"]:
        cooked = encode_clip(bones, clip)
        clips.append(cooked)
        report = {"id": clip["id"], "fps": clip["fps"], "duration": cooked["duration"],
                  "sample_count": cooked["sample_count"], **clip_sizes(bones, clip, cooked),
                  "quantization_error": deformation_error(source, clip, cooked, inverse_bind)}
        if compare_15hz and clip["fps"] > 15:
            reduced = copy.deepcopy(clip)
            intervals = max(1, round(cooked["duration"]*15))
            reduced["fps"] = intervals/cooked["duration"]
            poses = [source_pose(clip, i/intervals) for i in range(intervals+1)]
            reduced["frames"] = [{"translations": [p["translation"] for p in pose],
                                  "rotations": [p["rotation"] for p in pose]} for pose in poses]
            encoded = encode_clip(bones, reduced)
            report["comparison_15hz"] = {"fps": reduced["fps"], "sample_count": intervals+1,
                                          **clip_sizes(bones, reduced, encoded),
                                          "error": deformation_error(source, clip, encoded, inverse_bind)}
        reports.append(report)
    # Hierarchy path lengths bound every possible local rotation, including head
    # overlays and blends, unlike the sampled animation AABB. Root origin sphere
    # is intentionally conservative for the first CPU reference path.
    max_lengths = []
    for i, bone in enumerate(bones):
        length = max(math.sqrt(sum(v*v for v in frame["translations"][i]))
                     for clip in source["clips"] for frame in clip["frames"])
        length = max(length, math.sqrt(sum(v*v for v in bone["translation"]))) + math.sqrt(3)*TRANSLATION_SCALE
        max_lengths.append(length + (max_lengths[bone["parent"]] if bone["parent"] >= 0 else 0))
    radius = max(max_lengths[j] + math.sqrt(sum(v*v for v in point(inverse_bind[j], p)))
                 for p, j in zip(source["mesh"]["vertices"], source["mesh"]["joints"])) + .001
    mesh = copy.deepcopy(source["mesh"])
    mesh["uvs"] = [[u, 1-v] for u, v in mesh["uvs"]]
    cross = sum(len({mesh["joints"][j] for j in mesh["indices"][i:i+3]}) != 1 for i in range(0, len(mesh["indices"]), 3))
    connections = {}
    for offset in range(0, len(mesh["indices"]), 3):
        triangle = mesh["indices"][offset:offset+3]
        owners = tuple(sorted({mesh["joints"][i] for i in triangle}))
        if len(owners) > 1:
            connection = connections.setdefault(owners, {"triangles": 0, "vertices": set()})
            connection["triangles"] += 1
            connection["vertices"].update(triangle)
    position_keys = [tuple(p) for p in mesh["vertices"]]
    joint_keys = [(p, j) for p, j in zip(position_keys, mesh["joints"])]
    normal_keys = [(p, tuple(round(n*127) for n in normal)) for p, normal in zip(joint_keys, mesh["normals"])]
    attribute_keys = [(p, tuple(uv)) for p, uv in zip(normal_keys, mesh["uvs"])]
    report = {"version": 1, "id": source["id"], "skeleton_id": source["skeleton_id"],
              "bones": len(bones), "vertices": len(mesh["vertices"]), "triangles": len(mesh["indices"])//3,
              "cross_joint_triangles": cross, "clips": reports,
              "single_joint_triangles": len(mesh["indices"])//3-cross,
              "unique_bind_positions": len({tuple(p) for p in mesh["vertices"]}),
              "vertex_attribute_splits": {"unique_positions": len(set(position_keys)),
                                           "with_joint_identity": len(set(joint_keys)),
                                           "with_quantized_normals": len(set(normal_keys)),
                                           "with_texture_coordinates": len(set(attribute_keys)),
                                           "note": "Cumulative distinct attribute tuples, before any target cache scheduling. A shared position may still require separate vertices for normals or UV seams."},
              "joint_connections": [{"bones": [bones[j]["id"] for j in owners], "triangles": value["triangles"],
                                     "referenced_vertices": len(value["vertices"])} for owners, value in connections.items()],
              "cross_joint_skinning_note": "Triangles may reference vertices owned by different bones. Every vertex still has exactly one influence; triangles join already-deformed vertices without multi-weight interpolation.",
              "encoded_key_bytes": sum(r["constant_stripped_key_bytes"] for r in reports),
              "dense_float_key_bytes": sum(r["dense_float_key_bytes"] for r in reports),
              "dense_int16_key_bytes": sum(r["dense_int16_key_bytes"] for r in reports),
              "geometry_bytes": len(mesh["vertices"])*23+len(mesh["indices"])*2,
              "joint_index_bytes": len(mesh["joints"]), "skeleton_descriptor_bytes_n64": len(bones)*64,
              "asset_descriptor_bytes_n64": 52,
              "socket_descriptor_bytes_n64": len(source.get("sockets", []))*36,
              "clip_and_track_descriptor_bytes_n64": sum(r["track_descriptor_bytes_n64"]+r["event_bytes_n64"]+r["clip_descriptor_bytes_n64"] for r in reports),
              "bounds_center": [0, 0, 0], "bounds_radius": radius,
              "bounds_policy": "Conservative hierarchy path-length sphere includes arbitrary rigid rotation, blending and head attention.",
              "compression_policy": "Source sample rates preserved; int16 XYZW and XYZ, rest-channel removal and single-sample constants. 15 Hz is comparison only.",
              "memory_note": "Immutable keys and geometry shared per asset. Target descriptor estimates exclude strings, final linker alignment, pose scratch, per-actor state and renderer caches; measure target sizeof separately."}
    result = {"version": 1, "id": source["id"], "skeleton_id": source["skeleton_id"], "bones": bones,
              "mesh": mesh, "clips": clips, "sockets": source.get("sockets", []), "head_bone": source["head_bone"],
              "bounds_center": [0, 0, 0], "bounds_radius": radius, "encoded_bytes": report["encoded_key_bytes"], "report": report}
    return result


_CACHE = {}


def load_character(path, *, compare_15hz=True):
    path = Path(path)
    require(path.stat().st_size <= 32*1024*1024, "Character source exceeds 32 MiB")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    key = digest, compare_15hz
    if key not in _CACHE:
        result = cook_source(json.loads(raw), compare_15hz=compare_15hz)
        result["source_sha256"] = digest
        result["symbol"] = "dl_character_" + digest[:20]
        result["report"]["source_sha256"] = digest
        _CACHE[key] = result
    return _CACHE[key]


def joint_meshes(character):
    mesh = character["mesh"]
    result = {}
    for i in range(0, len(mesh["indices"]), 3):
        triangle = mesh["indices"][i:i+3]
        joint = mesh["joints"][triangle[0]]
        if any(mesh["joints"][v] != joint for v in triangle):
            continue
        target = result.setdefault(joint, {"vertices": [], "normals": [], "uvs": [], "indices": []})
        for vertex in triangle:
            target["indices"].append(len(target["vertices"]))
            for key in ("vertices", "normals", "uvs"):
                target[key].append(mesh[key][vertex])
    return result


def cf(value):
    text = format(float(value), ".9g")
    return text + ("f" if "." in text or "e" in text.lower() else ".0f")


def cv(values):
    return "{" + ",".join(cf(v) for v in values) + "}"


def ct(transform):
    return "{" + cv(transform["translation"]) + "," + cv(transform["rotation"]) + "}"


def emit_c(character, *, shared=False, declarations=False):
    """Emit content-hashed symbols, optionally extern so a bundle shares bytes."""
    s, mesh = character["symbol"], character["mesh"]
    if declarations:
        return "\n".join([f"extern const DlAnimationAsset {s};", f"extern const DlVec3 {s}_vertices[];",
                          f"extern const uint16_t {s}_indices[];", f"extern const DlVec2 {s}_uvs[];",
                          f"extern const DlNormal {s}_normals[];"])
    storage = "const" if shared else "static const"
    lines = [f'{storage} DlVec3 {s}_vertices[] = {{' + ",".join(cv(v) for v in mesh["vertices"]) + "};",
             f'{storage} uint16_t {s}_indices[] = {{' + ",".join(map(str, mesh["indices"])) + "};",
             f'{storage} DlVec2 {s}_uvs[] = {{' + ",".join(cv(v) for v in mesh["uvs"]) + "};",
             f'{storage} DlNormal {s}_normals[] = {{' + ",".join("{"+",".join(str(max(-127, min(127, round(v*127)))) for v in n)+"}" for n in mesh["normals"]) + "};",
             f'static const uint8_t {s}_joints[] = {{' + ",".join(map(str, mesh["joints"])) + "};",
             f'static const DlAnimBone {s}_bones[] = {{' + ",".join("{"+json.dumps(b["id"])+f",{b['parent']},"+ct(b)+","+ct(b["inverse_bind"])+"}" for b in character["bones"]) + "};"]
    for i, clip in enumerate(character["clips"]):
        for j, track in enumerate(clip["tracks"]):
            lines.append(f'static const int16_t {s}_keys_{i}_{j}[] = {{' + ",".join(map(str, track["values"])) + "};")
        if clip["tracks"]:
            lines.append(f'static const DlAnimTrack {s}_tracks_{i}[] = {{' + ",".join(
                f"{{{t['bone']},{t['channel']},{t['sample_count']},{s}_keys_{i}_{j}}}" for j, t in enumerate(clip["tracks"])) + "};")
        if clip["events"]:
            lines.append(f'static const DlAnimEvent {s}_events_{i}[] = {{' + ",".join(f"{{{e['phase']},{e['kind']}}}" for e in clip["events"]) + "};")
    lines.append(f'static const DlAnimClip {s}_clips[] = {{' + ",".join(
        "{"+json.dumps(c["id"])+","+",".join(cf(c[k]) for k in ("duration", "stride_length", "translation_scale"))+
        f",{c['sample_count']},{len(c['tracks'])},{str(c['loop']).lower()},"+
        (f"{s}_tracks_{i}" if c["tracks"] else "NULL")+","+(f"{s}_events_{i}" if c["events"] else "NULL")+
        f",{len(c['events'])}"+"}" for i, c in enumerate(character["clips"])) + "};")
    if character["sockets"]:
        lines.append(f'static const DlAnimSocket {s}_sockets[] = {{' + ",".join(
            "{"+json.dumps(socket["id"])+f",{socket['bone']},"+ct(socket)+"}" for socket in character["sockets"]) + "};")
    lines.append(f'{storage} DlAnimationAsset {s} = {{.id=' + json.dumps(character["id"])+
                 f",.bones={s}_bones,.bone_count={len(character['bones'])},.vertex_bones={s}_joints,.clips={s}_clips,"+
                 f".clip_count={len(character['clips'])},.head_bone={character['head_bone']},.bounds_center="+cv(character["bounds_center"])+
                 f",.bounds_radius={cf(character['bounds_radius'])},.encoded_bytes={character['encoded_bytes']},"+
                 ".sockets="+(f"{s}_sockets" if character["sockets"] else "NULL")+f",.socket_count={len(character['sockets'])}"+"};")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = load_character(args.source)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output/"character.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
        (args.output/"character.h").write_text('#include <stddef.h>\n#include "animation.h"\n'+emit_c(result)+"\n", encoding="utf-8")
    print(json.dumps(result["report"], indent=2))


if __name__ == "__main__":
    main()
