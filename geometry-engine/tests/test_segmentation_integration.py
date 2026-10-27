"""API -> service -> hosted protocol -> real decoder -> PNG, without networking."""

import json
from pathlib import Path
import tempfile
import unittest

import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from geometry_engine.sam_backend import Sam3Backend
from geometry_engine.segmentation import SegmentationService, SegmentationSettings
from geometry_engine_api.app import create_app
from geometry_engine_api.dependencies import get_segmentation_service, get_settings
from geometry_engine_api.services.archive import Settings


class SegmentationIntegrationTest(unittest.TestCase):
    def test_api_publishes_original_pixel_masks_from_meta_wire_response(self):
        engine = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=engine) as temporary:
            root = Path(temporary)
            session = root / "scan-01"
            frame = session / "original" / "frames" / "000001"
            frame.mkdir(parents=True)
            Image.new("RGB", (5, 4), (30, 80, 90)).save(frame / "rgb.png")
            (frame / "metadata.json").write_text(json.dumps({"frame_id": "000001", "rgb": {
                "path": "rgb.png", "width": 5, "height": 4}}), encoding="utf-8")
            (session / "selected_frames").mkdir()
            (session / "selected_frames" / "selected_frames.json").write_text(json.dumps({
                "schema_version": "1.0", "session_id": "scan-01", "path_base": "session_directory",
                "selected_count": 1, "frames": [{"frame_id": "000001", "timestamp_s": 1.0,
                    "rgb": "original/frames/000001/rgb.png", "metadata": "original/frames/000001/metadata.json"}]
            }), encoding="utf-8")
            # Known one-bit fixture: the two diagonal pixels in a 2x2 crop.
            wire = ("<0f>7<|box;x1=1;y1=1;x2=2;y2=2;w=5;h=4;c=0.9|>"
                    "<|mask;x=0;y=0;c=0.9;data=2,2,!!!!!&T:u9`|>\n")
            lane = {"item_id": "msg_1", "output_index": 0, "content_index": 0}
            events = [{"type": "response.output_text.delta", **lane, "delta": wire},
                      {"type": "response.output_text.done", **lane, "text": wire},
                      {"type": "response.completed"}]
            body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
            backend = Sam3Backend("test-only", transport=httpx.MockTransport(lambda request:
                httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})))
            service = SegmentationService(backend, SegmentationSettings(prompts=("window",), min_mask_pixels=1))
            app = create_app()
            app.dependency_overrides[get_settings] = lambda: Settings(data_dir=root)
            app.dependency_overrides[get_segmentation_service] = lambda: service
            with TestClient(app) as client:
                response = client.post("/sessions/segment", json={"session_id": "scan-01"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["segmentation"]["detection_count"], 1)
            output = session / "segmentation"
            results = json.loads((output / "detections.json").read_text(encoding="utf-8"))
            detection = results["frames"][0]["detections"][0]
            with Image.open(output / detection["mask"]) as mask:
                expected = np.zeros((4, 5), dtype=np.uint8)
                expected[1, 1] = expected[2, 2] = 255
                np.testing.assert_array_equal(np.asarray(mask), expected)
            self.assertEqual(detection["label"], "window")
            self.assertEqual(detection["provider_object_id"], "7")
            self.assertEqual(detection["bbox_xyxy"], [1, 1, 3, 3])


if __name__ == "__main__":
    unittest.main()
