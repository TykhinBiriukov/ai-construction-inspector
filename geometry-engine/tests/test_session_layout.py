"""Raw/processed separation, portable references and cross-stage compatibility."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from geometry_engine.session_layout import (original_directory, processed_session,
    raw_original_directory, session_reference)
from geometry_engine.validation import session_file
from geometry_engine.mask_projection import MaskProjectionService
from geometry_engine.mesh_segmentation import MeshSegmentationService
from test_mask_projection import MaskProjectionTests


class SplitLayoutTests(unittest.TestCase):
    def test_raw_input_reconstruction_and_validation_write_only_processed(self):
        from test_reconstruction import CaptureFixture
        from geometry_engine.reconstruction import ReconstructionService, ReconstructionSettings
        from geometry_engine.validation import SessionValidationService
        fixture=CaptureFixture();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.capture()
        raw=fixture.session/'raw'/'scan';raw.mkdir(parents=True)
        fixture.original.rename(raw/'original')
        processed=fixture.session/'processed'/'scan'
        SessionValidationService().validate(raw)
        self.assertTrue((processed/'validation/report.json').is_file())
        report=ReconstructionService(ReconstructionSettings(create_preview=False)).reconstruct(raw)
        self.assertGreater(report['triangle_count'],0)
        self.assertTrue((processed/'reconstruction/mesh.ply').is_file())
        self.assertEqual([p.name for p in raw.iterdir()],['original'])

    def test_projection_and_fusion_read_raw_without_copying_or_modifying_it(self):
        fixture = MaskProjectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.session
        raw = root/'raw'/'scan'
        processed = root/'processed'/'scan'
        raw.mkdir(parents=True)
        processed.mkdir(parents=True)
        fixture.original.rename(raw/'original')
        (root/'mesh_preprocessing').rename(processed/'mesh_preprocessing')
        fixture.segmentation.rename(processed/'segmentation')
        source = raw/'original'
        before = {p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in source.rglob('*') if p.is_file()}
        report = MaskProjectionService(fixture.settings).project(raw)
        self.assertEqual(report['status'],'completed')
        self.assertTrue((processed/'mask_projection/report.json').is_file())
        self.assertFalse((raw/'mask_projection').exists())
        self.assertEqual(session_file(processed,processed,'original/scene.json'),source/'scene.json')
        self.assertEqual(session_reference(source/'scene.json',processed),'original/scene.json')
        MeshSegmentationService(projection_settings=fixture.settings).segment(processed)
        self.assertTrue((processed/'mesh_segmentation/report.json').is_file())
        after = {p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in source.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertFalse((processed/'original').exists())
        with self.assertRaises(ValueError):
            session_file(processed,processed,'original/../other/file')

    def test_derived_input_does_not_replace_raw_for_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); raw=root/'raw'/'scan'; processed=root/'processed'/'scan'
            (raw/'original').mkdir(parents=True);(processed/'input').mkdir(parents=True)
            self.assertEqual(processed_session(raw),processed)
            self.assertEqual(original_directory(processed),processed/'input')
            self.assertEqual(raw_original_directory(processed),raw/'original')


if __name__ == '__main__':unittest.main()
