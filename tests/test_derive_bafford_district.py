"""Original synthetic clipping, floor and atomic district conversion fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import compile_level as level
from tools import derive_bafford_district as district
from tools import expand_bafford_stream as expansion
from tools.project_levels import ProjectLevels,json_bytes

ROOT=Path(__file__).resolve().parents[1]


def corner(x,y,z):
    return dict(position=[x,y,z],normal=[0,1,0],uv=[x,z])


def box(low,high,yaw=0):
    return dict(lo=list(low),hi=list(high),yaw=yaw,semantic=("stone",""),sources=[dict(scene="original.json",entity_id="proxy")])


class DistrictGeometryTests(unittest.TestCase):
    def test_z_clipping_preserves_attributes_area_and_unmodified_input(self):
        triangle=[corner(-1,0,42),corner(1,1,44),corner(1,0,42)]
        original=copy.deepcopy(triangle)
        upper=district.clip_axis(triangle,2,43,"west")
        lower=district.clip_axis(triangle,2,43,"east")
        self.assertEqual(triangle,original)
        self.assertEqual(len(upper)+len(lower),3)
        for parts,side in ((upper,"west"),(lower,"east")):
            for part in parts:
                for row in part:
                    x,y,z=row["position"]
                    self.assertEqual(row["uv"],[x,z])
                    self.assertEqual(row["normal"],[0,1,0])
                    self.assertGreaterEqual(y,0);self.assertLessEqual(y,1)
                    self.assertTrue(z<=43 if side=="west" else z>=43)
        area=lambda tri:abs(sum(tri[i]["position"][0]*tri[(i+1)%3]["position"][2]-tri[(i+1)%3]["position"][0]*tri[i]["position"][2] for i in range(3)))*.5
        self.assertAlmostEqual(sum(area(part) for part in upper+lower),area(triangle))
        # Fan corners previously shared dictionaries and were restored twice.
        self.assertTrue(all(42<=p["position"][2]<=44 for t in upper+lower for p in t))

    def test_axis_box_split_is_exact_and_keeps_full_provenance(self):
        original=box([-1,-.1,42],[1,0,44]);before=copy.deepcopy(original)
        parts=district.split_box(original,2,43)
        self.assertEqual(original,before)
        self.assertEqual([side for side,_ in parts],["west","east"])
        self.assertEqual(parts[0][1]["hi"],[1,0,43])
        self.assertEqual(parts[1][1]["lo"],[-1,-.1,43])
        self.assertEqual(parts[0][1]["sources"],original["sources"])
        with self.assertRaisesRegex(ValueError,"Rotated proxy crosses"):
            district.split_box(box([-1,0,42],[1,1,44],.2),2,43)
        self.assertEqual(len(district.split_box(box([-1,0,44],[1,1,45],.2),2,43)),1)

    def test_exact_flat_box_support_is_not_duplicated_and_gaps_are_not_filled(self):
        triangle=[corner(0,0,0),corner(0,0,1),corner(1,0,1)]
        self.assertFalse(district.floor_candidate(triangle,[box([0,-.1,0],[1,0,1])]))
        self.assertTrue(district.floor_candidate(triangle,[box([0,-.1,0],[.4,0,1])]))
        self.assertTrue(district.floor_candidate(triangle,[]))
        triangle[1]["position"][1]=.25;triangle[2]["position"][1]=.25
        self.assertTrue(district.floor_candidate(triangle,[box([0,-.1,0],[1,0,1])]))
        self.assertFalse(district.floor_candidate([corner(0,3,0),corner(0,3,1),corner(1,3,1)],[]))

    def test_roundtrip_anchor_preserves_exact_floor_grade_and_bounds(self):
        triangle=[corner(-.6477,-2.4003,46.1772),corner(-.6477,-1.0287,40.6908),corner(.6477,-1.0287,40.6908)]
        parts=district.clip_axis(triangle,2,43,"west")+district.clip_axis(triangle,2,43,"east")
        anchor=[0,2,43]
        with tempfile.TemporaryDirectory(dir=ROOT/"build") as tmp:
            p=Path(tmp)/"ramp.obj";p.write_bytes(district.converter.obj_payload(parts,anchor))
            mesh=level.read_obj(p,textured=True)
            transform=dict(position=anchor,rotation=[0,0,0],scale=[1,1,1])
            for point in mesh["vertices"]:
                x,y,z=level.transform_point(point,transform)
                self.assertAlmostEqual(y,-1.606-.25*(z-43),places=9)
                self.assertGreaterEqual(z,40.6908-district.PADDING)
                self.assertLessEqual(z,46.1772+district.PADDING)

    def test_policy_has_ten_regions_bidirectional_door_and_bounded_sets(self):
        domains,sets,gates=district.policy()
        self.assertEqual(len(domains),10)
        self.assertEqual(set(domains),set(sets))
        self.assertIn('next-passage',sets['rear-courtyard'])
        pairs={(g["from"],g["to"]) for g in gates}
        for source,target in pairs:self.assertIn((target,source),pairs)
        self.assertIn(('next-passage','west-return'),pairs)
        self.assertIn(('rear-courtyard','lower-east-alley'),pairs)
        door=[g for g in gates if g.get("door")==district.DOOR_ID]
        self.assertEqual(len(door),2)
        self.assertEqual({g["from"] for g in door},{"ramp-upper","main-interior"})
        for region,working in sets.items():
            self.assertIn(region,working)
            for gate in (g for g in gates if g["from"]==region):
                self.assertLessEqual(len(set(working)|set(sets[gate["to"]])),5)

    def test_named_starts_have_distinct_authored_activation_regions(self):
        domains,_,_=district.policy()
        expected={"upper-room":"main-interior","rear-courtyard":"rear-courtyard","east-alley":"lower-east-alley","west-return":"west-return","outer-east-walk":"outer-east-walk"}
        for row in district.DISTRICT_STARTS:
            point=row["position"];bounds=domains[expected[row["id"]]]
            self.assertTrue(all(bounds[0][i]<=point[i]<=bounds[1][i] for i in range(3)))

    def test_off_center_source_support_crosses_complete_authored_apertures(self):
        domains,sets,gates=district.policy()
        entities=[]
        def rectangle(ident,x0,x1,z0,z1,height):
            points=[[x0,height(z0),z0],[x0,height(z1),z1],[x1,height(z1),z1],[x1,height(z0),z0]]
            for index,corners in enumerate(((0,1,2),(0,2,3))):
                entities.append(dict(id=ident+str(index),kind='static',transform=dict(position=[0,0,0],rotation=[0,0,0],scale=[1,1,1]),
                                     floor=dict(vertices=[points[i]for i in corners])))
        rectangle('ramp',-3.048,3.048,40,46,lambda z:-1.606-.25*(z-43))
        rectangle('arch',-.9144,.9144,39,39.6,lambda z:0)
        rectangle('next',-3.9624,3.9624,26.8,27.5,lambda z:0)
        rectangle('west',-4,-3.3,26.8,32.5,lambda z:0)
        document=dict(entities=entities)
        manifest=dict(regions=[dict(id=ident,activation_bounds=bounds,working_set=sets[ident])for ident,bounds in domains.items()],transitions=gates)
        before=copy.deepcopy(document)
        floors=[level.floor_triangle(row)for row in entities]
        for x,z in ((1.4,43.035),(1.4,42.965),(.72,39.355),(.72,39.285),(-3.5,27.1622),(-3.5,27.0922),(-3.6926,27.15),(-3.6226,27.15)):
            heights=[level.floor_height([x,0,z],f)for f in floors];height=max(y for y in heights if y is not None)
            level.validate_actor_placement([x,height,z],[], 'off-center original fixture',floors=floors)
        audited=district.audit_gate_apertures(document,manifest)
        self.assertEqual(document,before)
        self.assertTrue(all(row['uncovered_samples']==0 for row in audited))
        original=copy.deepcopy(manifest)
        for gate in original['transitions']:
            pair={gate['from'],gate['to']}
            if pair in ({'ramp-lower','ramp-upper'},{'ramp-upper','main-interior'}):
                gate['bounds'][0][0]=-.65;gate['bounds'][1][0]=.65
            elif pair=={'rear-courtyard','next-passage'}:
                gate['bounds'][0][0]=-3;gate['bounds'][1][0]=3
            elif pair=={'rear-courtyard','west-return'}:
                gate['bounds'][0][2]=27.2
        missed={row['id']for row in district.audit_gate_apertures(document,original)if row['uncovered_samples']}
        for source,target in (('ramp-lower','ramp-upper'),('ramp-upper','main-interior'),('rear-courtyard','next-passage'),('rear-courtyard','west-return')):
            self.assertIn(source+'-to-'+target,missed)
            self.assertIn(target+'-to-'+source,missed)

    def test_activation_audit_detects_edges_missing_from_the_gate_list(self):
        domains,sets,gates=district.policy()
        entities=[]
        for name,x0,x1,z0,z1 in (('west-cut',-4.2,-3.4,26.3,26.7),
                                ('east-cut',3.3,4,29.6,30),('east-north',4.1,6,27.1,27.7),
                                ('east-south',4.1,6,32.1,32.8)):
            points=[[x0,0,z0],[x0,0,z1],[x1,0,z1],[x1,0,z0]]
            for index,corners in enumerate(((0,1,2),(0,2,3))):
                entities.append(dict(id=name+str(index),kind='static',
                    transform=dict(position=[0,0,0],rotation=[0,0,0],scale=[1,1,1]),
                    floor=dict(vertices=[points[i] for i in corners])))
        manifest=dict(visibility='authored-sets',regions=[dict(id=name,activation_bounds=bounds,working_set=sets[name])
            for name,bounds in domains.items()],transitions=gates)
        document=dict(entities=entities)
        self.assertTrue(all(not row['missing_edge'] for row in district.audit_activation_adjacency(document,manifest)))
        removed={frozenset(('next-passage','west-return')),frozenset(('rear-courtyard','lower-east-alley'))}
        manifest['transitions']=[row for row in gates if frozenset((row['from'],row['to'])) not in removed]
        missing={(row['source'],row['target']) for row in district.audit_activation_adjacency(document,manifest) if row['missing_edge']}
        for a,b in (('next-passage','west-return'),('rear-courtyard','lower-east-alley')):
            self.assertIn((a,b),missing);self.assertIn((b,a),missing)

    def test_adjacency_support_includes_thick_stair_landings(self):
        document=dict(entities=[dict(id='landing',kind='static',
            transform=dict(position=[0,0,0],rotation=[0,0,0],scale=[1,1,1]),
            collider=dict(shape='box',center=[0,-.5,0],half_size=[1,.5,1]))])
        manifest=dict(visibility='authored-sets',regions=[
            dict(id='left',activation_bounds=[[-1,-1,-1],[0,3,1]]),
            dict(id='right',activation_bounds=[[0,-1,-1],[1,3,1]])],transitions=[])
        rows=district.audit_activation_adjacency(document,manifest)
        self.assertEqual({(r['source'],r['target']) for r in rows},{('left','right'),('right','left')})
        self.assertTrue(all(r['missing_edge'] and r['valid_samples'] for r in rows))


class DistrictPublicationTests(unittest.TestCase):
    def setUp(self):
        (ROOT/"build").mkdir(exist_ok=True)
        temporary=tempfile.TemporaryDirectory(prefix="district-test-",dir=ROOT/"build")
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.content=self.root/"content";self.content.mkdir()
        self.source=self.content/"baseline.json";self.manifest=self.content/"baseline.mission.json"
        self.full=self.content/"full.json";self.annotations=self.root/"annotations.json";self.topology=self.root/"topology.json"
        self.output=self.content/"thief_bafford_district.json"
        transform=lambda p,s=[1,1,1]:dict(position=p,rotation=[0,0,0],scale=s)
        self.document=dict(version=2,title="Original edited fixture",assets=[dict(id="box",type="primitive",shape="box")],materials=[dict(id="stone",color=[.5,.5,.5,1])],
                           entities=[dict(id=side+"-floor",kind="static",model="box",material="stone",transform=transform([x,-.1,0],[2,.2,2]),collider=dict(shape="box",center=[0,0,0],half_size=[.5,.5,.5])) for side,x in (("west",-1),("east",1))]+
                           [dict(id="spawn",kind="spawn",transform=transform([-1,0,0])),dict(id="light",kind="light",transform=transform([-1,1,0]),radius=3,intensity=.5)])
        self.partition=dict(version=1,source=self.source.name,entry_region="west",visibility="all-regions",regions=[
            dict(id="west",bounds=[[-2,-.2,-1],[.01,2,1]],entities=["west-floor","spawn","light"],neighbors=["east"]),
            dict(id="east",bounds=[[-.01,-.2,-1],[2,2,1]],entities=["east-floor"],neighbors=["west"])])
        for p,data in ((self.source,self.document),(self.manifest,self.partition),(self.full,dict(version=2,assets=[],materials=[],entities=[])),(self.annotations,{}),(self.topology,dict(cells=[]))):p.write_bytes(json_bytes(data))

    def derived(self,*args):
        document=copy.deepcopy(self.document);document.update(authoring_profile="streamed-mission",title="Synthetic district",stream_district={})
        return document,copy.deepcopy(self.partition),{},dict(source_cells=2)

    def convert(self,**kwargs):
        with patch.object(district,"derive",side_effect=self.derived):
            return district.convert(self.source,self.manifest,self.full,self.annotations,self.topology,self.output,content_root=self.content,**kwargs)

    def test_publication_uses_actual_cook_and_preserves_all_originals(self):
        originals={p:p.read_bytes() for p in (self.source,self.manifest,self.full,self.annotations,self.topology)}
        report=self.convert()
        self.assertGreater(report["cooked"]["package_bytes"],0)
        self.assertEqual(json.loads(self.output.read_bytes())["authoring_profile"],"streamed-mission")
        self.assertEqual(json.loads(self.output.with_suffix('.mission.json').read_bytes())["source"],self.output.name)
        for p,raw in originals.items():self.assertEqual(p.read_bytes(),raw)

    def test_pure_derive_preserves_artist_geometry_and_deterministic_source_provenance(self):
        document=copy.deepcopy(self.document)
        document['stream_expansion']=dict(preserved_cells=[1],added_cells=[])
        before=copy.deepcopy(document)
        state=level.validate(document,self.content,streaming=True)
        domains,_,_=district.policy()
        selected={region:(index+2,) for index,region in enumerate(district.CELL_REGIONS)}
        new=[];meshes={};provenance={};counts={}
        for region,(cid,) in selected.items():
            domain=domains['ramp-lower' if region=='central-ramp' else region]
            x,z=(domain[0][0]+domain[1][0])*.5,(domain[0][2]+domain[1][2])*.5
            points=[[0,-.5,42],[0,-1,44],[1,-1,44]] if region=='central-ramp' else [[x,0,z],[x,0,z+.5],[x+.5,0,z+.5]]
            ident='source-'+region;model='mesh-'+ident
            new.append(dict(id=ident,kind='static',model=model,material='stone',transform=dict(position=[0,0,0],rotation=[0,0,0],scale=[1,1,1])))
            meshes[model]=dict(vertices=points,normals=[[0,1,0]]*3,indices=[0,1,2])
            provenance[(ident,0)]=dict(source_scene='full.json',source_entity_id=ident,source_model_id=model,source_triangle=17,cell=cid,polygon=4)
            counts[cid]=1
        topology=dict(cells=[dict(id=1,portals=[])]+[dict(id=cids[0],portals=[dict(target=1)]) for cids in selected.values()])
        annotations={'source-model':[[cids[0],4] for cids in selected.values()]}
        fake=(new,meshes,state['materials'],provenance,[],counts,{})
        with patch.object(district,'CELL_REGIONS',selected),patch.object(expansion,'select_new_geometry',return_value=fake):
            first=district.derive(document,state,self.partition,{},annotations,topology,self.content,'baseline.json','full.json')
            reordered=dict(state,by_id=dict(reversed(list(state['by_id'].items()))))
            second=district.derive(document,reordered,self.partition,{},annotations,topology,self.content,'baseline.json','full.json')
        self.assertEqual(document,before)
        self.assertEqual(first,second)
        result,manifest,payloads,report=first
        self.assertEqual(result['authoring_profile'],'streamed-mission')
        self.assertEqual(len(manifest['regions']),10)
        self.assertTrue(payloads)
        floors=[e for e in result['entities'] if 'floor'in e]
        self.assertTrue(floors)
        self.assertTrue(all(e['source']['source_triangle']==17 for e in floors))
        self.assertEqual({e['kind'] for e in result['entities'] if e['id'].startswith('district-demonstration')},{'door','loot'})
        homes={e:r['id'] for r in manifest['regions'] for e in r['entities']}
        self.assertEqual(homes[district.DOOR_ID],'ramp-upper')
        self.assertEqual(homes[district.LOOT_ID],'main-interior')
        self.assertEqual(set(homes),{e['id'] for e in result['entities']})
        for region in ('west','east'):
            self.assertEqual(next(r for r in report['regions'] if r['id']==region)['triangles'],12)

    def test_existing_edits_require_explicit_replace_and_recovery(self):
        self.convert();self.output.write_bytes(b'artist edited district')
        with self.assertRaisesRegex(ValueError,"edits exist"):
            self.convert()
        self.assertEqual(self.output.read_bytes(),b'artist edited district')
        self.convert(replace=True)
        recovered=list((self.root/'.editor/replaced-districts').glob('*/thief_bafford_district.json'))
        # Editor state location is resolved by the transaction owner.
        if not recovered:recovered=list((ProjectLevels(self.root).editor/'replaced-districts').glob('*/thief_bafford_district.json'))
        self.assertEqual(len(recovered),1)
        self.assertEqual(recovered[0].read_bytes(),b'artist edited district')

    def test_original_or_annotation_race_aborts_before_any_output(self):
        for target in (self.source,self.annotations):
            with self.subTest(target=target.name):
                original_publish=ProjectLevels.publish
                def raced(project,changes,expected):
                    target.write_bytes(target.read_bytes()+b' ')
                    return original_publish(project,changes,expected)
                with patch.object(ProjectLevels,"publish",raced):
                    with self.assertRaisesRegex(ValueError,"changed"):
                        self.convert()
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_suffix('.mission.json').exists())

    def test_original_paths_cannot_be_outputs(self):
        with self.assertRaisesRegex(ValueError,"preserve all original"):
            district.convert(self.output,self.manifest,self.full,self.annotations,self.topology,self.output,content_root=self.content)


if __name__=='__main__':unittest.main()
