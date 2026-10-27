"""Protocol fixtures exercise the real Meta decoder without calling Meta."""

import base64
from io import BytesIO
import json
import unittest

import httpx
import numpy as np
from PIL import Image

from geometry_engine.sam_backend import Sam3Backend, Sam3UnavailableError


# Official codec's encoding of [[1, 0], [0, 1]], generated once as a fixture.
MASK = "!!!!!&T:u9`"
WIRE = f"<0f>1<|box;x1=1;y1=1;x2=2;y2=2;w=5;h=4;c=0.9|><|mask;x=0;y=0;c=0.9;data=2,2,{MASK}|>\n"
LANE = {"item_id": "msg_1", "output_index": 0, "content_index": 0}


def stream(text=WIRE, *, terminal="response.completed", done=True):
    events = [{"type": "response.output_text.delta", **LANE, "delta": text[:31]},
              {"type": "response.output_text.delta", **LANE, "delta": text[31:]}]
    if done:
        events.append({"type": "response.output_text.done", **LANE, "text": text})
    if terminal:
        events.append({"type": terminal})
    return ": heartbeat\n\n" + "".join("data: " + json.dumps(e) + "\n\n" for e in events)


class SamBackendTests(unittest.TestCase):
    def backend(self, content, *, status=200, handler=None):
        transport = httpx.MockTransport(handler or (lambda request: httpx.Response(
            status, text=content, headers={"content-type": "text/event-stream"})))
        return Sam3Backend("test-secret", transport=transport)

    def test_real_parser_places_box_local_mask_and_separates_prompts(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            self.assertEqual(request.url, "https://api.meta.ai/v1/responses")
            self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            return httpx.Response(200, text=stream(), headers={"content-type": "text/event-stream"})
        detections = self.backend("", handler=handler).predict(Image.new("RGB", (5, 4)), ("wall", "door"))
        self.assertEqual([d.label for d in detections], ["wall", "door"])
        expected = np.zeros((4, 5), dtype=bool)
        expected[1, 1] = expected[2, 2] = True
        np.testing.assert_array_equal(detections[0].mask, expected)
        self.assertEqual(detections[0].object_id, "1")
        self.assertAlmostEqual(detections[0].score, .9)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["model"], "sam-3.1")
        self.assertEqual(requests[0]["metadata"], {"include_confidence": "true", "mask_encoding": "one_bit"})
        self.assertEqual(requests[1]["input"][0]["content"][0]["text"], "door")

    def test_uploaded_png_has_identical_pixels_without_exif(self):
        source = Image.fromarray(np.arange(60, dtype=np.uint8).reshape(4, 5, 3))
        exif = Image.Exif()
        exif[274] = 6
        source.info["exif"] = exif.tobytes()
        def handler(request):
            data = json.loads(request.content)["input"][0]["content"][1]["image_url"]
            with Image.open(BytesIO(base64.b64decode(data.split(",", 1)[1]))) as uploaded:
                self.assertEqual(uploaded.size, (5, 4))
                self.assertFalse(uploaded.getexif())
                np.testing.assert_array_equal(np.asarray(uploaded), np.asarray(source))
            return httpx.Response(200, text=stream(""), headers={"content-type": "text/event-stream"})
        self.assertEqual(self.backend("", handler=handler).predict(source, ("wall",)), [])

    def test_completed_empty_output_is_a_valid_negative_result(self):
        self.assertEqual(self.backend(stream("")).predict(Image.new("RGB", (5, 4)), ("door",)), [])

    def test_content_part_done_only_is_parsed_and_guarded_before_decoding(self):
        def content_part(text):
            events = [{"type": "response.content_part.done", **LANE,
                       "part": {"type": "output_text", "text": text}},
                      {"type": "response.completed"}]
            return "".join("data: " + json.dumps(e) + "\n\n" for e in events)
        detections = self.backend(content_part(WIRE)).predict(Image.new("RGB", (5, 4)), ("wall",))
        self.assertEqual(int(detections[0].mask.sum()), 2)
        for wire in (WIRE.replace("w=5", "w=9"), WIRE.replace("data=2,2", "data=999999,999999")):
            with self.subTest(wire=wire[:60]), self.assertRaises(Sam3UnavailableError):
                self.backend(content_part(wire)).predict(Image.new("RGB", (5, 4)), ("wall",))

    def test_incomplete_failed_or_unfinalized_stream_never_returns_masks(self):
        for content in [stream(terminal=None), stream(terminal="response.incomplete"),
                        stream(terminal="response.failed"), stream(done=False),
                        stream().rstrip("\n")]:
            with self.subTest(content=content[-60:]), self.assertRaises(Sam3UnavailableError):
                self.backend(content).predict(Image.new("RGB", (5, 4)), ("wall",))

    def test_bad_masks_dimensions_missing_confidence_and_text_are_rejected(self):
        for text in [WIRE.replace("w=5", "w=8"), WIRE.replace(";c=0.9", ""),
                     WIRE.replace(MASK, "!invalid"), "I see a wall\n",
                     WIRE.replace("data=2,2", "data=999999,999999")]:
            with self.subTest(text=text[:60]), self.assertRaises(Sam3UnavailableError):
                self.backend(stream(text)).predict(Image.new("RGB", (5, 4)), ("wall",))

    def test_http_error_and_timeout_do_not_leak_response_or_key(self):
        for status in (400, 401, 403, 404, 429, 500, 302):
            with self.subTest(status=status), self.assertRaises(Sam3UnavailableError) as caught:
                self.backend("test-secret upstream-private", status=status).predict(
                    Image.new("RGB", (5, 4)), ("wall",))
            self.assertNotIn("test-secret", str(caught.exception))
            self.assertNotIn("upstream-private", str(caught.exception))
        def timeout(request):
            raise httpx.ReadTimeout("test-secret", request=request)
        with self.assertRaises(Sam3UnavailableError) as caught:
            self.backend("", handler=timeout).predict(Image.new("RGB", (5, 4)), ("wall",))
        self.assertNotIn("test-secret", str(caught.exception))

    def test_configuration_and_multiconcept_prompt_rejected_without_request(self):
        with self.assertRaises(Sam3UnavailableError):
            Sam3Backend("")
        for prompts in [("wall, door",), ("",), ("door+window",)]:
            with self.subTest(prompts=prompts), self.assertRaises(ValueError):
                self.backend("").predict(Image.new("RGB", (5, 4)), prompts)


if __name__ == "__main__":
    unittest.main()
