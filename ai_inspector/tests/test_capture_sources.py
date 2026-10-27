import base64
import copy
import hashlib
import io
import json
import unittest
from types import SimpleNamespace
from uuid import uuid4

from ai_inspector.capture_sources import S3CaptureSourceStore
from ai_inspector.errors import APIError


class FakeS3Error(Exception):
    def __init__(self, code, status=400):
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.head_overrides = {}
        self.get_overrides = {}
        self.failures = {}
        self.calls = []
        self.bodies = []

    def _object(self, operation, kwargs):
        key = (kwargs["Key"], kwargs["VersionId"])
        self.calls.append((operation, dict(kwargs)))
        if (operation, key) in self.failures:
            raise self.failures[(operation, key)]
        if key not in self.objects:
            raise FakeS3Error("NoSuchVersion", 404)
        return key, self.objects[key]

    def head_object(self, **kwargs):
        key, data = self._object("head_object", kwargs)
        return {
            "ContentLength": len(data), "VersionId": kwargs["VersionId"],
            "ChecksumType": "FULL_OBJECT",
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
        } | self.head_overrides.get(key, {})

    def get_object(self, **kwargs):
        key, data = self._object("get_object", kwargs)
        overrides = self.get_overrides.get(key, {})
        body = io.BytesIO(overrides.get("body", data))
        self.bodies.append(body)
        return {
            "Body": body, "ContentLength": len(data), "VersionId": kwargs["VersionId"],
        } | {name: value for name, value in overrides.items() if name != "body"}

    def generate_presigned_url(self, method, **kwargs):
        self.calls.append(("presign", {"method": method, **kwargs}))
        params = kwargs["Params"]
        return f"https://s3.example/{params['Key']}?versionId={params['VersionId']}&signature=test-secret"


class CaptureSourceTests(unittest.TestCase):
    def setUp(self):
        self.build_fixture()

    def build_fixture(self):
        self.settings = SimpleNamespace(
            capture_bucket="private-capture-bucket", capture_region="eu-central-1", storage_id="capture-s3",
            max_manifest_bytes=8 * 1024 * 1024, max_capture_files=40000,
            max_capture_bytes=50 * 1024**3, source_url_lifetime_s=900,
        )
        self.s3 = FakeS3()
        self.store = S3CaptureSourceStore(self.settings, s3_client=self.s3)
        self.session_id, self.inspection_id = str(uuid4()), str(uuid4())
        self.observation_id, self.segment_id, self.completion_id = (str(uuid4()) for _ in range(3))
        self.prefix = f"projects/demo-building/sessions/{self.session_id}/"
        self.session = {
            "session_id": self.session_id, "inspection_id": self.inspection_id,
            "project_id": "demo-building", "room_id": "room-204", "model_version": "demo-model-v1",
            "capture_origin": "device", "input_mode": "live_capture",
            "storage": {"storage_id": "capture-s3", "key_prefix": self.prefix},
        }
        self.payloads = {"rgb.jpg": b"\xff\xd8RGB bytes\xff\xd9"}
        self.frame = {
            "schema_version": "0.2", "session_id": self.session_id,
            "observation_id": self.observation_id, "frame_id": self.observation_id,
            "segment_id": self.segment_id, "capture_origin": "device", "instruction_id": None,
            "tracking_state": "normal", "depth_source": "none", "depth": None,
            "depth_size": None, "depth_unit": None, "depth_to_rgb_mapping": None, "confidence": None,
            "rgb": self.frame_descriptor("rgb.jpg"),
            "timestamp_s": 1.5, "saved_at": "2026-10-27T12:01:00Z", "rgb_size": [1920, 1440],
            "K_rgb": [[1400, 0, 960], [0, 1400, 720], [0, 0, 1]],
            "T_world_camera": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        }
        self.manifest = {
            "schema_version": "0.1", "format": "capture_manifest_v1",
            **{name: self.session[name] for name in (
                "session_id", "inspection_id", "project_id", "room_id", "model_version", "capture_origin", "input_mode",
            )},
            "capture_closed_at": "2026-10-27T12:02:00Z", "observation_count": 1,
            "segments": [{
                "segment_id": self.segment_id, "created_at": "2026-10-27T12:00:00Z",
                "units": "m", "coordinate_convention": "arkit_world_camera_v1",
                "matrix_layout": "rows_column_vectors",
            }],
            "observations": [{"observation_id": self.observation_id, "segment_id": self.segment_id, "files": []}],
        }
        self.refresh_files()

    def frame_descriptor(self, name):
        data = self.payloads[name]
        return {"name": name, "byte_length": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def refresh_files(self):
        self.payloads["frame.json"] = json.dumps(self.frame).encode()
        entries = []
        for name, data in self.payloads.items():
            entry = {
                **self.frame_descriptor(name),
                "object_key": f"{self.prefix}observations/{self.observation_id}/{name}",
                "version_id": f"version-{name}",
            }
            self.s3.objects[(entry["object_key"], entry["version_id"])] = data
            entries.append(entry)
        self.manifest["observations"][0]["files"] = entries
        self.save_manifest()

    def save_manifest(self, data=None):
        data = json.dumps(self.manifest).encode() if data is None else data
        self.capture_ref = {
            "storage_id": "capture-s3", "manifest_key": f"{self.prefix}manifests/{self.completion_id}.json",
            "manifest_version_id": "manifest-version-1", "manifest_byte_length": len(data),
            "manifest_sha256": hashlib.sha256(data).hexdigest(),
        }
        self.s3.objects[(self.capture_ref["manifest_key"], "manifest-version-1")] = data

    def with_depth(self, *, confidence=True):
        self.payloads["depth.f32"] = b"\x00\x00\x80\x3f" * 4
        self.frame.update({
            "depth": self.frame_descriptor("depth.f32"), "depth_size": [2, 2], "depth_unit": "m",
            "depth_source": "sceneDepth", "depth_to_rgb_mapping": "Aligned native buffers",
        })
        if confidence:
            self.payloads["confidence.u8"] = b"\x02" * 4
            self.frame["confidence"] = self.frame_descriptor("confidence.u8")
        self.refresh_files()

    def file(self, name):
        return next(item for item in self.manifest["observations"][0]["files"] if item["name"] == name)

    def key(self, name):
        item = self.file(name)
        return item["object_key"], item["version_id"]

    def assert_api_error(self, callback, status, code):
        with self.assertRaises(APIError) as caught:
            callback()
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(caught.exception.body["code"], code)
        return caught.exception.body

    def validate(self):
        return self.store.validate(self.session, self.capture_ref)

    def test_rgb_only_returns_pinned_inventory_without_signed_urls(self):
        result = self.validate()
        self.assertEqual(result["observation_count"], 1)
        self.assertEqual(result["observations"][0]["observation_id"], self.observation_id)
        self.assertEqual({item["name"] for item in result["files"]}, {"frame.json", "rgb.jpg"})
        self.assertEqual(result["excluded_observations"], [])
        self.assertNotIn("signature=", repr(result))
        for operation, params in self.s3.calls:
            self.assertEqual(params["Bucket"], self.settings.capture_bucket)
            self.assertTrue(params["VersionId"])
        self.assertNotIn(("get_object", self.key("rgb.jpg")), [
            (method, (params["Key"], params["VersionId"])) for method, params in self.s3.calls
        ])
        self.assertTrue(all(body.closed for body in self.s3.bodies))

    def test_rgbd_and_legacy_frames_preserve_binary_file_refs(self):
        for version in ("0.1", "0.2"):
            with self.subTest(version=version):
                self.build_fixture()
                self.with_depth()
                self.frame["schema_version"] = version
                self.refresh_files()
                result = self.validate()
                self.assertEqual(len(result["files"]), 4)
                self.assertEqual(next(item for item in result["files"] if item["name"] == "depth.f32")["byte_length"], 16)

    def test_synthetic_depth_requires_explicit_synthetic_metadata(self):
        self.with_depth(confidence=False)
        for data in (self.session, self.manifest, self.frame):
            data["capture_origin"] = "synthetic"
        self.session["input_mode"] = self.manifest["input_mode"] = "synthetic_fixture"
        self.frame.update(tracking_state="synthetic", depth_source="synthetic_plane")
        self.refresh_files()
        self.assertEqual(len(self.validate()["files"]), 3)

    def test_manifest_reference_must_belong_to_configured_session(self):
        for change in (
            {"storage_id": "another-store"}, {"manifest_version_id": "null"},
            {"manifest_version_id": ""}, {"manifest_byte_length": 0},
            {"manifest_sha256": "A" * 64},
            {"manifest_key": f"projects/demo-building/sessions/{uuid4()}/manifests/{uuid4()}.json"},
            {"manifest_key": f"{self.prefix}manifests/../../outside.json"},
        ):
            with self.subTest(change=change):
                self.assert_api_error(
                    lambda: self.store.validate(self.session, self.capture_ref | change), 422, "capture_manifest_invalid",
                )

    def test_manifest_byte_length_and_hash_are_verified(self):
        for change, field in (({"manifest_sha256": "0" * 64}, "hash_mismatches"), ({"manifest_byte_length": 2}, "size_mismatches")):
            error = self.assert_api_error(
                lambda: self.store.validate(self.session, self.capture_ref | change), 422, "capture_source_incomplete",
            )
            self.assertIn(self.capture_ref["manifest_key"], error["details"][field])

    def test_rejects_non_json_duplicate_keys_and_nonfinite_json(self):
        for data in (b"not json", b"[]", b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'):
            with self.subTest(data=data):
                self.save_manifest(data)
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_manifest_schema_context_and_inventory_constraints(self):
        mutations = (
            lambda item: item.update(schema_version="0.2"),
            lambda item: item.update(format="unknown"),
            lambda item: item.update(session_id=str(uuid4())),
            lambda item: item.update(model_version="another-model"),
            lambda item: item.update(capture_closed_at="yesterday"),
            lambda item: item.update(observation_count=0),
            lambda item: item.update(observation_count=True),
            lambda item: item.update(observations=[]),
            lambda item: item.update(segments=[]),
            lambda item: item["observations"].append(copy.deepcopy(item["observations"][0])) or item.update(observation_count=2),
            lambda item: item["segments"].append(copy.deepcopy(item["segments"][0])),
            lambda item: item["observations"][0].update(segment_id=str(uuid4())),
            lambda item: item["segments"][0].update(units="mm"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.build_fixture()
                mutate(self.manifest)
                self.save_manifest()
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_file_inventory_rejects_unsafe_duplicate_and_unversioned_refs(self):
        mutations = (
            lambda entries: entries[0].update(object_key="outside/session/rgb.jpg"),
            lambda entries: entries[0].update(name="../rgb.jpg"),
            lambda entries: entries[0].update(name=[]),
            lambda entries: entries[0].update(version_id="null"),
            lambda entries: entries[0].update(byte_length=True),
            lambda entries: entries[0].update(sha256="invalid"),
            lambda entries: entries.append(copy.deepcopy(entries[0])),
            lambda entries: entries.pop(),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.build_fixture()
                mutate(self.manifest["observations"][0]["files"])
                self.save_manifest()
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_missing_exact_version_is_rejected_with_missing_files(self):
        key = self.key("rgb.jpg")
        self.s3.objects[(key[0], "a-newer-version")] = self.s3.objects.pop(key)
        error = self.assert_api_error(self.validate, 422, "capture_source_incomplete")
        self.assertEqual(error["details"]["missing_files"], [key[0]])

    def test_requested_delete_marker_is_missing_but_missing_bucket_is_retryable(self):
        key = self.key("rgb.jpg")
        self.s3.failures[("head_object", key)] = FakeS3Error("MethodNotAllowed", 405)
        error = self.assert_api_error(self.validate, 422, "capture_source_incomplete")
        self.assertEqual(error["details"]["missing_files"], [key[0]])
        self.s3.failures[("head_object", key)] = FakeS3Error("NoSuchBucket", 404)
        error = self.assert_api_error(self.validate, 503, "capture_storage_unavailable")
        self.assertTrue(error["retryable"])

    def test_full_object_checksum_mismatch_is_rejected(self):
        self.s3.head_overrides[self.key("rgb.jpg")] = {"ChecksumSHA256": base64.b64encode(b"\x00" * 32).decode()}
        error = self.assert_api_error(self.validate, 422, "capture_source_incomplete")
        self.assertEqual(error["details"]["hash_mismatches"], [self.key("rgb.jpg")[0]])

    def test_composite_absent_or_malformed_checksum_falls_back_to_stream_hash(self):
        for override in (
            {"ChecksumType": "COMPOSITE", "ChecksumSHA256": "not-a-full-object-checksum"},
            {"ChecksumType": None}, {"ChecksumSHA256": None},
            {"ChecksumSHA256": "not-base64"},
        ):
            with self.subTest(override=override):
                self.build_fixture()
                key = self.key("rgb.jpg")
                self.s3.head_overrides[key] = override
                self.validate()
                self.assertIn(("get_object", key), [
                    (method, (params["Key"], params["VersionId"])) for method, params in self.s3.calls
                ])
                self.assertTrue(all(body.closed for body in self.s3.bodies))

    def test_streamed_hash_mismatch_closes_body_and_does_not_trust_custom_metadata(self):
        key = self.key("rgb.jpg")
        self.s3.head_overrides[key] = {"ChecksumType": None, "Metadata": {"sha256": self.file("rgb.jpg")["sha256"]}}
        self.s3.get_overrides[key] = {"body": b"x" * len(self.payloads["rgb.jpg"])}
        self.assert_api_error(self.validate, 422, "capture_source_incomplete")
        self.assertTrue(all(body.closed for body in self.s3.bodies))

    def test_truncated_transfer_is_retryable_and_closes_body(self):
        key = self.key("rgb.jpg")
        self.s3.head_overrides[key] = {"ChecksumType": None}
        self.s3.get_overrides[key] = {"body": b"x"}
        error = self.assert_api_error(self.validate, 503, "capture_storage_unavailable")
        self.assertTrue(error["retryable"])
        self.assertTrue(all(body.closed for body in self.s3.bodies))

    def test_storage_auth_and_network_errors_are_retryable_without_sdk_details(self):
        for failure in (FakeS3Error("AccessDenied", 403), FakeS3Error("SlowDown", 503), TimeoutError("secret URL")):
            with self.subTest(failure=type(failure).__name__):
                self.build_fixture()
                self.s3.failures[("head_object", self.key("rgb.jpg"))] = failure
                error = self.assert_api_error(self.validate, 503, "capture_storage_unavailable")
                self.assertTrue(error["retryable"])
                self.assertNotIn("secret", repr(error))

    def test_provider_must_return_requested_exact_version(self):
        self.s3.head_overrides[self.key("rgb.jpg")] = {"VersionId": "new-current-version"}
        self.assert_api_error(self.validate, 503, "capture_storage_unavailable")

    def test_frame_identity_origin_instruction_and_schema_are_checked(self):
        for change in (
            {"schema_version": "0.3"}, {"schema_version": []}, {"frame_id": str(uuid4())},
            {"session_id": str(uuid4())}, {"segment_id": str(uuid4())},
            {"capture_origin": "synthetic"}, {"instruction_id": "unknown-instruction"},
            {"tracking_state": "limited"},
        ):
            with self.subTest(change=change):
                self.build_fixture()
                self.frame.update(change)
                self.refresh_files()
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_rgb_only_requires_explicit_null_depth_metadata(self):
        for field in ("depth", "depth_size", "depth_unit", "depth_to_rgb_mapping", "confidence"):
            with self.subTest(field=field):
                self.build_fixture()
                del self.frame[field]
                self.refresh_files()
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")
        self.build_fixture()
        self.frame["schema_version"] = "0.1"
        self.refresh_files()
        self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_frame_file_descriptors_and_depth_dimensions_match_inventory(self):
        for mutate in (
            lambda frame: frame["rgb"].update(sha256="0" * 64),
            lambda frame: frame["depth"].update(byte_length=1),
            lambda frame: frame.update(depth_size=[2, 3]),
            lambda frame: frame.update(confidence=None),
            lambda frame: frame.update(depth_unit="mm"),
        ):
            with self.subTest(mutate=mutate):
                self.build_fixture()
                self.with_depth()
                mutate(self.frame)
                self.refresh_files()
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_excluded_observations_require_distinct_ids_and_reasons(self):
        excluded_id = str(uuid4())
        self.manifest["excluded_observations"] = [{"observation_id": excluded_id, "reason": "Motion blur"}]
        self.save_manifest()
        self.assertEqual(self.validate()["excluded_observations"], self.manifest["excluded_observations"])
        for entries in (
            [{"observation_id": self.observation_id, "reason": "Already included"}],
            [{"observation_id": excluded_id, "reason": " "}],
            [{"observation_id": excluded_id, "reason": "Excluded"}] * 2,
        ):
            self.manifest["excluded_observations"] = entries
            self.save_manifest()
            self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_configured_manifest_file_and_total_byte_limits(self):
        for setting, value in (("max_manifest_bytes", 10), ("max_capture_files", 1), ("max_capture_bytes", 1)):
            with self.subTest(setting=setting):
                self.build_fixture()
                setattr(self.settings, setting, value)
                self.assert_api_error(self.validate, 422, "capture_manifest_invalid")

    def test_download_access_signs_only_exact_versions_and_preserves_metadata(self):
        inventory = self.validate()
        original_ref, original_files = copy.deepcopy(self.capture_ref), copy.deepcopy(inventory["files"])
        self.s3.calls.clear()
        access = self.store.download_access(self.capture_ref, inventory["files"])
        self.assertEqual(self.capture_ref, original_ref)
        self.assertEqual(inventory["files"], original_files)
        self.assertEqual(len(access["items"]), 2)
        self.assertTrue(access["expires_at"].endswith("Z"))
        expected = [(self.capture_ref["manifest_key"], self.capture_ref["manifest_version_id"])] + [
            (item["object_key"], item["version_id"]) for item in inventory["files"]
        ]
        for (operation, options), (key, version) in zip(self.s3.calls, expected, strict=True):
            self.assertEqual(operation, "presign")
            self.assertEqual(options["method"], "get_object")
            self.assertEqual(options["Params"], {"Bucket": self.settings.capture_bucket, "Key": key, "VersionId": version})
            self.assertEqual(options["ExpiresIn"], 900)
            self.assertEqual(options["HttpMethod"], "GET")

    def test_download_access_rejects_cross_session_file_refs(self):
        inventory = self.validate()
        inventory["files"][0]["object_key"] = "other-project/private-file"
        self.s3.calls.clear()
        self.assert_api_error(
            lambda: self.store.download_access(self.capture_ref, inventory["files"]), 422, "capture_manifest_invalid",
        )
        self.assertEqual(self.s3.calls, [])

    def test_empty_bucket_defers_validation_without_constructing_sdk(self):
        self.settings.capture_bucket = ""
        self.store = S3CaptureSourceStore(self.settings)
        for callback in (self.validate, lambda: self.store.download_access(self.capture_ref, [])):
            error = self.assert_api_error(callback, 503, "capture_storage_not_configured")
            self.assertTrue(error["retryable"])


if __name__ == "__main__":
    unittest.main()
