"""Signed upload grants and server-owned immutable snapshots on Supabase."""

import hashlib
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .capture_sources import S3CaptureSourceStore, _HASH, _MANIFEST_KEY, _invalid, _incomplete, _positive_integer
from .errors import APIError


class SupabaseCaptureSourceStore(S3CaptureSourceStore):
    def __init__(self, settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = client
        self._pins = {}
        self._pin_prefix = None
        self._validation_lock = threading.Lock()

    def _client(self):
        if not self.settings.supabase_enabled:
            raise APIError(503, "capture_storage_not_configured", "Supabase capture storage is not configured", retryable=True)
        return self.http

    @contextmanager
    def connection(self):
        self._client()
        client = self.http or httpx.Client(timeout=30, follow_redirects=False)
        try:
            yield client
        except httpx.TransportError:
            raise APIError(503, "capture_storage_unavailable", "Supabase storage request will be retried", retryable=True) from None
        finally:
            if self.http is None:
                client.close()

    def _url(self, operation: str, key: str) -> str:
        return (self.settings.supabase_url.rstrip("/") + "/storage/v1/" + operation + "/"
                + quote(self.settings.supabase_storage_bucket, safe="") + "/" + quote(key, safe="/"))

    def _headers(self) -> dict:
        return {"apikey": self.settings.supabase_secret_key, "Accept-Encoding": "identity"}

    @staticmethod
    def _status(response, key: str) -> None:
        if response.status_code in (400, 404) and response.status_code != 200:
            # A GET 400 is used by Storage for some object-not-found responses.
            try:
                response.read()
                error = response.json()
                missing = error.get("code") in ("NoSuchKey", "NoSuchObject", "not_found") or str(error.get("statusCode")) == "404"
            except (ValueError, AttributeError):
                missing = response.status_code == 404
            if missing or response.status_code == 404:
                raise _incomplete("A capture object is missing", key, "missing_files")
        if response.status_code != 200:
            raise APIError(503, "capture_storage_unavailable", "Supabase storage request will be retried", retryable=True)

    @staticmethod
    def _descriptor(descriptor: dict) -> None:
        if descriptor.get("version_id") is not None:
            raise _invalid("Supabase capture objects do not use S3 version IDs")
        if not _positive_integer(descriptor.get("byte_length")):
            raise _invalid("Capture object requires a positive byte length")
        digest = descriptor.get("sha256")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise _invalid("Capture object requires a lowercase SHA-256")

    def _reference(self, capture_ref: dict, session: dict | None = None):
        if not isinstance(capture_ref, dict) or capture_ref.get("format") not in ("supabase_capture_v1", "supabase_geometry_parts_v1") or capture_ref.get("storage_id") != "capture-supabase":
            raise _invalid("Unsupported Supabase capture reference")
        key = capture_ref.get("manifest_key")
        match = _MANIFEST_KEY.fullmatch(key) if isinstance(key, str) else None
        if match is None:
            raise _invalid("Invalid session manifest key")
        prefix = match["prefix"]
        if session and (prefix != session["storage"]["key_prefix"]
                        or session["storage"].get("bucket") != self.settings.supabase_storage_bucket
                        or session["storage"].get("project_url") != self.settings.supabase_url.rstrip("/")):
            raise _invalid("Capture reference belongs to another session or store")
        descriptor = {"object_key": key, "byte_length": capture_ref.get("manifest_byte_length"), "sha256": capture_ref.get("manifest_sha256")}
        self._descriptor(descriptor)
        if descriptor["byte_length"] > self.settings.max_manifest_bytes:
            raise _invalid("Manifest exceeds the configured limit")
        return prefix, descriptor

    @staticmethod
    def _validate_manifest_format(manifest: dict) -> None:
        if manifest.get("schema_version") != "0.1" or manifest.get("format") != "capture_manifest_supabase_v1":
            raise _invalid("Supabase requires format=capture_manifest_supabase_v1")

    def _stream_to(self, client, key: str, target, expected_length: int, expected_hash: str) -> None:
        digest, size = hashlib.sha256(), 0
        with client.stream("GET", self._url("object", key), headers=self._headers(), follow_redirects=False) as response:
            self._status(response, key)
            for chunk in response.iter_bytes(64 * 1024):
                size += len(chunk)
                if size > expected_length:
                    raise _incomplete("Capture object size differs from manifest", key, "size_mismatches")
                digest.update(chunk)
                if target is not None:
                    target.write(chunk)
        if size != expected_length:
            raise _incomplete("Capture object size differs from manifest", key, "size_mismatches")
        if digest.hexdigest() != expected_hash:
            raise _incomplete("Capture object hash differs from manifest", key, "hash_mismatches")

    def _publish_pin(self, client, key: str, source, size: int, digest: str) -> None:
        source.seek(0)
        media_type = ("application/zip" if key.endswith(".zip") else "application/json" if key.endswith(".json")
                      else "image/jpeg" if key.endswith(".jpg") else "application/octet-stream")
        headers = self._headers() | {"Content-Type": media_type, "Content-Length": str(size), "x-upsert": "false"}
        response = client.post(self._url("object", key), headers=headers, content=iter(lambda: source.read(64 * 1024), b""), follow_redirects=False)
        if response.status_code in (200, 201):
            return
        if response.status_code in (400, 409):
            # A prior interrupted validation may already have pinned these bytes.
            self._stream_to(client, key, None, size, digest)
            return
        raise APIError(503, "capture_storage_unavailable", "Accepted source copy will be retried", retryable=True)

    def _verify_object(self, descriptor: dict, *, read: bool = False):
        key, size, digest = descriptor["object_key"], descriptor["byte_length"], descriptor["sha256"]
        if self._pin_prefix is None:
            raise RuntimeError("No snapshot context")
        staging = Path(self.settings.data_dir) / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=staging) as temporary, self.connection() as client:
            self._stream_to(client, key, temporary, size, digest)
            pinned = self._pin_prefix + digest + "/" + key.rsplit("/", 1)[-1]
            self._publish_pin(client, pinned, temporary, size, digest)
            self._pins[key] = pinned
            if read:
                temporary.seek(0)
                return temporary.read()
        return None

    def validate(self, session: dict, capture_ref: dict) -> dict:
        with self._validation_lock:
            if capture_ref.get("format") == "supabase_geometry_parts_v1":
                from .geometry_parts import validate_parts
                return validate_parts(self, session, capture_ref)
            if capture_ref.get("format") == "supabase_capture_zip_v1":
                from .capture_archives import validate_archive
                return validate_archive(self, session, capture_ref)
            return self._validate_snapshot(session, capture_ref)

    def _validate_snapshot(self, session: dict, capture_ref: dict) -> dict:
        prefix, descriptor = self._reference(capture_ref, session)
        completion_id = descriptor["object_key"].rsplit("/", 1)[-1].removesuffix(".json")
        self._pins = {}
        self._pin_prefix = prefix + "accepted/" + completion_id + "/"
        try:
            inventory = super().validate(session, capture_ref)
            for item in inventory["files"]:
                item["pinned_object_key"] = self._pins[item["object_key"]]
            inventory["pinned_capture_ref"] = capture_ref | {
                "snapshot_id": completion_id, "pinned_manifest_key": self._pins[capture_ref["manifest_key"]],
            }
            return inventory
        finally:
            self._pin_prefix = None
            self._pins = {}

    def object_matches(self, key: str, size: int, digest: str) -> bool:
        with self.connection() as client:
            try:
                self._stream_to(client, key, None, size, digest)
                return True
            except APIError as error:
                if error.body["details"].get("missing_files"):
                    return False
                raise

    def _signed_url(self, value: str, expected_operation: str, key: str) -> str:
        if not isinstance(value, str):
            raise APIError(503, "invalid_storage_receipt", "Supabase signed URL could not be verified", retryable=True)
        base = self.settings.supabase_url.rstrip("/")
        url = value if value.startswith("https://") else base + "/storage/v1" + value
        parsed = urlsplit(url)
        expected = urlsplit(self._url(expected_operation, key))
        if parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or parsed.path != expected.path or not parsed.query:
            raise APIError(503, "invalid_storage_receipt", "Supabase signed URL could not be verified", retryable=True)
        return url

    def signed_upload(self, key: str, media_type: str) -> dict:
        with self.connection() as client:
            response = client.post(self._url("object/upload/sign", key), headers=self._headers() | {"x-upsert": "false"}, json={}, follow_redirects=False)
            if response.status_code not in (200, 201):
                raise APIError(503, "capture_storage_unavailable", "Upload permission could not be issued", retryable=True)
            try:
                body = response.json()
                url = self._signed_url(body.get("url") or body.get("signedURL"), "object/upload/sign", key)
            except (ValueError, AttributeError):
                raise APIError(503, "invalid_storage_receipt", "Upload permission could not be verified", retryable=True) from None
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        return {"upload_url": url, "expires_at": expires_at, "method": "PUT", "headers": {"Content-Type": media_type}}

    def download_access(self, capture_ref: dict, files: list[dict]) -> dict:
        prefix, _ = self._reference(capture_ref)
        snapshot = capture_ref.get("snapshot_id")
        if not isinstance(snapshot, str) or "/" in snapshot or not snapshot:
            raise _invalid("Accepted snapshot ID is missing")
        pin_prefix = prefix + "accepted/" + snapshot + "/"
        manifest_pin = capture_ref.get("pinned_manifest_key", "")
        if not manifest_pin.startswith(pin_prefix + capture_ref["manifest_sha256"] + "/"):
            raise _invalid("Manifest is outside its accepted snapshot")
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.settings.source_url_lifetime_s)).isoformat().replace("+00:00", "Z")
        with self.connection() as client:
            def signed(key):
                response = client.post(self._url("object/sign", key), headers=self._headers(), json={"expiresIn": self.settings.source_url_lifetime_s}, follow_redirects=False)
                if response.status_code != 200:
                    raise APIError(503, "capture_storage_unavailable", "Source access could not be refreshed", retryable=True)
                try:
                    return self._signed_url(response.json().get("signedURL"), "object/sign", key)
                except (ValueError, AttributeError):
                    raise APIError(503, "invalid_storage_receipt", "Source access could not be verified", retryable=True) from None
            items = []
            for item in files:
                pinned = item.get("pinned_object_key", "")
                if pinned != pin_prefix + item["sha256"] + "/" + item["name"]:
                    raise _invalid("File is outside its accepted snapshot")
                metadata = {key: value for key, value in item.items() if key != "pinned_object_key"}
                items.append(metadata | {"download_url": signed(pinned), "expires_at": expires_at})
            return {"manifest_url": signed(manifest_pin), "expires_at": expires_at, "items": items}
