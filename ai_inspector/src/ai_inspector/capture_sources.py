"""Verify and expose the immutable S3 source selected by capture completion.

This adapter verifies transport integrity and capture metadata. Geometry quality,
camera calibration, and reconstruction remain the Geometry Service's concern.
"""

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from .errors import APIError


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FILES = frozenset({"frame.json", "rgb.jpg", "depth.f32", "confidence.u8"})
_UTC_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)\Z")
_MANIFEST_KEY = re.compile(
    r"(?P<prefix>projects/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/sessions/"
    r"(?P<session>[^/]+)/)manifests/(?P<completion>[^/]+)\.json\Z"
)


def _invalid(message: str, **details) -> APIError:
    return APIError(422, "capture_manifest_invalid", message, details=details)


def _incomplete(message: str, key: str, kind: str) -> APIError:
    details = {"missing_files": [], "hash_mismatches": []}
    details.setdefault(kind, []).append(key)
    return APIError(422, "capture_source_incomplete", message, details=details)


def _unavailable() -> APIError:
    return APIError(
        503, "capture_storage_unavailable", "Capture storage is temporarily unavailable", retryable=True,
    )


def _uuid(value, label: str) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{label} must be a UUID")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise _invalid(f"{label} must be a UUID") from None


def _version(value) -> bool:
    return (
        isinstance(value, str) and bool(value) and value != "null"
        and value == value.strip() and len(value) <= 1024
        and all(ord(character) >= 32 for character in value)
    )


def _positive_integer(value) -> bool:
    return type(value) is int and value > 0


def _date(value, label: str) -> None:
    if not isinstance(value, str) or not _UTC_DATE.fullmatch(value):
        raise _invalid(f"{label} must be a UTC RFC 3339 timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid(f"{label} must be a UTC RFC 3339 timestamp") from None


def _json_document(data: bytes, label: str) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate object key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("Nonfinite JSON number")

    try:
        result = json.loads(
            data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant,
        )
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _invalid(f"{label} must be a valid UTF-8 JSON object") from None
    if not isinstance(result, dict):
        raise _invalid(f"{label} must be a JSON object")
    return result


class S3CaptureSourceStore:
    def __init__(self, settings, s3_client=None) -> None:
        self.settings = settings
        self._s3_client = s3_client

    def _client(self):
        if not self.settings.capture_bucket:
            raise APIError(
                503, "capture_storage_not_configured", "Capture storage is not configured", retryable=True,
            )
        if self._s3_client is None:
            try:
                import boto3
                from botocore.config import Config

                self._s3_client = boto3.client(
                    "s3", region_name=self.settings.capture_region,
                    config=Config(
                        signature_version="s3v4", connect_timeout=5, read_timeout=30,
                        retries={"max_attempts": 3, "mode": "standard"},
                    ),
                )
            except Exception:
                raise _unavailable() from None
        return self._s3_client

    def _call(self, operation: str, key: str, version_id: str, **kwargs) -> dict:
        try:
            return getattr(self._client(), operation)(
                Bucket=self.settings.capture_bucket, Key=key, VersionId=version_id, **kwargs,
            )
        except APIError:
            raise
        except Exception as exception:
            response = getattr(exception, "response", {})
            error = response.get("Error") if isinstance(response, dict) else None
            metadata = response.get("ResponseMetadata") if isinstance(response, dict) else None
            code = str(error.get("Code", "")) if isinstance(error, dict) else ""
            status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
            if code == "NoSuchBucket":
                raise _unavailable() from None
            # S3 returns 405 when a specifically requested version is a delete marker.
            if code in {"NoSuchKey", "NoSuchVersion", "NotFound", "404", "MethodNotAllowed", "405"} or status in (404, 405):
                raise _incomplete("A referenced capture object version is missing", key, "missing_files") from None
            # AccessDenied can hide missing objects; it is not proof of absence.
            # Never include exception text: SDK errors may contain signed URLs.
            raise _unavailable() from None

    def _reference(self, capture_ref: dict, session: dict | None = None) -> tuple[str, dict]:
        if not isinstance(capture_ref, dict):
            raise _invalid("capture_ref must be an object")
        if capture_ref.get("storage_id") != self.settings.storage_id:
            raise _invalid("Capture storage does not match the configured store")
        key = capture_ref.get("manifest_key")
        match = _MANIFEST_KEY.fullmatch(key) if isinstance(key, str) else None
        if match is None:
            raise _invalid("Manifest key must identify a session capture manifest")
        _uuid(match["session"], "Manifest session ID")
        _uuid(match["completion"], "Manifest completion ID")
        prefix = match["prefix"]
        if session is not None:
            expected_prefix = f"projects/{session['project_id']}/sessions/{session['session_id']}/"
            if prefix != expected_prefix or session["storage"]["key_prefix"] != expected_prefix:
                raise _invalid("Manifest key belongs to another capture session")
            if capture_ref["storage_id"] != session["storage"]["storage_id"]:
                raise _invalid("Manifest storage does not match the capture session")
        descriptor = {
            "object_key": key, "version_id": capture_ref.get("manifest_version_id"),
            "byte_length": capture_ref.get("manifest_byte_length"),
            "sha256": capture_ref.get("manifest_sha256"),
        }
        self._descriptor(descriptor)
        if descriptor["byte_length"] > self.settings.max_manifest_bytes:
            raise _invalid("Manifest exceeds the configured byte limit")
        return prefix, descriptor

    @staticmethod
    def _descriptor(descriptor: dict) -> None:
        if not _version(descriptor.get("version_id")):
            raise _invalid("Every capture object requires a non-null S3 version ID")
        if not _positive_integer(descriptor.get("byte_length")):
            raise _invalid("Every capture object requires a positive byte length")
        digest = descriptor.get("sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise _invalid("Every capture object requires a lowercase SHA-256")

    def _verify_object(self, descriptor: dict, *, read: bool = False) -> bytes | None:
        key, version_id = descriptor["object_key"], descriptor["version_id"]
        expected_length, expected_hash = descriptor["byte_length"], descriptor["sha256"]
        head = self._call("head_object", key, version_id, ChecksumMode="ENABLED")
        if head.get("DeleteMarker"):
            raise _incomplete("A referenced capture object version is deleted", key, "missing_files")
        if head.get("VersionId") != version_id:
            raise _unavailable()
        if head.get("ContentLength") != expected_length:
            raise _incomplete("Capture object size does not match its manifest", key, "size_mismatches")
        checksum = None
        if head.get("ChecksumType") == "FULL_OBJECT" and isinstance(head.get("ChecksumSHA256"), str):
            try:
                raw = base64.b64decode(head["ChecksumSHA256"], validate=True)
                if len(raw) == 32:
                    checksum = raw.hex()
            except (ValueError, binascii.Error):
                pass
        if checksum is not None and checksum != expected_hash:
            raise _incomplete("Capture object hash does not match its manifest", key, "hash_mismatches")
        if checksum is not None and not read:
            return None
        response = self._call("get_object", key, version_id)
        body = response.get("Body")
        try:
            if response.get("VersionId") != version_id:
                raise _unavailable()
            if response.get("ContentLength") != expected_length:
                raise _incomplete("Capture object size does not match its manifest", key, "size_mismatches")
            if body is None:
                raise _unavailable()
            digest = hashlib.sha256()
            length = 0
            chunks = [] if read else None
            while True:
                chunk = body.read(64 * 1024)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise _unavailable()
                length += len(chunk)
                if length > expected_length:
                    raise _incomplete("Capture object size does not match its manifest", key, "size_mismatches")
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
            if length < expected_length:
                # A truncated transfer is retryable; HEAD already verified size.
                raise _unavailable()
            if digest.hexdigest() != expected_hash:
                raise _incomplete("Capture object hash does not match its manifest", key, "hash_mismatches")
            return b"".join(chunks) if chunks is not None else None
        except APIError:
            raise
        except Exception:
            raise _unavailable() from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    # Preserve the integrity/transfer result when closing a broken connection.
                    pass

    def validate(self, session: dict, capture_ref: dict) -> dict:
        self._client()
        prefix, manifest_descriptor = self._reference(capture_ref, session)
        manifest = _json_document(self._verify_object(manifest_descriptor, read=True), "Capture manifest")
        self._validate_manifest_format(manifest)
        for field in (
            "session_id", "inspection_id", "project_id", "room_id", "model_version", "capture_origin", "input_mode",
        ):
            if manifest.get(field) != session[field]:
                raise _invalid("Manifest context does not match its capture session", field=field)
        _date(manifest.get("capture_closed_at"), "capture_closed_at")
        segments = manifest.get("segments")
        observations = manifest.get("observations")
        if not isinstance(segments, list) or not segments:
            raise _invalid("Manifest must declare a nonempty segment inventory")
        if not isinstance(observations, list) or not observations:
            raise _invalid("Manifest must declare a nonempty observation inventory")
        if len(segments) > self.settings.max_capture_files or len(observations) > self.settings.max_capture_files:
            raise _invalid("Capture inventory exceeds the configured item limit")
        if type(manifest.get("observation_count")) is not int or manifest["observation_count"] != len(observations):
            raise _invalid("Manifest observation_count must match its observation inventory")
        segment_ids = set()
        for segment in segments:
            if not isinstance(segment, dict):
                raise _invalid("Segment entries must be objects")
            segment_id = _uuid(segment.get("segment_id"), "segment_id")
            if segment_id in segment_ids:
                raise _invalid("Duplicate segment ID")
            segment_ids.add(segment_id)
            _date(segment.get("created_at"), "Segment created_at")
            if (
                segment.get("units") != "m"
                or segment.get("coordinate_convention") != "arkit_world_camera_v1"
                or segment.get("matrix_layout") != "rows_column_vectors"
            ):
                raise _invalid("Unsupported segment coordinate metadata")

        observation_ids = set()
        files = []
        normalized_observations = []
        total_bytes = manifest_descriptor["byte_length"]
        for observation in observations:
            if not isinstance(observation, dict):
                raise _invalid("Observation entries must be objects")
            observation_id = observation.get("observation_id")
            canonical_id = _uuid(observation_id, "observation_id")
            if canonical_id in observation_ids:
                raise _invalid("Duplicate observation ID")
            observation_ids.add(canonical_id)
            if _uuid(observation.get("segment_id"), "segment_id") not in segment_ids:
                raise _invalid("Observation refers to an undeclared segment")
            entries = observation.get("files")
            if not isinstance(entries, list) or not 2 <= len(entries) <= 4:
                raise _invalid("Observation must declare two to four capture files")
            by_name = {}
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or entry["name"] not in _FILES:
                    raise _invalid("Unsupported capture filename")
                name = entry["name"]
                if name in by_name:
                    raise _invalid("Duplicate capture filename")
                if entry.get("object_key") != f"{prefix}observations/{observation_id}/{name}":
                    raise _invalid("Capture object key does not belong to its observation")
                self._descriptor(entry)
                item = {field: entry[field] for field in ("name", "object_key", "byte_length", "sha256")}
                if "version_id" in entry:
                    item["version_id"] = entry["version_id"]
                by_name[name] = item
                files.append({"observation_id": observation_id, **item})
                total_bytes += item["byte_length"]
                if len(files) > self.settings.max_capture_files or total_bytes > self.settings.max_capture_bytes:
                    raise _invalid("Capture inventory exceeds the configured file or byte limit")
            if not {"frame.json", "rgb.jpg"}.issubset(by_name):
                raise _invalid("Every observation requires frame.json and rgb.jpg")
            if by_name["frame.json"]["byte_length"] > self.settings.max_manifest_bytes:
                raise _invalid("Frame metadata exceeds the configured byte limit")
            normalized_observations.append({
                "observation_id": observation_id, "segment_id": observation["segment_id"],
                "files": list(by_name.values()),
            })

        excluded = manifest.get("excluded_observations", [])
        if not isinstance(excluded, list) or len(excluded) > self.settings.max_capture_files:
            raise _invalid("Invalid excluded observation inventory")
        excluded_ids = set()
        normalized_excluded = []
        for item in excluded:
            if not isinstance(item, dict):
                raise _invalid("Excluded observation entries must be objects")
            observation_id = _uuid(item.get("observation_id"), "Excluded observation ID")
            reason = item.get("reason")
            if observation_id in observation_ids or observation_id in excluded_ids:
                raise _invalid("Excluded observation IDs must be unique and absent from the included inventory")
            if not isinstance(reason, str) or not reason.strip():
                raise _invalid("Excluded observations require a nonempty reason")
            excluded_ids.add(observation_id)
            normalized_excluded.append({"observation_id": item["observation_id"], "reason": reason})

        for observation in normalized_observations:
            by_name = {item["name"]: item for item in observation["files"]}
            frame = _json_document(self._verify_object(by_name["frame.json"], read=True), "frame.json")
            self._validate_frame(frame, session, observation, by_name)
            for name, item in by_name.items():
                if name != "frame.json":
                    self._verify_object(item)
        return {
            "observations": normalized_observations, "files": files,
            "observation_count": len(normalized_observations), "excluded_observations": normalized_excluded,
        }

    @staticmethod
    def _validate_manifest_format(manifest: dict) -> None:
        if manifest.get("schema_version") != "0.1" or manifest.get("format") != "capture_manifest_v1":
            raise _invalid("Unsupported capture manifest schema or format")

    @staticmethod
    def _validate_frame(frame: dict, session: dict, observation: dict, files: dict) -> None:
        if frame.get("schema_version") not in ("0.1", "0.2"):
            raise _invalid("Unsupported frame.json schema version")
        for field, expected in (
            ("session_id", session["session_id"]), ("observation_id", observation["observation_id"]),
            ("frame_id", observation["observation_id"]), ("segment_id", observation["segment_id"]),
        ):
            if _uuid(frame.get(field), f"Frame {field}") != _uuid(expected, field):
                raise _invalid("Frame identity does not match its capture inventory", field=field)
        if frame.get("capture_origin") != session["capture_origin"]:
            raise _invalid("Frame capture_origin does not match its session")
        if "instruction_id" not in frame or frame["instruction_id"] is not None:
            raise _invalid("Instruction references are unsupported without instruction history")

        def descriptor(role: str, filename: str) -> None:
            value = frame.get(role)
            if not isinstance(value, dict) or filename not in files:
                raise _invalid(f"Frame {role} requires its declared file")
            expected = files[filename]
            if not _positive_integer(value.get("byte_length")) or any(
                value.get(field) != expected[field] for field in ("name", "byte_length", "sha256")
            ):
                raise _invalid(f"Frame {role} metadata does not match the manifest")

        descriptor("rgb", "rgb.jpg")
        if "depth" not in frame or "confidence" not in frame:
            raise _invalid("Frame depth and confidence must be explicit values or null")
        has_depth = frame["depth"] is not None
        if not has_depth:
            if frame["schema_version"] != "0.2" or session["capture_origin"] != "device":
                raise _invalid("RGB-only frames require device-origin frame.json 0.2")
            if frame.get("depth_source") != "none":
                raise _invalid("RGB-only frame depth_source must be none")
            for field in ("depth", "depth_size", "depth_unit", "depth_to_rgb_mapping", "confidence"):
                if field not in frame or frame[field] is not None:
                    raise _invalid("RGB-only depth fields must be explicit null", field=field)
            if set(files) != {"frame.json", "rgb.jpg"}:
                raise _invalid("RGB-only observations cannot include depth or confidence files")
        else:
            descriptor("depth", "depth.f32")
            expected_source = "synthetic_plane" if session["capture_origin"] == "synthetic" else "sceneDepth"
            if frame.get("depth_source") != expected_source or frame.get("depth_unit") != "m":
                raise _invalid("Unsupported frame depth source or unit")
            size = frame.get("depth_size")
            if not isinstance(size, list) or len(size) != 2 or not all(_positive_integer(value) for value in size):
                raise _invalid("Depth dimensions must contain two positive integers")
            pixel_count = size[0] * size[1]
            if files["depth.f32"]["byte_length"] != pixel_count * 4:
                raise _invalid("Depth byte length does not match float32 dimensions")
            if frame["confidence"] is not None:
                descriptor("confidence", "confidence.u8")
                if files["confidence.u8"]["byte_length"] != pixel_count:
                    raise _invalid("Confidence byte length does not match depth dimensions")
            elif "confidence.u8" in files:
                raise _invalid("Null frame confidence cannot include a confidence file")
        expected_tracking = "synthetic" if session["capture_origin"] == "synthetic" else "normal"
        if frame.get("tracking_state") != expected_tracking:
            raise _invalid("Frame tracking state does not match capture origin")

    def download_access(self, capture_ref: dict, files: list[dict]) -> dict:
        client = self._client()
        prefix, manifest = self._reference(capture_ref)
        if not isinstance(files, list) or len(files) > self.settings.max_capture_files:
            raise _invalid("Invalid capture file access inventory")
        normalized_files = []
        seen = set()
        for item in files:
            if not isinstance(item, dict):
                raise _invalid("Capture file access entries must be objects")
            _uuid(item.get("observation_id"), "observation_id")
            if not isinstance(item.get("name"), str) or item["name"] not in _FILES:
                raise _invalid("Unsupported capture filename")
            key = f"{prefix}observations/{item['observation_id']}/{item['name']}"
            if item.get("object_key") != key or key in seen:
                raise _invalid("Invalid or duplicate capture file access key")
            self._descriptor(item)
            seen.add(key)
            normalized_files.append({field: item[field] for field in (
                "observation_id", "name", "object_key", "version_id", "byte_length", "sha256",
            )})
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.settings.source_url_lifetime_s)).isoformat().replace("+00:00", "Z")

        def signed_url(item: dict) -> str:
            try:
                return client.generate_presigned_url(
                    "get_object", Params={
                        "Bucket": self.settings.capture_bucket, "Key": item["object_key"], "VersionId": item["version_id"],
                    }, ExpiresIn=self.settings.source_url_lifetime_s, HttpMethod="GET",
                )
            except Exception:
                raise _unavailable() from None

        return {
            "manifest_url": signed_url(manifest), "expires_at": expires_at,
            "items": [{**item, "download_url": signed_url(item), "expires_at": expires_at} for item in normalized_files],
        }
