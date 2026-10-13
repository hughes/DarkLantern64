#!/usr/bin/env python3
"""Derive a local Bafford district while preserving every artist input.

Commercial sources and products stay ignored. Cell annotations are checked as
provenance, never treated as a rebuilt visibility or navigation solution.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(ROOT))
from tools import compile_level as level
from tools import compile_mission as mission
from tools import convert_bafford_stream as converter
from tools import expand_bafford_stream as expansion
from tools.asset_pack import content_path
from tools.project_levels import ProjectLevels, json_bytes

CELL_REGIONS = {
    "central-ramp": (4326,4336,4340,4341,4342,4343,4344,4345,4348,4349,4350,4351,4352,4353,4354,4355,4356,4357,4358,4981,4989),
    "main-interior": (4273,4275,4277,4279,4281,4283,4328,4329,4330,4332,4333,4334,4335,4337,4338,4979),
    "rear-courtyard": (4229,4230,4233,4234,4245,4251,4252,4256,4258,4261,4263,4264,4265,4267,4269,4272,4274,4276,4278,4280,4282,4284,4296),
    "lower-east-alley": (3660,3663,3674,3675,3676,3677,3679,3802,4237,4243,4246,4247,4248,4249,4250,4253,4254,4255,4257,4318,4319,4322,4323,4324,4327,4980,4982),
    "outer-east-walk": (3147,3149,3156,3165,3166,3168,3169,3171,3172,3177,3179,3181,3186,3189,3196,3197,3198,3199,3200,3201,3202,3203,3204,3205,3206,3207,3248,3425,3493,3494,3644,3645,3666,3669,3673),
    "next-passage": (4092,4093,4098,4099,4100,4101,4116,4117,4126,4137,4138,4139,4140,4141,4142,4143,4144,4145,4146,4147),
    "west-return": (4212,4213,4214,4266,4268,4287,4293,4299,4300,4301,4303,4304,4305,4307,4310,4312,4315,4656),
}
RAMP_SEAM = 43.0
PADDING = 1e-4
DOOR_ID = "district-demonstration-door"
LOOT_ID = "district-demonstration-loot"
DISTRICT_STARTS = (
    dict(id="upper-room",label="Upper room",position=[0,0,35.3],yaw=180,pitch=0),
    dict(id="rear-courtyard",label="Rear courtyard",position=[-2.25,0,29.845],yaw=180,pitch=0),
    dict(id="east-alley",label="East alley room",position=[5,0,35.548],yaw=180,pitch=0),
    dict(id="west-return",label="West return",position=[-5,0,26.33],yaw=0,pitch=0),
    dict(id="outer-east-walk",label="Outer east walk",position=[10,0,28.2],yaw=90,pitch=0),
)


def clip_axis(triangle, axis, seam, side):
    """Use the existing attributed clipper under an axis permutation."""
    order = [axis] + [i for i in range(3) if i != axis]
    permuted = [{**corner, "position": [corner["position"][i] for i in order]} for corner in triangle]
    # Fan triangles may share corner dictionaries. Restore each independently
    # so a common fan vertex is never axis-permuted twice.
    result = copy.deepcopy(converter.clip_triangle(permuted, seam, side))
    result = [[copy.deepcopy(corner) for corner in part] for part in result]
    for part in result:
        for corner in part:
            values = corner["position"]
            corner["position"] = [values[order.index(i)] for i in range(3)]
    return result


def split_box(box, axis, seam):
    if box["lo"][axis] < seam < box["hi"][axis]:
        level.require(box["yaw"] == 0, "Rotated proxy crosses district region seam")
    parts = []
    for side in ("west", "east"):
        row = copy.deepcopy(box)
        if side == "west":
            row["hi"][axis] = min(row["hi"][axis], seam)
        else:
            row["lo"][axis] = max(row["lo"][axis], seam)
        if row["lo"][axis] < row["hi"][axis]:
            parts.append((side, row))
    return parts


def attributed_triangles(entity, mesh):
    transform = entity["transform"]
    positions = [level.transform_point(point, transform) for point in mesh["vertices"]]
    normals = [converter.normalized(level.rotate([n[i] / transform["scale"][i] for i in range(3)], transform["rotation"])) for n in mesh.get("normals", [])]
    for index in range(len(mesh["indices"]) // 3):
        corners = mesh["indices"][index*3:index*3+3]
        a,b,c = [positions[i] for i in corners]
        u,v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
        flat = converter.normalized([u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]])
        yield index, [dict(position=positions[i], normal=normals[i] if normals else flat,
                           **({"uv":mesh["uvs"][i]} if "uvs" in mesh else {})) for i in corners]


def supported_by_box(triangle, boxes):
    """A whole fan triangle covered by an existing flat box needs no duplicate."""
    for box in boxes:
        if box["yaw"] != 0:
            continue
        if all(abs(p["position"][1]-box["hi"][1]) <= .001 and
               all(box["lo"][i]-.000001 <= p["position"][i] <= box["hi"][i]+.000001 for i in (0,2)) for p in triangle):
            return True
    return False


def floor_candidate(triangle, boxes):
    # Contact additions cover authored district walking surfaces up to the
    # selected ground/interior storey. Render geometry and source boxes above
    # this storey are preserved in full, including roofs and ceilings.
    if min(p["normal"][1] for p in triangle) < .707106 or max(p["position"][1] for p in triangle) > 2:
        return False
    if supported_by_box(triangle, boxes):
        return False
    entity = dict(id="floor-check", transform=dict(position=[0,0,0], rotation=[0,0,0], scale=[1,1,1]),
                  floor=dict(vertices=[p["position"] for p in triangle]))
    level.floor_triangle(entity)
    return True


def source_support_query(document):
    """Return all clear ground/interior standing heights, with open doors."""
    boxes=[level.collider(row) for row in document['entities'] if 'collider' in row]
    floors=[level.floor_triangle(row) for row in document['entities'] if 'floor' in row]
    cache={}
    def supports(x,z):
        if (x,z) in cache:return cache[x,z]
        heights=[level.floor_height([x,0,z],floor) for floor in floors]
        heights=[height for height in heights if height is not None and -3<=height<=2]
        for box in boxes:
            if box['door']:continue
            local=level.local_xz([x,0,z],box);top=box['center'][1]+box['half_size'][1]
            if abs(local[0])<=box['half_size'][0] and abs(local[1])<=box['half_size'][2] and -3<=top<=2:heights.append(top)
        clear=[]
        for height in sorted(set(heights)):
            try:level.validate_actor_placement([x,height,z],boxes,'gate aperture sample',floors=floors,door_open=True)
            except ValueError:continue
            clear.append(height)
        cache[x,z]=clear
        return clear
    return supports


def audit_gate_apertures(document, manifest, sample_step=.02):
    """Sample standing source support on both sides of every authored cut.

    This bounded geometry audit is not a navigation proof. All clear support
    heights are considered so an upper box cannot hide a street crossing.
    """
    supports=source_support_query(document)
    regions={row['id']:row for row in manifest['regions']}
    rows=[]
    for gate in sorted(manifest.get('transitions',[]),key=lambda row:row['id']):
        minimum,maximum=gate['bounds']
        normal=0 if maximum[0]-minimum[0]<maximum[2]-minimum[2] else 2
        tangent=2-normal;plane=(minimum[normal]+maximum[normal])*.5
        a,b=[regions[gate[side]]['activation_bounds'] for side in ('from','to')]
        low,high=max(a[0][tangent],b[0][tangent]),min(a[1][tangent],b[1][tangent])
        crossing_samples=[];uncovered=[]
        for index in range(math.ceil((high-low)/sample_step)+1):
            value=min(high,low+index*sample_step)
            before,after=[0,0,0],[0,0,0]
            before[tangent]=after[tangent]=value
            before[normal],after[normal]=plane-.035,plane+.035
            pairs=[(first,second) for first in supports(before[0],before[2]) for second in supports(after[0],after[2]) if abs(first-second)<=.28]
            if not pairs:continue
            covered=minimum[tangent]<=value<=maximum[tangent] and any(
                minimum[1]<=first<=maximum[1] and minimum[1]<=second<=maximum[1] for first,second in pairs)
            row=dict(tangent=round(value,6),height_pairs=pairs)
            crossing_samples.append(row)
            if not covered:uncovered.append(row)
        rows.append(dict(id=gate['id'],normal_axis=normal,plane=plane,sample_step=sample_step,
            valid_samples=len(crossing_samples),sample_extent=[crossing_samples[0]['tangent'],crossing_samples[-1]['tangent']] if crossing_samples else None,
            uncovered_samples=len(uncovered),uncovered_extent=[uncovered[0]['tangent'],uncovered[-1]['tangent']] if uncovered else None))
    return rows


def audit_activation_adjacency(document, manifest, sample_step=.02):
    """Find supported phase exits, including edges missing from the gate list.

    Sample both XZ faces of each activation domain and every touching or
    overlapping destination. An overlap can retain the earlier gate sample,
    so this check asks for a directed edge; the aperture audit checks its cut.
    Sampling is bounded and does not establish complete navigation or a PVS.
    """
    if manifest.get('visibility')!='authored-sets':return []
    supports=source_support_query(document)
    edges={(gate['from'],gate['to']) for gate in manifest.get('transitions',[])}
    rows=[]
    for source in manifest['regions']:
        a=source['activation_bounds']
        for normal in (0,2):
            tangent=2-normal
            for side,direction in ((0,-1),(1,1)):
                plane=a[side][normal]
                for target in manifest['regions']:
                    if target['id']==source['id']:continue
                    b=target['activation_bounds']
                    after_normal=plane+direction*.035
                    if not b[0][normal]<=after_normal<=b[1][normal]:continue
                    low,high=max(a[0][tangent],b[0][tangent]),min(a[1][tangent],b[1][tangent])
                    if low>=high:continue
                    valid=[]
                    for index in range(math.ceil((high-low)/sample_step)+1):
                        value=min(high,low+index*sample_step)
                        before,after=[0,0,0],[0,0,0]
                        before[tangent]=after[tangent]=value
                        before[normal],after[normal]=plane-direction*.035,after_normal
                        pairs=[(first,second) for first in supports(before[0],before[2])
                               for second in supports(after[0],after[2])
                               if abs(first-second)<=.28 and a[0][1]<=first<=a[1][1] and b[0][1]<=second<=b[1][1]]
                        if pairs:valid.append(value)
                    if valid:rows.append(dict(source=source['id'],target=target['id'],normal_axis=normal,
                        plane=plane,sample_step=sample_step,valid_samples=len(valid),
                        sample_extent=[round(valid[0],6),round(valid[-1],6)],missing_edge=(source['id'],target['id'])not in edges))
    return rows


def policy():
    """Authored visibility working sets; source topology is not a PVS."""
    domains = {
        "west": [[-20,-3,36.576],[-3.048,14,55]],
        "east": [[-3.048,-3,46.7868],[16,14,55]],
        "ramp-lower": [[-3.048,-3,43],[3.6576,14,46.7868]],
        "ramp-upper": [[-3.048,-3,39.32],[3.6576,14,43]],
        "main-interior": [[-3.2,-3,32.46],[3.2,14,39.32]],
        "rear-courtyard": [[-3.6576,-3,27.1272],[7.1628,14,32.46]],
        "next-passage": [[-3.9624,-3,21.7128],[3.9624,14,27.1272]],
        "west-return": [[-16.4592,-3,24.6888],[-3.6576,14,36.576]],
        "lower-east-alley": [[3.6576,-3,27.432],[8.382,14,46.7868]],
        "outer-east-walk": [[8.382,-3,20.7264],[37.338,14,33.0708]],
    }
    sets = {
        "west": ["west","east"], "east": ["west","east"],
        "ramp-lower": ["west","east","ramp-lower","ramp-upper"],
        "ramp-upper": ["east","ramp-lower","ramp-upper","main-interior"],
        "main-interior": ["ramp-upper","main-interior","rear-courtyard"],
        "rear-courtyard": ["main-interior","rear-courtyard","next-passage"],
        "next-passage": ["rear-courtyard","next-passage"],
        "west-return": ["west","west-return","rear-courtyard"],
        "lower-east-alley": ["east","lower-east-alley"],
        "outer-east-walk": ["lower-east-alley","outer-east-walk"],
    }
    links = [
        ("west","east",[[-3.068,-3,46.7868],[-3.028,2,52.43]]),
        ("east","ramp-lower",[[-.65,-3,46.7668],[.65,3,46.8068]]),
        ("ramp-lower","ramp-upper",[[-3.048,-3,42.98],[3.048,3,43.02]]),
        ("ramp-upper","main-interior",[[-.9144,-.1,39.30],[.9144,2.5,39.34]]),
        ("main-interior","rear-courtyard",[[-3.2,-.1,32.44],[3.2,3,32.48]]),
        ("rear-courtyard","next-passage",[[-3.6576,-.1,27.1072],[3.9624,3,27.1472]]),
        ("rear-courtyard","west-return",[[-3.6776,-.1,27.1272],[-3.6376,3,32.46]]),
        ("next-passage","west-return",[[-3.9824,-.1,24.6888],[-3.9424,3,27.1272]]),
        ("west","west-return",[[-13,-.1,36.556],[-3.6576,3,36.596]]),
        ("east","lower-east-alley",[[3.6576,-3,46.7668],[8.382,3,46.8068]]),
        ("rear-courtyard","lower-east-alley",[[3.6376,-.1,27.432],[3.6776,3,32.46]]),
        ("lower-east-alley","outer-east-walk",[[8.362,-.1,27.432],[8.402,3,33.0708]]),
    ]
    gates=[]
    links=[(*row,"") for row in links]+[("rear-courtyard","lower-east-alley",
        [[3.6576,-.1,27.412],[7.1628,3,27.452]],"-north-overlap")]
    for a,b,bounds,suffix in links:
        for source,target in ((a,b),(b,a)):
            row=dict(id=source+"-to-"+target+suffix, **{"from":source,"to":target}, bounds=bounds)
            if {a,b} == {"ramp-upper","main-interior"}:
                row["door"] = DOOR_ID
            gates.append(row)
    return domains,sets,gates


def derive(document, baseline_state, baseline_manifest, full, annotations, topology, asset_root, source_name, full_name):
    memberships = {cid:region for region,cells in CELL_REGIONS.items() for cid in cells}
    level.require(len(memberships) == sum(map(len,CELL_REGIONS.values())), "District source cells have multiple homes")
    baseline = set(document["stream_expansion"]["preserved_cells"] + document["stream_expansion"]["added_cells"])
    level.require(not baseline & set(memberships), "District new cells overlap the artist baseline")
    # Portal connectivity is a provenance check, not an actor traversability claim.
    cells={row["id"]:row for row in topology["cells"]}
    reached=set(baseline)
    while True:
        more={cid for cid in memberships if cid not in reached and any(p["target"] in reached for p in cells[cid].get("portals",[]))}
        if not more: break
        reached |= more
    level.require(set(memberships) <= reached, "District source cell closure disconnected from artist baseline")
    solid_cells=set(memberships) & {row[0] for records in annotations.values() for row in records}
    new,meshes,materials,provenance,raw_boxes,counts,omissions=expansion.select_new_geometry(
        full,annotations,topology,solid_cells,asset_root,baseline_state["materials"],full_scene=full_name)
    groups=collections.defaultdict(lambda:dict(triangles=[],provenance=[]))
    boxes=collections.defaultdict(list)
    floor_rows=collections.defaultdict(list)
    homes={entity:region["id"] for region in baseline_manifest["regions"] for entity in region["entities"]}
    markers=collections.defaultdict(list)
    for entity in sorted(baseline_state["by_id"].values(),key=lambda row:row["id"]):
        owner=homes[entity["id"]]
        if "collider" in entity:
            boxes[owner].append(expansion.box_record(entity,source_name))
        if entity["kind"] != "static":
            level.require(entity["kind"] in ("spawn","light"), "Baseline dynamics require explicit shared conversion")
            markers[owner].append(copy.deepcopy(entity))
        if "model" in entity:
            for index,triangle in attributed_triangles(entity,baseline_state["meshes"][entity["model"]]):
                old=entity.get("source",{}).get("triangle_provenance",[])
                meta=copy.deepcopy(old[index]) if len(old)>index else dict(source_entity_id=entity["id"],source_model_id=entity["model"],source_triangle=index)
                meta["preserved_artist_scene"]=source_name
                groups[(owner,entity["material"])]["triangles"].append(triangle)
                groups[(owner,entity["material"])]["provenance"].append(meta)
    for box in raw_boxes:
        cid=box["sources"][0]["metadata"]["cell"]
        owner=memberships[cid]
        if owner == "central-ramp":
            for side,part in split_box(box,2,RAMP_SEAM):
                boxes["ramp-upper" if side=="west" else "ramp-lower"].append(part)
        else: boxes[owner].append(box)
    for entity in new:
        for index,triangle in attributed_triangles(entity,meshes[entity["model"]]):
            meta=provenance[(entity["id"],index)]
            owner=memberships[meta["cell"]]
            parts=[(owner,triangle)] if owner!="central-ramp" else [("ramp-upper" if side=="west" else "ramp-lower",part)
                for side in ("west","east") for part in clip_axis(triangle,2,RAMP_SEAM,side)]
            for part_index,(owner,part) in enumerate(parts):
                groups[(owner,entity["material"])]["triangles"].append(part)
                groups[(owner,entity["material"])]["provenance"].append({**meta,"district_part":part_index})
    merge_reports={}
    for region in boxes:
        boxes[region],merge_reports[region]=expansion.merge_boxes(boxes[region])
    for (region,material),group in sorted(groups.items()):
        for triangle,meta in zip(group["triangles"],group["provenance"]):
            if floor_candidate(triangle,boxes[region]):
                floor_rows[region].append((triangle,meta))
    domains,sets,gates=policy()
    demonstration=dict(game="DarkLantern", provenance="DarkLantern authored district demonstration")
    markers["ramp-upper"].append(dict(id=DOOR_ID,kind="door",model="district-unit-box",material="district-door-wood",
        transform=dict(position=[0,1.05,39.32],rotation=[0,0,0],scale=[1.1,2.1,.12]),
        collider=dict(shape="box",center=[0,0,0],half_size=[.5,.5,.5]),source=demonstration))
    markers["main-interior"].append(dict(id=LOOT_ID,kind="loot",model="district-unit-box",material="district-loot-gold",value=25,loot_highlight=True,
        transform=dict(position=[0,.7,34.6],rotation=[0,0,0],scale=[.25,.25,.25]),source=demonstration))
    materials.update({"district-door-wood":dict(id="district-door-wood",color=[.40,.23,.10,1],double_sided=False),
                      "district-loot-gold":dict(id="district-loot-gold",color=[1,.75,.10,1],double_sided=False)})
    entities,assets,payloads,regions=[],[dict(id="district-unit-box",type="primitive",shape="box")],{},[]
    for region in sorted(domains):
        points=[p["position"] for (owner,_),group in groups.items() if owner==region for tri in group["triangles"] for p in tri]
        points += [[box[end][i] for i in range(3)] for box in boxes[region] for end in ("lo","hi")]
        points += [e["transform"]["position"] for e in markers[region]]
        bounds=[[min(p[i] for p in points)-PADDING for i in range(3)], [max(p[i] for p in points)+PADDING for i in range(3)]]
        # Dynamic primitive corners and player head are included conservatively.
        bounds[1][1]=max(bounds[1][1],domains[region][0][1]+1.65)
        for e in markers[region]:
            if "model" in e:
                for i in range(3):
                    bounds[0][i]=min(bounds[0][i],e["transform"]["position"][i]-e["transform"]["scale"][i]*.5-PADDING)
                    bounds[1][i]=max(bounds[1][i],e["transform"]["position"][i]+e["transform"]["scale"][i]*.5+PADDING)
        anchor=[(a+b)*.5 for a,b in zip(*bounds)]
        owned=[]
        for (owner,material),group in sorted(groups.items()):
            if owner!=region or not group["triangles"]: continue
            ident="bafford-district-"+region+"-"+converter.digest(material.encode())[:12]
            model="mesh-"+ident
            payload=converter.obj_payload(group["triangles"],anchor)
            uri="assets/thief_bafford/models/district-"+region+"-"+converter.digest(payload)[:20]+".obj"
            assets.append(dict(id=model,uri=uri));payloads[uri]=payload
            row=dict(id=ident,kind="static",model=model,material=material,transform=dict(position=anchor,rotation=[0,0,0],scale=[1,1,1]),
                     source=dict(converter="bafford-district-v1",region=region,triangle_provenance=group["provenance"]))
            entities.append(row);owned.append(ident)
        for box in boxes[region]:
            row=expansion.proxy_entities([box])[0]
            row["id"]=row["id"].replace("bafford-expanded-","bafford-district-"+region+"-")
            entities.append(row);owned.append(row["id"])
        for triangle,meta in floor_rows[region]:
            points=[p["position"] for p in triangle]
            ident="bafford-district-floor-"+region+"-"+converter.digest(json_bytes(dict(points=points,provenance=meta)))[:20]
            row=dict(id=ident,kind="static",transform=dict(position=anchor,rotation=[0,0,0],scale=[1,1,1]),
                     floor=dict(vertices=[[p[i]-anchor[i] for i in range(3)] for p in points]),
                     source=dict(converter="exact-source-floor-v1",**meta))
            entities.append(row);owned.append(ident)
        entities+=markers[region];owned += [e["id"] for e in markers[region]]
        regions.append(dict(id=region,bounds=bounds,entities=sorted(owned),activation_bounds=domains[region],working_set=sorted(sets[region]),
                            neighbors=sorted({g["to"] for g in gates if g["from"]==region})))
    used={e["material"] for e in entities if "material" in e}
    result={key:copy.deepcopy(document[key]) for key in ("version","environment","test_starts","views") if key in document}
    result.update(title="Bafford District",authoring_profile="streamed-mission",assets=assets,
                  materials=[materials[i] for i in sorted(used)],entities=sorted(entities,key=lambda row:row["id"]))
    starts=result.setdefault("test_starts",[])
    level.require(not {row["id"] for row in starts} & {row["id"] for row in DISTRICT_STARTS},"Artist baseline already defines a district demonstration start")
    starts.extend(copy.deepcopy(DISTRICT_STARTS))
    result["stream_district"]=dict(version=1,preserved_scene=source_name,preserved_cells=sorted(baseline),added_cells=sorted(memberships),
        cell_regions={region:list(cids) for region,cids in CELL_REGIONS.items()},ramp_split_z=RAMP_SEAM,
        collision_merge=merge_reports,new_collision_omissions=omissions,
        visibility="Authored conservative working sets, not source PVS; back-view acceptance is separate",
        floors="Exact evaluated upward fan triangles in the selected ground/interior storey (Y <= 2 metres), except complete triangles already supported by source boxes")
    manifest=dict(version=1,source="",entry_region=baseline_manifest["entry_region"],visibility="authored-sets",regions=regions,transitions=gates)
    report=dict(version=1,preserved_cells=sorted(baseline),added_cells=sorted(memberships),portal_only_cells=sorted(set(memberships)-solid_cells),added_triangle_counts={str(k):counts.get(k,0) for k in sorted(memberships)},
                source_cells=len(baseline|set(memberships)),regions=[dict(id=r["id"],triangles=sum(len(g["triangles"]) for (owner,_),g in groups.items() if owner==r["id"]),
                    boxes=len(boxes[r["id"]]),floors=len(floor_rows[r["id"]]),groups=sum(owner==r["id"] for owner,_ in groups),bounds=r["bounds"]) for r in regions],
                entities=len(entities),materials=len(used),collision_merge=merge_reports,omissions=["Original mission scripts/objectives and source dynamic actors remain omitted", "Nonrectangular/diagonal walls omitted by original proxy importer are not replaced by filled bounding boxes"],
                acceptance="Derived source and exact floors; target streamed traversal, authored visibility and persistence require separate acceptance")
    return result,manifest,payloads,report


def convert(source, baseline_manifest_path, full_path, annotations_path, topology_path, output, manifest_path=None, *, content_root=ROOT/"content", replace=False):
    content_root=Path(content_root).resolve()
    paths=[Path(p).resolve() for p in (source,baseline_manifest_path,full_path,annotations_path,topology_path,output)]
    source,baseline_manifest_path,full_path,annotations_path,topology_path,output=paths
    manifest_path=Path(manifest_path).resolve() if manifest_path else output.with_name(output.stem+".mission.json")
    level.require(source.parent==full_path.parent==baseline_manifest_path.parent==output.parent==manifest_path.parent==content_root,"Canonical sources must be directly inside content root")
    level.require(output.name=="thief_bafford_district.json" and manifest_path.name=="thief_bafford_district.mission.json","District output must use separate district filenames")
    level.require(output not in paths[:5] and manifest_path not in paths[:5] and output!=manifest_path,"District derivation must preserve all original source inputs")
    project=ProjectLevels(content_root.parent)
    with project.lock():
        inputs=[project.safe(p) for p in (source,baseline_manifest_path,full_path,annotations_path,topology_path)]
        expected={p:p.read_bytes() for p in inputs}
        document,baseline_manifest,full,annotations,topology=[json.loads(expected[p]) for p in inputs]
        prior={p:p.read_bytes() if p.exists() else None for p in (output,manifest_path)}
        level.require(replace or not any(v is not None for v in prior.values()),"District edits exist; pass --replace explicitly and recovery copies will be retained")
        expected.update(prior)
        discovered=level.validate(document,content_root,streaming=True)
        deps={uri:Path(path).resolve() for uri,path in discovered["dependencies"]}
        selected={c for rows in CELL_REGIONS.values() for c in rows}
        assets={a["id"]:a for a in full["assets"]};materials={m["id"]:m for m in full["materials"]}
        for entity in full["entities"]:
            if entity["kind"]=="static" and "model" in entity and selected & set(entity.get("source",{}).get("cells",[])):
                uri=assets[entity["model"]]["uri"];deps[uri]=content_path(uri,content_root,{".obj"})
                material=discovered["materials"].get(entity["material"],materials[entity["material"]])
                if "texture" in material:
                    uri=material["texture"]["uri"];deps[uri]=content_path(uri,content_root,{".png",".jpg",".jpeg"})
        captured={};external={}
        for uri,path in sorted(deps.items()):
            if not uri.startswith("code:"):
                level.require(project.safe(content_root/uri)==path and path.is_relative_to(content_root),"District dependency escapes/aliases content")
            captured[uri]=path.read_bytes()
            if path.is_relative_to(project.root):expected[project.safe(path)]=captured[uri]
            else:external[path]=captured[uri]
        staging=project.safe(project.editor/"district-staging");staging.mkdir(parents=True,exist_ok=True)
        changes={}
        with tempfile.TemporaryDirectory(prefix="bafford-",dir=staging) as temporary:
            overlay=Path(temporary)/"content";overlay.mkdir()
            for uri,raw in captured.items():
                if uri.startswith("code:"):continue
                p=overlay/uri;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            state=level.validate(document,overlay,streaming=True)
            level.require({u for u,_ in state["dependencies"]}=={u for u,_ in discovered["dependencies"]},"Baseline dependency set changed; retry")
            result,manifest,payloads,report=derive(document,state,baseline_manifest,full,annotations,topology,overlay,source.name,full_path.name)
            manifest["source"]=output.name
            report["input_sources"]={p.relative_to(project.root).as_posix():converter.digest(expected[p]) for p in inputs}
            report["input_dependencies"]=[dict(uri=u,sha256=converter.digest(raw)) for u,raw in sorted(captured.items())]
            result["stream_district"]["input_sources"]=report["input_sources"]
            report['gate_aperture_audit']=audit_gate_apertures(result,manifest)
            level.require(all(row['uncovered_samples']==0 for row in report['gate_aperture_audit']),
                          'Authored transition misses a valid sampled source aperture')
            report['activation_adjacency_audit']=audit_activation_adjacency(result,manifest)
            level.require(not any(row['missing_edge'] for row in report['activation_adjacency_audit']),
                          'Supported activation exit has no authored directed transition')
            for uri,raw in payloads.items():
                p=project.safe(content_root/uri);old=p.read_bytes() if p.exists() else None
                level.require(old is None or old==raw,"Immutable district OBJ changed")
                expected[p]=old
                if old is None:changes[p]=raw
                p=overlay/uri;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            (overlay/output.name).write_bytes(json_bytes(result))
            (overlay/manifest_path.name).write_bytes(json_bytes(manifest))
            cooked=mission.prepare_stream_level(overlay/manifest_path.name,Path(temporary)/"prepared",overlay)
            report["cooked"]=dict(package_sha256=cooked["mission"]["sha256"],package_bytes=cooked["mission"]["bytes"],report=cooked["mission"]["report"])
        if any(v is not None for v in prior.values()):
            recovery=project.safe(project.editor/"replaced-districts"/uuid.uuid4().hex)
            changes.update({recovery/p.name:raw for p,raw in prior.items() if raw is not None})
        changes.update({output:json_bytes(result),manifest_path:json_bytes(manifest),project.safe(project.root/"build/bafford-district-conversion/report.json"):json_bytes(report)})
        for p,raw in external.items():level.require(p.read_bytes()==raw,"External compiler input changed; retry")
        project.publish(changes,expected)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=ROOT/"content/thief_bafford_expanded.json")
    parser.add_argument("--baseline-manifest",type=Path,default=ROOT/"content/thief_bafford_expanded.mission.json")
    parser.add_argument("--full",type=Path,default=ROOT/"content/thief_bafford.json")
    parser.add_argument("--annotations",type=Path,default=ROOT/"build/thief-bafford/face-annotations.json")
    parser.add_argument("--topology",type=Path,default=ROOT/"build/thief-bafford/world-topology.json")
    parser.add_argument("--output",type=Path,default=ROOT/"content/thief_bafford_district.json")
    parser.add_argument("--manifest",type=Path)
    parser.add_argument("--content-root",type=Path,default=ROOT/"content")
    parser.add_argument("--replace",action="store_true")
    args=parser.parse_args(argv)
    try:
        report=convert(args.source,args.baseline_manifest,args.full,args.annotations,args.topology,args.output,args.manifest,content_root=args.content_root,replace=args.replace)
        print(json.dumps({k:report[k] for k in ("source_cells","entities","materials","regions")},indent=2));print("Package",report["cooked"]["package_bytes"],report["cooked"]["package_sha256"])
        return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        print("Bafford district derivation failed: "+str(error),file=sys.stderr);return 1


if __name__=="__main__":raise SystemExit(main())
