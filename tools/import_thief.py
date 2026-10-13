#!/usr/bin/env python3
"""Convert the first Thief campaign mission through editable Blender scenes.

The complete source study and a bounded entry-area runtime probe are distinct.
No game installation files are changed; copied commercial content stays local.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque, Counter
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.thief_import.dark import Database, read_worldrep, read_textures, read_light_table, polygon_uvs
from tools.thief_import.objects import ObjectDatabase, extract_objects, merge_compiled_lights as match_light_properties
from tools.thief_import.geometry import (game_point, clean_polygon, polygon_box,
                                        floor_position, dot, obj_payload, METRES_PER_UNIT)
from tools.thief_import.textures import TextureArchive, texture_id
from tools.blender_assets import find_blender, package_addon
from tools.blender_scene import checkout, export_source, apply_exchange
from tools.compile_level import validate, compile_level


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def transform(position, yaw=0, scale=(1,1,1)):
    return dict(position=[round(v, 7) for v in position], rotation=[0,yaw,0], scale=list(scale))


def entity(ident, kind, position, **fields):
    return dict(id=ident, kind=kind, transform=transform(position), **fields)


def source(row):
    return dict(game="Thief Gold", **row)


def load_faces(world, textures, archive, odb, asset_dir):
    """Preserve original UV bases and image sizes; omit water/sky boundaries."""
    material_map, image_sizes, misses = {}, {}, []
    used = {p.texture_id for c in world.cells for p in c.render_polygons[:c.solid_polygon_count]}
    for index in sorted(used):
        if index >= len(textures):
            continue
        tex = textures[index]
        family, name = tex["family"], tex["name"]
        ident = texture_id(index, family, name)
        if name.casefold() == "null":
            material_map[index] = dict(id=ident, color=[.18,.2,.24,1], double_sided=False,
                source=dict(texture_id=index, family=family, name=name, surface="unknown"))
            image_sizes[index] = (64,64)
            continue
        try:
            image, entry = archive.read(family, name)
        except ValueError as error:
            misses.append(str(error))
            material_map[index] = dict(id=ident, color=[.7,.2,.7,1], double_sided=False,
                source=dict(texture_id=index, family=family, name=name, missing_texture=True))
            image_sizes[index] = (64,64)
            continue
        file = asset_dir / "textures" / f"{index:03d}-{name.lower()}.png"
        file.parent.mkdir(parents=True, exist_ok=True)
        image.save(file)
        image_sizes[index] = image.size
        sound = odb.texture_surface_material(family, name)
        material_map[index] = dict(id=ident, color=[1,1,1,1], double_sided=False,
            texture=dict(uri=file.relative_to(ROOT/"content").as_posix(), width=32, height=32, format="RGBA16"),
            source=dict(texture_id=index, family=family, name=name, archive_entry=entry,
                        original_size=list(image.size), **sound))
    faces, omissions = [], Counter()
    for cell in world.cells:
        for pi in range(cell.solid_polygon_count):
            index = cell.render_polygons[pi].texture_id
            if index not in material_map:
                omissions["special_texture_boundaries"] += 1
                continue
            points = [game_point(p) for p in cell.polygon_vertices(pi)]
            uvs = polygon_uvs(cell, pi, *image_sizes[index], flip_v=True)
            cleaned = clean_polygon(points, uvs)
            if cleaned is None:
                omissions["near_zero_area_source_slivers"] += 1
                continue
            points, uvs, normal = cleaned
            nx, ny, nz = cell.planes[cell.polygons[pi].plane_index].normal
            inward = [nx, nz, -ny]
            if dot(normal, inward) < 0:
                points.reverse()
                uvs.reverse()
                normal = [-v for v in normal]
            faces.append(dict(cell=cell.id, polygon=pi, texture=index,
                points=points, uvs=uvs, normal=normal,
                center=[sum(p[i] for p in points)/len(points) for i in range(3)]))
    omissions["water_portal_surfaces"] = sum(len(c.render_polygons)-c.solid_polygon_count for c in world.cells)
    return faces, material_map, dict(omissions), misses


def architecture(faces, material_map, asset_dir, prefix):
    groups = defaultdict(list)
    for face in faces:
        region = tuple(math.floor(v/12) for v in face["center"])
        groups[(region, face["texture"])].append(face)
    assets, entities, annotations = [], [], {}
    for group_index, ((region, tex), group) in enumerate(sorted(groups.items())):
        chunks, current, vertices = [], [], 0
        for face in group:
            if current and vertices+len(face["points"]) > 3000:
                chunks.append(current)
                current, vertices = [], 0
            current.append(face)
            vertices += len(face["points"])
        if current:
            chunks.append(current)
        for piece, chunk in enumerate(chunks):
            ident = f"{prefix}-surface-{group_index:04d}-{piece}"
            model = "mesh-" + ident
            origin = [v*12+6 for v in region]
            path = asset_dir / "models" / (ident + ".obj")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(obj_payload(chunk, origin), encoding="utf-8")
            assets.append(dict(id=model, uri=path.relative_to(ROOT/"content").as_posix()))
            entities.append(entity(ident, "static", origin, model=model,
                material=material_map[tex]["id"], source=dict(game="Thief Gold", region=list(region),
                    texture_id=tex, cells=sorted({f["cell"] for f in chunk}), source_polygons=len(chunk))))
            annotations[model] = [[f["cell"], f["polygon"]]
                                  for f in chunk for _ in range(len(f["points"])-2)]
    return assets, entities, annotations


def collision_entities(faces, material_map, prefix):
    result, seen, unmapped = [], set(), Counter()
    for face in faces:
        proxy = polygon_box(face["points"], face["normal"])
        if proxy is None:
            unmapped["sloped" if max(abs(v) for v in face["normal"]) < .99999 else "nonrectangular_or_too_thin"] += 1
            continue
        center, half = proxy
        key = tuple(round(v,5) for v in center+half)
        if key in seen:
            continue
        seen.add(key)
        ident = f"{prefix}-collision-{face['cell']:04d}-{face['polygon']:03d}"
        result.append(entity(ident, "static", center,
            collider=dict(shape="box", center=[0,0,0], half_size=half),
            source=dict(game="Thief Gold", cell=face["cell"], polygon=face["polygon"],
                        material=material_map[face["texture"]]["source"])))
    return result, dict(unmapped)


def gameplay(extracted, faces, prefix, *, study):
    result, notes = [], []
    floors = [f for f in faces if f["normal"][1] > .99999]
    row = extracted["spawn"]
    point = game_point(row["position"])
    snapped = floor_position(point, floors, max_drop=4)
    if snapped:
        point = snapped
    spawn = entity(prefix+"-spawn", "spawn", point, source=source(row))
    spawn["transform"]["rotation"][1] = (90+row["facing_degrees"][2]) % 360
    result.append(spawn)
    cells = {f["cell"] for f in faces}

    def included(point):
        return study or floor_position(point, floors, max_drop=3) is not None

    guards = [r for r in extracted["guards"] if included(game_point(r["position"]))]
    if not study:
        guards = sorted(guards, key=lambda r: sum((game_point(r["position"])[i]-point[i])**2 for i in range(3)))[:2]
    for row in guards:
        pos = game_point(row["position"])
        snapped = floor_position(pos, floors)
        if snapped:
            pos = snapped
        elif not study:
            continue
        guard = entity(f"{prefix}-guard-{row['source_id']}", "guard", pos, model="mesh-guard",
            material="mat-guard", enemy_type="watchman", behavior="sentry", patrol=[], source=source(row))
        guard["transform"]["rotation"][1] = (90+row["facing_degrees"][2]) % 360
        result.append(guard)
    notes.append("Guard origins are projected to containing horizontal floors for DarkLantern feet positions; source positions/facing/patrols are retained. All adapted guards use sentries.")
    for row in extracted["loot"]:
        pos = game_point(row["position"])
        if not included(pos):
            continue
        name = (row["model"]+" "+row["archetype"]["name"]).lower()
        kind = "scepter" if "scept" in name else "goblet" if any(s in name for s in ("cup","goblet","vase")) else "jewel" if any(s in name for s in ("gem","ring","necklace")) else "purse" if "purse" in name else "coins"
        result.append(entity(f"{prefix}-loot-{row['source_id']}", "loot", pos,
            model="mesh-loot-"+kind, material="mat-loot-atlas", value=max(0,row["value"]),
            interaction_radius=1.5, source=source(row)))
    for row in extracted.get('objectives', []):
        pos = game_point(row['position'])
        if included(pos):
            result.append(entity(f"{prefix}-objective-{row['source_id']}", 'objective',pos,
                model='mesh-loot-scepter',material='mat-loot-atlas',
                required_loot=0,interaction_radius=1.5,source=source(row)))
    lights = [r for r in extracted["lights"] if r["brightness"] > 0]
    if not study:
        lights = sorted(lights, key=lambda r: sum((game_point(r["position"])[i]-point[i])**2 for i in range(3)))[:16]
    for row in lights:
        radius = row["radius"]*METRES_PER_UNIT if row["radius"] > 0 else 8.0
        result.append(entity(f"{prefix}-light-{row['source_id']}"+('-animated' if row['animated'] else ''), "light",
            game_point(row["position"]), radius=max(.1,min(128,radius)),
            intensity=min(16,max(0,row["brightness"]/125)), color=row["color"], source=source(row)))
    notes.append("Source light brightness / 125 maps to intensity; unbounded source radii use an 8 m probe radius; animated lights are static snapshots.")
    return result, notes


def entry_cells(world, faces, source_spawn):
    """A connected portal neighborhood that stays inside existing runtime budgets."""
    containing = [c.id for c in world.cells if all(dot(p.normal,source_spawn)+p.distance >= -.001 for p in c.planes)]
    if not containing:
        raise ValueError("Source player start is outside the world cells")
    by_cell = defaultdict(list)
    for face in faces:
        by_cell[face["cell"]].append(face)
    queue, seen, selected = deque(containing), set(), []
    corners = triangles = boxes = 0
    while queue:
        cid = queue.popleft()
        if cid in seen:
            continue
        seen.add(cid)
        rows = by_cell[cid]
        nv = sum(len(f["points"]) for f in rows)
        nt = sum(len(f["points"])-2 for f in rows)
        nb = sum(polygon_box(f["points"],f["normal"]) is not None for f in rows)
        if corners+nv > 2200 or triangles+nt > 1200 or boxes+nb > 155:
            continue
        selected += rows
        corners += nv
        triangles += nt
        boxes += nb
        neighbors = [p.target_cell for p in world.cells[cid].polygons if p.is_portal and p.target_cell != 0xffff]
        queue.extend(sorted(neighbors, key=lambda n:sum((world.cells[n].center[i]-source_spawn[i])**2 for i in range(3))))
    return selected, containing[0]


def level_document(title, assets, materials, static, gameplay_rows, *, study):
    used = {e["material"] for e in static if "material" in e}
    document = dict(version=2, title=title, assets=assets,
        asset_packs=["assets/guard/pack.json", "assets/loot/pack.json"],
        materials=[m for m in materials.values() if m["id"] in used],
        entities=static+gameplay_rows, test_starts=[],
        environment=dict(ambient=[.10,.11,.14], moon_direction=[-.4,.8,.3], moon_color=[.48,.58,.85],
            moon_intensity=.08, fog_color=[.025,.03,.05],fog_near=20,fog_far=64,
            sky_top=[.015,.025,.06], sky_bottom=[.03,.035,.055], exposure=1.2))
    if study:
        document["authoring_profile"] = "external-scale-study"
    else:
        start = next(e for e in gameplay_rows if e['kind'] == 'spawn')
        point = list(start['transform']['position'])
        point[1] += 1.5
        document['views'] = [dict(id='bafford-entry',position=point,
            yaw=start['transform']['rotation'][1],pitch=0,door_open=False)]
    return document


def finish_blender(work, report, blender):
    reports = report['converted']
    for label, filename in (('full','thief-bafford.blend'),('entry','thief-bafford-entry.blend')):
        path = ROOT / reports[label]['level']
        destination = ROOT/'art'/filename
        if not destination.exists():
            checkout(path.name,destination,ROOT,blender)
        annotate = subprocess.run([str(find_blender(blender)),"--background","--disable-autoexec",str(destination),
            "--python-exit-code","1","--python",str(ROOT/"tools/blender/thief_scene.py"),"--",str(work),label],capture_output=True,text=True)
        (work/f"{label}-annotate.log").write_text(annotate.stdout+annotate.stderr,encoding="utf-8")
        if annotate.returncode:
            raise RuntimeError(annotate.stdout+annotate.stderr)
        exported = export_source(destination,ROOT,blender)
        exchange_meta = json.loads(Path(exported['exchange']).read_bytes())
        if exchange_meta['level'] != path.name:
            raise ValueError(f"{destination}: checkout belongs to {exchange_meta['level']}, expected {path.name}")
        imported = apply_exchange(exported["exchange"],ROOT)
        reports[label]["blender"] = dict(blend=destination.relative_to(ROOT).as_posix(),
            exchange=Path(exported["exchange"]).relative_to(ROOT).as_posix(),revision=exported["revision"],
            imported=imported["published"],changed_meshes=len(imported["changed_meshes"]))
        write_json(work/'report.json',report)
        print(json.dumps(dict(stage="blender_imported",part=label,**reports[label]["blender"])),flush=True)
    package_addon()
    compile_level(ROOT/reports['entry']['level'],ROOT/'build/scenes/thief_bafford_entry',ROOT/'content')


def merge_compiled_lights(extracted, compiled):
    extracted['property_lights'] = extracted['lights']
    merged = match_light_properties(extracted['property_lights'],compiled['lights'])
    extracted['lights'] = merged['lights']
    extracted['stats'].update(merged['stats'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, default=Path(r"C:\Program Files (x86)\Steam\steamapps\common\thief_gold"))
    parser.add_argument("--difficulty", type=int, choices=(0,1,2), default=2)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--prepare-only", action="store_true", help="Write adapted source documents; omit Blender and importer")
    parser.add_argument("--finish-prepared",action="store_true",help="Resume the Blender/export/import stages from build/thief-bafford/report.json, preserving existing sources")
    args = parser.parse_args(argv)
    install = args.install.resolve()
    work = ROOT/"build/thief-bafford"
    work.mkdir(parents=True, exist_ok=True)
    if args.finish_prepared:
        report = json.loads((work/'report.json').read_bytes())
        finish_blender(work,report,args.blender)
        return 0
    blend = ROOT/"art/thief-bafford.blend"
    probe_blend = ROOT/"art/thief-bafford-entry.blend"
    if blend.exists() or probe_blend.exists():
        raise ValueError("Thief .blend sources already exist. Keep artist edits; choose another checkout filename or move the old sources before regeneration.")
    mission_path, gam_path = install/"miss2.mis", install/"DARK.GAM"
    mission, gam = Database.read(mission_path), Database.read(gam_path)
    world, textures = read_worldrep(mission), read_textures(mission)
    compiled_lights = read_light_table(world)
    odb = ObjectDatabase(gam, mission)
    extracted = extract_objects(gam, mission, difficulty=args.difficulty)
    merge_compiled_lights(extracted,compiled_lights)
    asset_dir = ROOT/"content/assets/thief_bafford"
    archive = TextureArchive(install)
    try:
        faces, materials, omissions, missing = load_faces(world,textures,archive,odb,asset_dir)
    finally:
        archive.close()
    probe, start_cell = entry_cells(world,faces,extracted["spawn"]["position"])
    documents, annotations, reports = {}, {}, {}
    for label, selected, study in (("full",faces,True),("entry",probe,False)):
        prefix = "thief-bafford" + ("-entry" if not study else "")
        assets, entities, attrs = architecture(selected,materials,asset_dir,prefix)
        boxes, collision_omissions = collision_entities(selected,materials,prefix)
        gameplay_rows, notes = gameplay(extracted,selected,prefix,study=study)
        document = level_document("Thief | Bafford's Manor"+(" | source study" if study else " | entry probe"),assets,materials,entities+boxes,gameplay_rows,study=study)
        path = ROOT/"content"/("thief_bafford"+("_entry" if not study else "")+".json")
        write_json(path, document)
        state = validate(document,ROOT/"content",authoring=study)
        documents[label] = path
        annotations.update(attrs)
        reports[label] = dict(level=path.relative_to(ROOT).as_posix(),
            cells=len({f["cell"] for f in selected}), surfaces=len(selected),
            triangles=sum(len(f["points"])-2 for f in selected),
            architecture_models=len(assets), colliders=len(boxes),
            entities=len(document["entities"]),
            instanced_vertices=sum(len(state["meshes"][m["model"]]["vertices"]) for m in state["models"]),
            instanced_triangles=sum(len(state["meshes"][m["model"]]["indices"])//3 for m in state["models"]),
            lights=len(state["kinds"]["light"]),guards=len(state["kinds"]["guard"]),
            loot=len(state["kinds"]["loot"]), collision_omissions=collision_omissions, adaptations=notes)
        print(json.dumps(dict(stage="prepared",part=label,**reports[label])),flush=True)
    topology = dict(version=1, coordinates="Thief XYZ, Z up; chosen adaptation scale 0.3048 m per source unit",start_cell=start_cell,
        cells=[dict(id=c.id, medium=c.medium, center=c.center,radius=c.radius,
                    vertices=c.vertices,
                    planes=[asdict(p) for p in c.planes],
                    portals=[dict(polygon=i,target=p.target_cell,vertex_indices=p.vertex_indices,
                                  plane_index=p.plane_index,flags=p.flags) for i,p in enumerate(c.polygons) if p.is_portal],
                    light_indices=c.light_indices) for c in world.cells],
        bsp_nodes=[asdict(n) for n in world.bsp_nodes],bsp_planes=[asdict(p) for p in world.bsp_planes],
        compiled_lights=compiled_lights['lights'],animated_light_cell_mapping=compiled_lights['animation_cell_mapping'])
    write_json(work/"world-topology.json",topology)
    write_json(work/"source-objects.json",extracted)
    write_json(work/"face-annotations.json",annotations)
    report = dict(version=1, mission="Lord Bafford's Manor",source_mission="miss2.mis",
        source_sha256=hashlib.sha256(mission_path.read_bytes()).hexdigest(),
        gamesystem_sha256=hashlib.sha256(gam_path.read_bytes()).hexdigest(),
        source_world=world.stats(),source_objects=extracted["stats"], converted=reports,
        omissions=omissions,missing_textures=missing,
        limitations=["Full mission is a host-only authoring study; N64 seamless streaming is not implemented.",
            "Water/swimming, ladders, floor trapdoors, door models/scripts, props, special AI and mission scripts are omitted.",
            "Only rectangular axis-aligned world faces get exact thin box proxies; slopes and nonrectangular faces need mesh collision.",
            "Source surface sound tags are preserved; current runtime footsteps use the existing single sound.",
            "Guard and loot visuals use DarkLantern assets; guard patrols and source lighting attenuation are approximations."])
    write_json(work/"report.json",report)
    if not args.prepare_only:
        finish_blender(work,report,args.blender)
    compile_level(documents["entry"],ROOT/"build/scenes/thief_bafford_entry",ROOT/"content")
    print(f"Report: {work/'report.json'}",flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
