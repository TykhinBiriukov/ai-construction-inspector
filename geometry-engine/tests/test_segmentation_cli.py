"""SAM CLI contract tests; never call the external Meta API."""

import os
import unittest
from unittest.mock import patch

from scripts import run_segmentation


class SegmentationCliTests(unittest.TestCase):
    def test_cli_missing_key_exits_before_model_construction(self):
        with patch("sys.argv", ["run_segmentation.py", "--session", "test-session", "--max-frames", "3"]), \
                patch.dict(os.environ, {"MODEL_API_KEY": ""}), \
                patch.object(run_segmentation, "load_dotenv"), \
                patch.object(run_segmentation, "Sam3Backend") as backend:
            with self.assertRaises(SystemExit) as failure:
                run_segmentation.main()
        self.assertEqual(failure.exception.code, 1)
        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
