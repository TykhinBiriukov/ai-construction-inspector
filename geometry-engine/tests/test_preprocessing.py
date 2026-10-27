"""Integration tests for the reusable preprocessing API and package boundary."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from geometry_engine import preprocess_mesh
from geometry_engine.mesh import read_mesh, write_mesh


ENGINE = Path(__file__).resolve().parents[1]


class PreprocessingIntegrationTests(unittest.TestCase):
    def test_plain_mesh_needs_no_frames_or_dataset_manifest(self):
        with tempfile.TemporaryDirectory(dir=ENGINE) as directory:
            root = Path(directory)
            source = root / "input.ply"
            vertices = np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0], [0., 0, 0]])
            faces = np.array([[0, 1, 2], [3, 1, 2], [0, 0, 1]])
            write_mesh(source, vertices, np.full((4, 4), 255, dtype=np.uint8), faces)
            source_hash = hashlib.sha256(source.read_bytes()).digest()
            output = root / "prepared"
            report = preprocess_mesh(source, output)
            self.assertEqual(report["cleanup"]["prepared_vertices"], 3)
            self.assertEqual(report["cleanup"]["prepared_faces"], 1)
            self.assertNotIn("scene_id", report)
            self.assertNotIn("projection_validation", report)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), source_hash)
            actual, _, actual_faces = read_mesh(output / "mesh.ply")
            self.assertEqual(actual.shape, (3, 3))
            self.assertEqual(actual_faces.shape, (1, 3))
            for name in report["outputs"].values():
                self.assertTrue((output / name).is_file(), name)
            stored = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, report)
            before = (output / "report.json").read_bytes()
            with self.assertRaises(FileExistsError):
                preprocess_mesh(source, output)
            self.assertEqual((output / "report.json").read_bytes(), before)

    def test_library_and_cli_do_not_import_dataset_or_plotting_libraries(self):
        result = subprocess.run(
            [sys.executable, "-c", (
                "import sys; import geometry_engine; import geometry_engine.cli; "
                "assert not any(n.split('.')[0] in {'PIL','matplotlib','tools'} for n in sys.modules)"
            )], cwd=ENGINE, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsupported_units_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory(dir=ENGINE) as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                preprocess_mesh(root / "missing.ply", root / "prepared", unit="millimeter")
            self.assertFalse((root / "prepared").exists())


if __name__ == "__main__":
    unittest.main()
