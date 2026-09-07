"""Strict raw N64 framebuffer decoding and preflight authoring validation."""
import contextlib
import copy
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tools import capture_ares as capture

ROOT = Path(__file__).resolve().parents[1]


def frame(ident=1, width=2, height=1, rows=None):
    rows = rows if rows is not None else ["f80007c1"]
    return "\n".join([f"DL64 capture_begin id={ident} width={width} height={height} format=rgba5551",
                      *[f"DL64 capture_row id={ident} y={y} data={row}" for y,row in enumerate(rows)],
                      f"DL64 capture_end id={ident}"])


class CaptureDecodeTests(unittest.TestCase):
    def test_rgb5551_channels_ignore_coverage_alpha(self):
        log=frame(width=8, rows=["f800f80107c007c1003e003fffff0000"])
        self.assertEqual(capture.decode_captures(log), {1:(8,1,bytes([
            255,0,0, 255,0,0, 0,255,0, 0,255,0, 0,0,255, 0,0,255, 255,255,255, 0,0,0]))})

    def test_rows_and_frames_may_arrive_out_of_order_with_unrelated_logs(self):
        rows=["DL64 capture_begin id=2 width=1 height=2 format=rgba5551",
              "DL64 profile slot=audio us=37", "DL64 capture_row id=2 y=1 data=003f",
              "Ares diagnostic", "DL64 capture_row id=2 y=0 data=f801", "DL64 capture_end id=2"]
        log="\n".join(rows)+"\n"+frame()+"\nDL64 capture_complete views=2\nordinary tail log"
        decoded=capture.decode_captures(log, expected_count=2)
        self.assertEqual(decoded[2], (1,2,bytes([255,0,0,0,0,255])))
        self.assertEqual(set(decoded), {1,2})

    def test_missing_duplicate_mismatched_and_malformed_rows_fail(self):
        valid=frame(height=2,rows=["f80007c1","003fffff"])
        first="DL64 capture_row id=1 y=0 data=f80007c1"
        mutations=[(valid.replace(first+"\n", ""),"Incomplete"),
                   (valid.replace(first,first+"\n"+first),"duplicated"),
                   (valid.replace("row id=1 y=0", "row id=2 y=0"),"out of bounds"),
                   (valid.replace("y=0", "y=2"),"out of bounds"),
                   (valid.replace("y=0", "y=-1"),"Invalid capture row"),
                   (valid.replace("f80007c1", "f800"),"missing pixels"),
                   (valid.replace("f80007c1", "f80007xz"),"Invalid capture row"),
                   (valid.replace("DL64 capture_end id=1", "DL64 capture_end id=2"),"Incomplete"),
                   (valid.rsplit("\n",1)[0],"end of log"),
                   ("DL64 capture_row id=1 y=0 data=f800", "Invalid capture row"),
                   ("DL64 capture_end id=1", "Incomplete")]
        for log,message in mutations:
            with self.subTest(log=log), self.assertRaisesRegex(ValueError,message):
                capture.decode_captures(log)

    def test_dimensions_frame_identity_overlap_and_missing_frames_fail(self):
        for log in [frame(ident=0),frame(ident=9),frame(width=0),frame(width=641),
                    frame(height=0),frame(height=481),frame()+"\n"+frame(),
                    frame().replace("DL64 capture_row", "DL64 capture_begin id=2 width=1 height=1 format=rgba5551\nDL64 capture_row",1),
                    frame().replace("format=rgba5551", "format=rgba8888")]:
            with self.subTest(log=log), self.assertRaises(ValueError):
                capture.decode_captures(log)
        for log,count in [(frame(ident=2),1),(frame(ident=2),None),(frame()+"\n"+frame(ident=3),3),
                          (frame(),2)]:
            with self.subTest(log=log,count=count), self.assertRaises(ValueError):
                capture.decode_captures(log, expected_count=count)
        with self.assertRaisesRegex(ValueError,"Unexpected.*dimensions"):
            capture.decode_captures(frame(), expected_dimensions=(320,240))

    def test_completion_unknown_records_and_empty_logs_fail(self):
        for log in ["unrelated log", "DL64 capture_begin", "DL64 capture_complete views=0",
                    frame()+"\nDL64 capture_complete views=2",frame()+"\nDL64 capture_whatever foo",
                    frame()+"\nDL64 capture_complete views=1\nDL64 capture_complete views=1",
                    frame()+"\nDL64 capture_complete views=1\n"+frame(ident=2)]:
            with self.subTest(log=log), self.assertRaises(ValueError):
                capture.decode_captures(log)
        for count in (0,9,True,1.5):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError,"count"):
                capture.decode_captures(frame(), expected_count=count)


class CapturePreflightTests(unittest.TestCase):
    def setUp(self):
        self.view={"id":"courtyard", "position":[1,0,3], "yaw":120, "pitch":4}

    def test_view_ids_types_coordinates_and_count(self):
        self.assertEqual(capture.validate_views({"views":[self.view]}), [self.view])
        self.assertEqual(len(capture.validate_views({"views":[dict(self.view,door_open=True)]})), 1)
        for change in [{"id":"../escape"},{"id":"CON"},{"id":"naïve"},{"id":"a"*65},
                       {"id":4},{"position":[1,2]},{"position":"123"},{"position":[True,0,0]},
                       {"position":[1025,0,0]},{"yaw":float("nan")},{"pitch":float("inf")},
                       {"pitch":91},{"door_open":1},{"yaw":None}]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError,"Capture view"):
                capture.validate_views({"views":[dict(self.view,**change)]})
        for data in [None,[],{}, {"views":None},{"views":[]},{"views":[self.view]*9}, {"views":[42]}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                capture.validate_views(data)
        for name in ("courtyard","Courtyard"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError,"Duplicate"):
                capture.validate_views({"views":[self.view,dict(self.view,id=name)]})

    def test_bad_timeout_and_views_do_not_build_or_launch(self):
        builder=types.ModuleType("build")
        builder.build_rom=Mock(side_effect=AssertionError("Must not build invalid captures"))
        builder.scene_paths=Mock(side_effect=AssertionError("Must validate first"))
        smoker=types.ModuleType("smoke_ares")
        smoker.exercise=Mock(side_effect=AssertionError("Must not launch invalid captures"))
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules,{"build":builder,"smoke_ares":smoker}):
            level=Path(temporary)/"scene.json"
            level.write_text(json.dumps({"views":[self.view]}))
            for timeout in ("0","-1","nan","inf"):
                error=io.StringIO()
                with self.subTest(timeout=timeout),contextlib.redirect_stderr(error):
                    self.assertEqual(capture.main(["--level",str(level),"--timeout",timeout]),1)
                self.assertIn("positive finite",error.getvalue())
            for contents in ("{}", '{"views": [42]}', '{"views": []}', '{"views": "bad"}', "invalid json"):
                level.write_text(contents)
                with self.subTest(contents=contents),contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(capture.main(["--level",str(level)]),1)
            builder.build_rom.assert_not_called()
            builder.scene_paths.assert_not_called()
            smoker.exercise.assert_not_called()

    def test_animation_time_is_optional_and_bounded(self):
        for value in (0,.9,3600):
            view=dict(self.view,animation_time=value)
            self.assertEqual(capture.validate_views({'views':[view]}),[view])
        for value in (-1,3601,True,'1',float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'animation_time'):
                capture.validate_views({'views':[dict(self.view,animation_time=value)]})

    def test_character_clip_and_attention_capture_fields(self):
        view = dict(self.view, animation_clip="walk", head_yaw=45, head_pitch=-25)
        self.assertEqual(capture.validate_views({"views": [view]}), [view])
        for fields in ({"animation_clip": 'walk"'}, {"animation_clip": True},
                       {"head_yaw": 46}, {"head_pitch": -26}, {"head_yaw": float("nan")},
                       {"head_pitch": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                capture.validate_views({"views": [dict(self.view, **fields)]})

    def test_scene_output_mapping_and_content_boundaries(self):
        with patch.object(sys, "path", [str(ROOT/"tools"),*sys.path]):
            builder=importlib.import_module("build")
        source,output,default=builder.scene_paths(ROOT/"content/first_room.json")
        self.assertEqual((source,output,default),(ROOT/"content/first_room.json",ROOT/"build",True))
        source,output,default=builder.scene_paths(ROOT/"content/moonlit_courtyard.json")
        self.assertEqual((output,default),(ROOT/"build/scenes/moonlit_courtyard",False))
        for path in (ROOT/"escape.json",ROOT/"content/nested/scene.json",ROOT/"content/scene.obj",
                     ROOT/"content/two words.json",ROOT/"content/../escape.json",ROOT/"content/éclair.json"):
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,"content JSON"):
                builder.scene_paths(path)


if __name__ == "__main__":
    unittest.main()
