"""Hosted Meta SAM 3.1 with the official meta-sam-parser decoder.

References: https://dev.meta.ai/docs/sam/segmenting and /sam/client-libraries.
One concept per request; no local model or CUDA runtime is required.
"""

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
import json
import math
import re
import time

import httpx
import numpy as np
from PIL import Image


MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


class Sam3UnavailableError(RuntimeError):
    """A sanitized configuration, transport, or model response error."""


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    mask: np.ndarray
    object_id: str | None = None


def _sse_events(response, deadline):
    """Read complete SSE events, including data split across several lines."""
    data, size = [], 0
    for line in response.iter_lines():
        size += len(line.encode("utf-8")) + 1
        if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
            raise Sam3UnavailableError("Meta SAM response exceeded its size or time limit")
        if line == "":
            if data:
                body = "\n".join(data)
                data = []
                if body == "[DONE]":
                    return
                event = json.loads(body)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise ValueError("Invalid SSE event")
                yield event
            continue
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise Sam3UnavailableError("Meta SAM response ended inside an SSE event")


def _validate_wire_sizes(text, width, height):
    # The parser omits source w/h and decodes while parsing. Guard first.
    for token in re.findall(r"<\|box;([^|]*)\|>", text):
        fields = dict(field.split("=", 1) for field in token.split(";") if "=" in field)
        if int(fields.get("w", 0)) != width or int(fields.get("h", 0)) != height:
            raise ValueError("SAM source dimensions differ from the input image")
    for token in re.findall(r"<\|mask;(.*?)\|>", text, re.DOTALL):
        match = re.search(r"(?:^|;)data=([0-9]+),([0-9]+),", token)
        if match is None:
            raise ValueError("SAM mask dimensions are missing")
        h, w = map(int, match.groups())
        if h <= 0 or w <= 0 or h * w > MAX_IMAGE_PIXELS:
            raise ValueError("SAM mask dimensions exceed the limit")


async def _parse_events(events, parser_module):
    async def source():
        for event in events:
            yield event
    parsed = parser_module.parse_responses_stream(source(), parser_module.image_segmentation_format())
    return await parsed.final_result()


class Sam3Backend:
    model = "sam-3.1"

    def __init__(self, api_key: str, *, timeout_s=120, transport=None):
        if not isinstance(api_key, str) or not api_key.strip() or "\n" in api_key or "\r" in api_key:
            raise Sam3UnavailableError("Set MODEL_API_KEY to a valid Meta Model API key")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("SAM timeout must be a positive finite number")
        self._api_key = api_key.strip()
        self.timeout_s = float(timeout_s)
        self._transport = transport

    @property
    def model_info(self):
        return {"name": self.model, "provider": "Meta Model API", "decoder": "meta-sam-parser"}

    def predict(self, image: Image.Image, prompts: tuple[str, ...]) -> list[Detection]:
        if not isinstance(image, Image.Image):
            raise TypeError("SAM input must be a PIL image")
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("SAM input exceeds 25 million pixels")
        if (not isinstance(prompts, (tuple, list)) or not 1 <= len(prompts) <= 32
                or any(not isinstance(p, str) or not p.strip() or len(p) > 120
                       or any(c in p for c in ",;+\n\r") for p in prompts)):
            raise ValueError("Send 1-32 short concepts, one concept per prompt")
        try:
            import meta_sam_parser as parser
        except ImportError:
            raise Sam3UnavailableError("Install meta-sam-parser from requirements.txt before running segmentation") from None
        # Drop EXIF; keep stored sensor pixels aligned with camera intrinsics.
        clean_image = Image.fromarray(np.asarray(image.convert("RGB")))
        encoded = BytesIO()
        clean_image.save(encoded, format="PNG")
        image_url = "data:image/png;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")
        detections = []
        with httpx.Client(timeout=self.timeout_s, follow_redirects=False, trust_env=False,
                          transport=self._transport) as client:
            for label in prompts:
                payload = {"model": self.model, "stream": True,
                           "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
                           "input": [{"type": "message", "role": "user", "content": [
                               {"type": "input_text", "text": label},
                               {"type": "input_image", "image_url": image_url}]}]}
                try:
                    deadline = time.monotonic() + self.timeout_s
                    with client.stream("POST", "https://api.meta.ai/v1/responses", json=payload,
                                       headers={"Authorization": f"Bearer {self._api_key}",
                                                "Accept": "text/event-stream"}) as response:
                        if response.status_code != 200:
                            messages = {401: "Meta SAM rejected MODEL_API_KEY", 403: "Meta SAM access is denied",
                                        404: "The Meta account does not have access to sam-3.1",
                                        429: "Meta SAM rate limit reached; retry later"}
                            raise Sam3UnavailableError(messages.get(response.status_code,
                                f"Meta SAM request failed with HTTP {response.status_code}"))
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            raise Sam3UnavailableError("Meta SAM returned an unexpected response format")
                        events = []
                        for event in _sse_events(response, deadline):
                            events.append(event)
                            if event["type"] in ("response.completed", "response.failed", "response.incomplete", "error"):
                                break
                    if not events or events[-1]["type"] != "response.completed":
                        raise Sam3UnavailableError("Meta SAM did not complete the response; no partial result was accepted")
                    parts = [e["delta"] for e in events if e["type"] == "response.output_text.delta"]
                    final_texts = [e["text"] for e in events if e["type"] == "response.output_text.done"]
                    final_texts.extend(e["part"]["text"] for e in events
                                       if e["type"] == "response.content_part.done"
                                       and e.get("part", {}).get("type") == "output_text")
                    for text in ["".join(parts), *final_texts]:
                        _validate_wire_sizes(text, image.width, image.height)
                    result = asyncio.run(_parse_events(events, parser))
                    if result.outcome.status != "completed" or result.diagnostics:
                        raise ValueError("SAM returned incomplete or malformed segmentation output")
                    seen = set()
                    for record in result.records:
                        if record.kind == "text":
                            raise ValueError("SAM returned unexpected text instead of segmentation")
                        if record.kind != "mask":
                            continue
                        if record.object_id in seen:
                            raise ValueError("SAM returned a duplicate image object id")
                        seen.add(record.object_id)
                        if record.confidence is None:
                            raise ValueError("SAM omitted requested confidence")
                        bounds = record.bounds
                        coordinates = (bounds.left, bounds.top, bounds.right, bounds.bottom)
                        if any(not float(v).is_integer() for v in coordinates):
                            raise ValueError("SAM returned fractional mask bounds")
                        left, top, right, bottom = map(int, coordinates)
                        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                            raise ValueError("SAM mask lies outside the image")
                        raster = np.frombuffer(parser.decode_mask_to_raster(record.mask), dtype=np.uint8)
                        raster = raster.reshape(record.mask.height, record.mask.width)
                        if raster.shape != (bottom - top, right - left):
                            raster = np.asarray(Image.fromarray(raster).resize(
                                (right-left, bottom-top), Image.Resampling.NEAREST))
                        mask = np.zeros((image.height, image.width), dtype=bool)
                        mask[top:bottom, left:right] = raster != 0
                        detections.append(Detection(label, float(record.confidence), mask, record.object_id))
                except Sam3UnavailableError:
                    raise
                except httpx.TimeoutException:
                    raise Sam3UnavailableError("Meta SAM request timed out; retry the stage later") from None
                except httpx.HTTPError:
                    raise Sam3UnavailableError("Could not connect to Meta SAM") from None
                except (ValueError, TypeError, KeyError, parser.ResponsesStreamError):
                    raise Sam3UnavailableError("Meta SAM returned invalid segmentation data; no partial result was accepted") from None
        return detections
