import asyncio
import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from starlette.formparsers import SpooledTemporaryFile

from ai_inspector.api import create_app
from ai_inspector.config import Settings


class StreamedUploadLimitTests(unittest.TestCase):
    def test_overflow_closes_partially_parsed_multipart_file(self):
        test_root = Path(__file__).resolve().parents[1] / ".test-data"
        test_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as temporary:
            data_dir = Path(temporary).resolve()
            self.assertTrue(data_dir.is_relative_to(test_root.resolve()))
            app = create_app(Settings(
                data_dir, "test-operator-token", "test-device-token", max_bim_bytes=1024,
            ))
            boundary = "streamed-upload-limit"
            fields = {
                "schema_version": "0.1", "request_id": str(uuid4()),
                "project_id": "demo-building", "room_id": "room-204",
                "prompt": "Inspect the doorway.",
            }
            prefix = "".join(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                for name, value in fields.items()
            )
            prefix += (
                f'--{boundary}\r\nContent-Disposition: form-data; name="bim_model"; filename="room.ifc"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            )
            # Separate ASGI messages let the parser open and write its file before
            # the second data chunk exceeds the total body limit. TestClient can
            # coalesce a content iterator and miss this cleanup path entirely.
            chunks = [
                prefix.encode(), b"x" * (40 * 1024), b"x" * (40 * 1024),
                f"\r\n--{boundary}--\r\n".encode(),
            ]
            opened_files = []

            def track_file(*args, **kwargs):
                kwargs["dir"] = data_dir
                result = SpooledTemporaryFile(*args, **kwargs)
                opened_files.append(result)
                return result

            try:
                with patch("starlette.formparsers.SpooledTemporaryFile", track_file):
                    messages = asyncio.run(self._request(app, boundary, chunks))
                start = next(message for message in messages if message["type"] == "http.response.start")
                body = b"".join(
                    message.get("body", b"")
                    for message in messages if message["type"] == "http.response.body"
                )
                self.assertEqual(start["status"], 413, body.decode())
                error = json.loads(body)
                self.assertEqual(set(error), {"code", "message", "retryable", "details"})
                self.assertEqual(error["code"], "request_too_large")
                self.assertFalse(error["retryable"])
                self.assertTrue(opened_files, "Multipart parsing must have opened a file before overflow")
                self.assertTrue(all(item.closed for item in opened_files))
            finally:
                # Close files even when the assertion fails against a regression.
                for item in opened_files:
                    item.close()

    @staticmethod
    async def _request(app, boundary, chunks):
        pending = deque(chunks)
        response_finished = asyncio.Event()
        messages = []

        async def receive():
            if pending:
                return {"type": "http.request", "body": pending.popleft(), "more_body": bool(pending)}
            await response_finished.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_finished.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": "/v1/inspections", "raw_path": b"/v1/inspections",
            "query_string": b"", "root_path": "",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                (b"authorization", b"Bearer test-operator-token"),
            ],
            "client": ("test", 123), "server": ("test", 80),
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        return messages


if __name__ == "__main__":
    unittest.main()
