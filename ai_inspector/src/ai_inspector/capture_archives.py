"""Validate one capture ZIP without extracting archive paths onto the filesystem."""

import hashlib
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path
from uuid import UUID

from .capture_sources import _incomplete
from .errors import APIError
from .supabase_captures import SupabaseCaptureSourceStore


def _invalid(message: str) -> APIError:
    return APIError(422, "capture_archive_invalid", message)


def _entries(archive: zipfile.ZipFile, settings) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    if len(entries) > settings.max_archive_entries:
        raise _invalid("Capture ZIP exceeds the configured entry count")
    files, seen, total = {}, set(), 0
    for item in entries:
        name = item.filename
        original = getattr(item, "orig_filename", name)
        parts = name.rstrip("/").split("/")
        if (not name or "\x00" in original or "\\" in name or name.startswith("/")
                or any(part in ("", ".", "..") or ":" in part for part in parts)):
            raise _invalid("Capture ZIP contains an unsafe member path")
        normalized = name.rstrip("/").casefold()
        if normalized in seen:
            raise _invalid("Capture ZIP contains duplicate or ambiguous member paths")
        seen.add(normalized)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise _invalid("Capture ZIP may contain only regular files and directories")
        if item.flag_bits & 1:
            raise _invalid("Encrypted capture ZIPs are not supported")
        if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise _invalid("Capture ZIP must use stored or deflated compression")
        total += item.file_size
        if total > min(settings.max_unpacked_archive_bytes, settings.max_capture_bytes):
            raise _invalid("Capture ZIP exceeds the configured unpacked byte limit")
        if item.is_dir():
            continue
        # Finder metadata is not evidence; it still passed the path/type/size checks.
        if parts[0] == "__MACOSX" or parts[-1] == ".DS_Store":
            continue
        files[name] = item
    if "capture-manifest.json" not in files:
        raise _invalid("Capture ZIP must contain capture-manifest.json at its root")
    if files["capture-manifest.json"].file_size > settings.max_manifest_bytes:
        raise _invalid("The ZIP manifest exceeds the configured byte limit")
    return files


class _ArchiveSnapshotStore(SupabaseCaptureSourceStore):
    def __init__(self, parent, archive, entries, logical_manifest_key, session_prefix):
        super().__init__(parent.settings, parent.http)
        self.archive = archive
        self.entries = entries
        self.logical_manifest_key = logical_manifest_key
        self.session_prefix = session_prefix
        self.used = set()

    def _verify_object(self, descriptor: dict, *, read: bool = False):
        key, size, digest = descriptor["object_key"], descriptor["byte_length"], descriptor["sha256"]
        if key == self.logical_manifest_key:
            name = "capture-manifest.json"
        elif key.startswith(self.session_prefix):
            name = key[len(self.session_prefix):]
        else:
            raise _invalid("ZIP manifest refers to another session")
        entry = self.entries.get(name)
        if entry is None:
            raise _incomplete("ZIP is missing a manifest file", key, "missing_files")
        if entry.file_size != size:
            raise _incomplete("ZIP file size differs from its manifest", key, "size_mismatches")
        self.used.add(name)
        staging = Path(self.settings.data_dir) / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=staging) as temporary:
            checksum, actual_size = hashlib.sha256(), 0
            with self.archive.open(entry) as source:
                while chunk := source.read(64 * 1024):
                    actual_size += len(chunk)
                    if actual_size > size:
                        raise _incomplete("ZIP file size differs from its manifest", key, "size_mismatches")
                    checksum.update(chunk)
                    temporary.write(chunk)
            if actual_size != size or checksum.hexdigest() != digest:
                raise _incomplete("ZIP file integrity differs from its manifest", key, "hash_mismatches")
            pinned = self._pin_prefix + digest + "/" + key.rsplit("/", 1)[-1]
            with self.connection() as client:
                self._publish_pin(client, pinned, temporary, size, digest)
            self._pins[key] = pinned
            if read:
                temporary.seek(0)
                return temporary.read()
        return None


def validate_archive(parent, session: dict, capture_ref: dict) -> dict:
    settings = parent.settings
    parent._client()
    prefix = session["storage"]["key_prefix"]
    if (capture_ref.get("storage_id") != "capture-supabase"
            or session["storage"].get("bucket") != settings.supabase_storage_bucket
            or session["storage"].get("project_url") != settings.supabase_url.rstrip("/")):
        raise _invalid("Capture ZIP belongs to another storage configuration")
    key = capture_ref.get("archive_key", "")
    try:
        completion_id = str(UUID(key.rsplit("/", 1)[-1].removesuffix(".zip")))
    except (ValueError, AttributeError):
        raise _invalid("Capture ZIP key requires a completion UUID") from None
    if key != prefix + f"archives/{completion_id}.zip":
        raise _invalid("Capture ZIP is outside its session prefix")
    descriptor = {"object_key": key, "byte_length": capture_ref.get("archive_byte_length"), "sha256": capture_ref.get("archive_sha256")}
    parent._descriptor(descriptor)
    if descriptor["byte_length"] > settings.max_archive_bytes:
        raise _invalid("Capture ZIP exceeds the configured upload limit")
    staging = Path(settings.data_dir) / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, dir=staging) as archive_bytes:
        with parent.connection() as client:
            parent._stream_to(client, key, archive_bytes, descriptor["byte_length"], descriptor["sha256"])
        archive_bytes.seek(0)
        try:
            with zipfile.ZipFile(archive_bytes) as archive:
                entries = _entries(archive, settings)
                with archive.open(entries["capture-manifest.json"]) as source:
                    manifest_bytes = source.read(settings.max_manifest_bytes + 1)
                if len(manifest_bytes) > settings.max_manifest_bytes:
                    raise _invalid("The ZIP manifest exceeds the configured byte limit")
                manifest_key = prefix + f"manifests/{completion_id}.json"
                logical_ref = {"format": "supabase_capture_v1", "storage_id": "capture-supabase", "manifest_key": manifest_key,
                               "manifest_byte_length": len(manifest_bytes), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()}
                store = _ArchiveSnapshotStore(parent, archive, entries, manifest_key, prefix)
                inventory = store.validate(session, logical_ref)
                if set(entries) != store.used:
                    raise _invalid("Capture ZIP contains files absent from its manifest")
                pinned_archive = prefix + f"accepted/{completion_id}/{descriptor['sha256']}/capture.zip"
                with parent.connection() as client:
                    parent._publish_pin(client, pinned_archive, archive_bytes, descriptor["byte_length"], descriptor["sha256"])
                inventory["pinned_capture_ref"]["archive"] = {
                    "object_key": key, "pinned_object_key": pinned_archive, "byte_length": descriptor["byte_length"],
                    "sha256": descriptor["sha256"], "media_type": "application/zip",
                }
                return inventory
        except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, UnicodeError, zlib.error):
            raise _invalid("Capture ZIP is corrupt, encrypted, or unsupported") from None
