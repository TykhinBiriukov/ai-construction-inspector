"""Frame filtering behavior on small real image fixtures and known camera poses."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageFilter
from scipy.spatial.transform import Rotation

from geometry_engine.frame_filter import FrameFilterService, FrameFilterSettings, camera_motion, image_quality
from run_geometry import run_session


class FrameFilterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name)
        self.original = self.session / "original"
        (self.original / "frames").mkdir(parents=True)
        self.capture = {"schema_version":"1.0", "session_id":"test", "coordinate_system":"arkit",
                        "matrix_storage":"rows", "length_unit":"meter", "frames_directory":"frames", "frame_count":0}
        self.rows = []
        self.save_capture()

    def save_capture(self):
        (self.original / "capture.json").write_text(json.dumps(self.capture), encoding="utf-8")

    def frame(self, identity, *, x=0., yaw=0., roll=0., timestamp=None, kind="sharp", pose=True):
        folder = self.original / "frames" / identity
        folder.mkdir()
        y, u = np.mgrid[:64, :64]
        image = Image.fromarray(np.uint8(40+((u//4+y//4)%2)*160))
        if kind == "blurred":
            image = image.filter(ImageFilter.GaussianBlur(5))
        if kind == "dark":
            image = Image.new("L", (64,64), 0)
        if kind == "bright":
            image = Image.new("L", (64,64), 255)
        image.save(folder / "rgb.png")
        if kind == "corrupt":
            (folder / "rgb.png").write_bytes(b"not an image")
        matrix = np.eye(4)
        matrix[:3,:3] = Rotation.from_euler("yz", [yaw,roll], degrees=True).as_matrix()
        matrix[0,3] = x
        metadata = {"frame_id":identity, "timestamp_s":float(len(self.rows)) if timestamp is None else timestamp,
                    "rgb":{"path":"rgb.png", "width":64, "height":64},
                    "intrinsics":{"K":[[50,0,32],[0,50,32],[0,0,1]]},
                    "pose":{"T_world_from_camera":matrix.tolist()} if pose else None}
        (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        self.rows.append({"frame_id":identity, "metadata":f"frames/{identity}/metadata.json"})
        self.capture["frame_count"] = len(self.rows)
        self.save_capture()
        return folder

    def result(self):
        return json.loads((self.session / "selected_frames" / "selected_frames.json").read_text())

    def test_selects_first_then_translation_or_view_change(self):
        self.frame("000001")
        self.frame("000002",x=.1)
        self.frame("000003",x=.31)
        self.frame("000004",x=.31,yaw=20.)
        before = {path:path.read_bytes() for path in self.original.rglob("*") if path.is_file()}
        report = FrameFilterService().filter(self.session)
        self.assertEqual([r["frame_id"] for r in self.result()["frames"]], ["000001","000003","000004"])
        self.assertEqual(report["reason_counts"]["redundant_view"],1)
        self.assertEqual(report["selected_count"],3)
        for row in self.result()["frames"]:
            self.assertTrue((self.session / row["rgb"]).is_file())
            self.assertTrue((self.session / row["metadata"]).is_file())
        self.assertEqual(before,{path:path.read_bytes() for path in self.original.rglob("*") if path.is_file()})

    def test_compares_to_last_selected_and_ignores_in_plane_roll(self):
        self.frame("000001")
        self.frame("000002",x=.2)
        self.frame("000003",x=.35)
        self.frame("000004",x=.35,roll=90.)
        FrameFilterService().filter(self.session)
        self.assertEqual([r["frame_id"] for r in self.result()["frames"]],["000001","000003"])

    def test_processes_timestamp_order_not_directory_order(self):
        self.frame("000001",x=.5,timestamp=2.)
        self.frame("000002",timestamp=1.)
        FrameFilterService().filter(self.session)
        self.assertEqual([r["frame_id"] for r in self.result()["frames"]],["000002","000001"])

    def test_quality_rejections_and_invalid_pose_do_not_advance_reference(self):
        for index,kind in enumerate(["dark","bright","blurred","corrupt"],1):
            self.frame(f"{index:06d}",kind=kind)
        self.frame("000005",pose=False)
        self.frame("000006")
        report=FrameFilterService().filter(self.session)
        self.assertEqual([r["frame_id"] for r in self.result()["frames"]],["000006"])
        for reason in ["too_dark","too_bright","blurry","invalid_image","invalid_metadata"]:
            self.assertEqual(report["reason_counts"][reason],1)

    def test_sharpness_distinguishes_blur(self):
        sharp=self.frame("000001")
        blurred=self.frame("000002",kind="blurred")
        a=image_quality(sharp/'rgb.png',(64,64),640)
        b=image_quality(blurred/'rgb.png',(64,64),640)
        self.assertGreater(a['sharpness'],20)
        self.assertLess(b['sharpness'],20)

    def test_all_rejected_writes_failed_report_without_false_success(self):
        self.frame("000001",kind="dark")
        with self.assertRaisesRegex(ValueError,"No usable frames"):
            FrameFilterService().filter(self.session)
        report=json.loads((self.session/'selected_frames'/'report.json').read_text())
        self.assertEqual(report['status'],'failed')
        self.assertEqual(self.result()['frames'],[])

    def test_existing_results_and_original_are_protected(self):
        self.frame("000001")
        service=FrameFilterService()
        service.filter(self.session)
        before=(self.session/'selected_frames'/'report.json').read_bytes()
        with self.assertRaises(FileExistsError):
            service.filter(self.session)
        self.assertEqual(before,(self.session/'selected_frames'/'report.json').read_bytes())
        for output in [self.original/'selected',self.session.parent/'outside-filter']:
            with self.assertRaises(ValueError):
                service.filter(self.session,output)
        self.assertFalse(any(p.name.startswith('.frame-filter-') for p in self.session.iterdir()))

    def test_structured_index_bad_rows_paths_and_dimensions(self):
        self.frame("000001")
        bad=self.frame("000002",x=1.)
        metadata=json.loads((bad/'metadata.json').read_text())
        metadata['rgb']['width']=32
        (bad/'metadata.json').write_text(json.dumps(metadata))
        scene={'scene_id':'test','coordinate_system':{'length_unit':'meter'},'frame_count':4,'frame_index':'frames.jsonl'}
        (self.original/'scene.json').write_text(json.dumps(scene))
        lines=[json.dumps(self.rows[0]),json.dumps(self.rows[1]),'{',json.dumps({'frame_id':'000003','metadata':'../outside.json'})]
        (self.original/'frames.jsonl').write_text('\n'.join(lines))
        report=FrameFilterService().filter(self.session)
        self.assertEqual(report['selected_count'],1)
        self.assertEqual(report['reason_counts']['invalid_metadata'],2)
        self.assertEqual(report['reason_counts']['invalid_image'],1)

    def test_invalid_thresholds(self):
        for values in [{'min_translation_m':0},{'min_view_angle_deg':181},{'min_sharpness':-1},
                       {'min_brightness':250},{'quality_max_dimension':2},{'min_sharpness':float('nan')}]:
            with self.subTest(values=values),self.assertRaises(ValueError):
                FrameFilterSettings(**values)

    def test_frames_only_does_not_reprocess_mesh(self):
        self.frame("000001")
        with patch('run_geometry.preprocess_mesh') as preprocess,patch('run_geometry.SessionValidationService') as validator:
            result=run_session(self.session,frames_only=True)
        preprocess.assert_not_called()
        validator.assert_not_called()
        self.assertEqual(result['selected_frames']['selected_count'],1)

    def test_known_camera_angles(self):
        a=np.eye(4)
        b=np.eye(4);b[:3,:3]=Rotation.from_euler('y',90,degrees=True).as_matrix();b[0,3]=2
        distance,angle=camera_motion(a,b)
        self.assertAlmostEqual(distance,2)
        self.assertAlmostEqual(angle,90)
