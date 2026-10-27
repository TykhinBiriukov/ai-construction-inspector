import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from ai_inspector.api import create_app
from ai_inspector.config import Settings
from ai_inspector.session_folders import SupabaseSessionFolders
from ai_inspector.supabase_captures import SupabaseCaptureSourceStore


class FakeSupabase:
    def __init__(self):
        self.objects = {}
        self.grants = set()
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        path = unquote(request.url.path).removeprefix("/storage/v1/")
        for operation in ("object/upload/sign/", "object/sign/", "object/"):
            if path.startswith(operation):
                name = path.removeprefix(operation)
                break
        else:
            return httpx.Response(404)
        if request.method == "PUT":
            if name not in self.grants or request.headers.get("apikey") or request.headers.get("authorization"):
                return httpx.Response(403)
            if name in self.objects:
                return httpx.Response(409, json={"code": "Duplicate"})
            self.objects[name] = request.read()
            return httpx.Response(200, json={"Key": name})
        if request.headers.get("apikey") != "sb_secret_fake" or request.headers.get("authorization"):
            return httpx.Response(401)
        if request.method == "GET":
            if name not in self.objects:
                return httpx.Response(404, json={"code": "NoSuchKey"})
            return httpx.Response(200, content=self.objects[name])
        if operation == "object/upload/sign/":
            self.grants.add(name)
            return httpx.Response(200, json={"url": f"/object/upload/sign/{name}?token=upload-test-token"})
        if operation == "object/sign/":
            return httpx.Response(200, json={"signedURL": f"/object/sign/{name}?token=read-test-token"})
        if name in self.objects:
            return httpx.Response(409, json={"code": "Duplicate"})
        self.objects[name] = request.read()
        return httpx.Response(200, json={"Key": name})


class DeviceCaptureCaseTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".test-data"
        root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(Path(self.temporary.name), "operator-token", "device-token",
                                 supabase_url="https://test.supabase.co", supabase_storage_bucket="captures",
                                 supabase_secret_key="sb_secret_fake", geometry_base_url="https://geometry.example",
                                 inspector_public_base_url="https://inspector.example", geometry_api_token="g-out-token",
                                 geometry_service_token="g-in-token", geometry_accepts_rgb_only=True)
        self.fake = FakeSupabase()
        self.cloud = httpx.Client(transport=httpx.MockTransport(self.fake))
        self.addCleanup(self.cloud.close)
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(self.client.close)
        self.service = self.client.app.state.service
        self.service.session_folders = SupabaseSessionFolders(self.settings, self.cloud)
        self.service.capture._supabase_source_store = SupabaseCaptureSourceStore(self.settings, self.cloud)
        self.operator = {"Authorization": "Bearer operator-token"}
        self.device = {"Authorization": "Bearer device-token"}

    def quick(self):
        body = {"schema_version": "0.1", "request_id": str(uuid4()), "device_id": "iphone-1"}
        response = self.client.post("/v1/capture-sessions", json=body, headers=self.operator)
        self.assertEqual(response.status_code, 201, response.text)
        self.session = response.json()
        self.base = "/v1/sessions/" + self.session["session_id"]
        return body

    def start(self):
        response = self.client.post(self.base + "/capture-start", headers=self.device, json={
            "schema_version": "0.1", "operation_id": str(uuid4()),
            "device": {"name": "iPhone", "os_version": "test", "app_version": "test"},
        })
        self.assertEqual(response.status_code, 201, response.text)

    def fixture(self):
        self.observation_id, segment_id = str(uuid4()), str(uuid4())
        rgb = b"JPEG transport fixture"
        descriptor = lambda name, data: {"name": name, "byte_length": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        frame = {"schema_version": "0.2", "observation_id": self.observation_id, "frame_id": self.observation_id,
                 "session_id": self.session["session_id"], "segment_id": segment_id, "instruction_id": None,
                 "capture_origin": "device", "tracking_state": "normal", "rgb": descriptor("rgb.jpg", rgb),
                 "depth_source": "none", "depth": None, "depth_size": None, "depth_unit": None,
                 "depth_to_rgb_mapping": None, "confidence": None}
        files = {"rgb.jpg": rgb, "frame.json": json.dumps(frame).encode()}
        prefix = self.session["storage"]["key_prefix"]
        objects = []
        refs = []
        for name, content in files.items():
            meta = descriptor(name, content)
            objects.append(meta | {"observation_id": self.observation_id,
                                   "media_type": "image/jpeg" if name.endswith("jpg") else "application/json"})
            refs.append(meta | {"object_key": prefix + f"observations/{self.observation_id}/{name}"})
        completion_id = str(uuid4())
        manifest = {"schema_version": "0.1", "format": "capture_manifest_supabase_v1",
                    **{key: self.session[key] for key in ("session_id", "inspection_id", "project_id", "room_id", "model_version", "capture_origin", "input_mode")},
                    "capture_closed_at": "2026-10-27T14:00:00Z", "observation_count": 1,
                    "segments": [{"segment_id": segment_id, "created_at": "2026-10-27T13:00:00Z", "units": "m",
                                  "coordinate_convention": "arkit_world_camera_v1", "matrix_layout": "rows_column_vectors"}],
                    "observations": [{"observation_id": self.observation_id, "segment_id": segment_id, "files": refs}]}
        return files, objects, completion_id, json.dumps(manifest).encode()

    def upload(self, body, payloads):
        response = self.client.post(self.base + "/upload-plans", json=body, headers=self.device)
        self.assertEqual(response.status_code, 201, response.text)
        for item in response.json()["items"]:
            self.assertNotIn("sb_secret", item["upload_url"])
            result = self.cloud.put(item["upload_url"], content=payloads[item["name"]], headers=item["headers"])
            self.assertEqual(result.status_code, 200)
        return response.json()

    def capture(self):
        self.quick()
        self.start()
        files, objects, completion_id, manifest = self.fixture()
        self.upload({"schema_version": "0.1", "request_id": str(uuid4()), "kind": "observation_files", "objects": objects}, files)
        self.upload({"schema_version": "0.1", "request_id": str(uuid4()), "kind": "capture_manifest", "completion_id": completion_id,
                     "objects": [{"name": "capture-manifest.json", "observation_id": None, "media_type": "application/json",
                                  "byte_length": len(manifest), "sha256": hashlib.sha256(manifest).hexdigest()}]}, {"capture-manifest.json": manifest})
        body = {"schema_version": "0.1", "completion_id": completion_id, "capture_ref": {
            "format": "supabase_capture_v1", "storage_id": "capture-supabase", "manifest_key": self.session["storage"]["key_prefix"] + f"manifests/{completion_id}.json",
            "manifest_byte_length": len(manifest), "manifest_sha256": hashlib.sha256(manifest).hexdigest()}}
        response = self.client.post(self.base + "/complete", json=body, headers=self.device)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(self.service.capture.process_next())
        current = self.client.get(self.base, headers=self.operator).json()
        self.assertEqual(current["completion"]["status"], "accepted", current)
        return body

    def test_device_channel_receives_session_after_connection_without_creating_it(self):
        with self.client.websocket_connect("/v1/devices/iphone-1/events/ws", headers=self.device) as socket:
            self.assertEqual(socket.receive_json()["type"], "device.connected")
            self.assertEqual(socket.receive_json()["sessions"], [])
            self.assertTrue(self.client.get("/v1/devices/iphone-1/status", headers=self.operator).json()["online"])
            body = self.quick()
            packet = socket.receive_json()
            self.assertEqual(packet["type"], "events")
            self.assertEqual(packet["events"][0]["type"], "session.assigned")
            self.assertEqual(packet["events"][0]["session_id"], self.session["session_id"])
            self.assertEqual(self.client.post("/v1/capture-sessions", json=body, headers=self.operator).status_code, 200)
            socket.send_json({"type": "ping"})
            self.assertEqual(socket.receive_json()["type"], "pong")
        self.assertFalse(self.client.get("/v1/devices/iphone-1/status", headers=self.operator).json()["online"])

    def test_websocket_auth_first_message_and_http_replay(self):
        self.quick()
        with self.client.websocket_connect("/v1/devices/iphone-1/events/ws") as socket:
            socket.send_json({"type": "authenticate", "token": "device-token"})
            self.assertEqual(socket.receive_json()["type"], "device.connected")
            snapshot = socket.receive_json()
            self.assertEqual(snapshot["sessions"][0]["session_id"], self.session["session_id"])
            after = snapshot["resume_cursor"]
        self.start()
        page = self.client.get("/v1/devices/iphone-1/events", headers=self.device, params={"after": after}).json()
        self.assertEqual(page["events"][0]["session"]["capture_state"], "capturing")
        with self.client.websocket_connect("/v1/devices/iphone-1/events/ws?after=" + after, headers=self.device) as socket:
            self.assertEqual(socket.receive_json()["type"], "device.connected")
            self.assertEqual(socket.receive_json()["events"][0]["session"]["capture_state"], "capturing")

    def test_websocket_rejects_operator_other_device_and_query_credentials(self):
        for url, headers in (("/v1/devices/iphone-1/events/ws", self.operator),
                             ("/v1/devices/other/events/ws", self.device),
                             ("/v1/devices/iphone-1/events/ws?token=bad", self.device)):
            with self.client.websocket_connect(url, headers=headers) as socket:
                self.assertEqual(socket.receive_json()["type"], "stream.error")

    def test_supabase_snapshot_pins_bytes_before_generator_and_accepts_final_result(self):
        body = self.capture()
        current = self.client.get(self.base, headers=self.operator).json()
        job_id = current["geometry_job"]["job_id"]
        # Altering original uploaded bytes cannot change the source already accepted.
        raw_key = "captures/" + self.session["storage"]["key_prefix"] + f"observations/{self.observation_id}/rgb.jpg"
        self.fake.objects[raw_key] = b"later overwritten raw bytes"
        seen_command = []

        def generator(request):
            if request.method == "PUT":
                command = json.loads(request.content)
                seen_command.append(command)
                self.assertNotIn("manifest_version_id", command["source_ref"])
                self.assertEqual(command["source_ref"]["format"], "supabase_capture_v1")
                return httpx.Response(201, json={"job_id": job_id, "status": "queued"})
            command = seen_command[0]
            return httpx.Response(200, json={**command, "manifest_sha256": body["capture_ref"]["manifest_sha256"],
                "status": "ready", "revision": 2, "geometry_revision": 1, "analysis_origin": "mock",
                "evidence_observation_ids": [self.observation_id], "excluded_observations": [], "artifacts": [{
                    "artifact_id": "test-cloud", "kind": "point_cloud", "url": "https://geometry.example/test.ply",
                    "media_type": "application/octet-stream", "byte_length": 10, "sha256": "a" * 64, "is_synthetic": True}]})

        with httpx.Client(transport=httpx.MockTransport(generator)) as geometry:
            self.service.capture.process_next(geometry)
            self.service.capture.process_next(geometry)
        current = self.client.get(self.base, headers=self.operator).json()
        self.assertEqual(current["processing_state"], "completed", current)
        self.assertEqual(current["geometry_result"]["analysis_origin"], "mock")
        self.assertIsNone(current["result"])
        source_url = f"/internal/v1/sessions/{self.session['session_id']}/capture-source/files"
        access = self.client.get(source_url, headers={"Authorization": "Bearer g-in-token"}).json()
        rgb = next(item for item in access["items"] if item["name"] == "rgb.jpg")
        pinned_path = unquote(httpx.URL(rgb["download_url"]).path).removeprefix("/storage/v1/object/sign/")
        self.assertEqual(self.fake.objects[pinned_path], b"JPEG transport fixture")

    def test_upload_plan_replay_reports_existing_objects_and_conflicts(self):
        self.quick()
        self.start()
        files, objects, _, _ = self.fixture()
        body = {"schema_version": "0.1", "request_id": str(uuid4()), "kind": "observation_files", "objects": objects}
        self.upload(body, files)
        replay = self.client.post(self.base + "/upload-plans", headers=self.device, json=body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(all(item["status"] == "already_uploaded" for item in replay.json()["items"]))
        changed = body | {"objects": [objects[0] | {"sha256": "b" * 64}, objects[1]]}
        self.assertEqual(self.client.post(self.base + "/upload-plans", headers=self.device, json=changed).status_code, 409)


if __name__ == "__main__":
    unittest.main()
