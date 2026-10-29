"""Download the files referenced by a Supabase capture manifest."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
import zipfile

import httpx
from storage3.exceptions import StorageApiError

from .archive import ArchiveServiceError, ExtractionResult, download_archive, extract_archive


class SessionDownloadService:
    def __init__(self, settings, client, *, download_fn=download_archive):
        self.settings, self.client, self.download = settings, client, download_fn

    def extract(self, project_id, session_id):
        for name, value in (("project_id", project_id), ("session_id", session_id)):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
                raise ArchiveServiceError("invalid_request", f"Invalid {name}")
        root = self.settings.data_dir.resolve() / "raw"
        destination = root / session_id
        if destination.exists():
            raise ArchiveServiceError("session_exists", "Session folder already exists")
        root.mkdir(parents=True, exist_ok=True)
        prefix = f"projects/{project_id}/sessions/{session_id}"
        bucket = self.client.storage.from_(self.settings.storage_bucket)
        started = time.monotonic()
        try:
            with TemporaryDirectory(prefix=".download-", dir=root) as temporary:
                staged = Path(temporary) / "session"
                original = staged / "original"
                original.mkdir(parents=True)

                def fetch(key, path, limit):
                    remaining = self.settings.download_deadline_s - (time.monotonic()-started)
                    if remaining <= 0:
                        raise TimeoutError()
                    signed = bucket.create_signed_url(key, int(remaining)+60)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.download(signed["signedURL"], path, replace(self.settings,
                                  max_download_bytes=limit, download_deadline_s=remaining))

                fetch(prefix+"/.session.json", original/".session.json", 65536)
                descriptor = json.loads((original/".session.json").read_text(encoding="utf-8"))
                if descriptor.get("project_id") != project_id or descriptor.get("session_id") != session_id:
                    raise ValueError("Session identity differs from request")
                manifests = bucket.list(prefix+"/manifests", {"limit": 2, "sortBy": {"column": "name", "order": "asc"}})
                if len(manifests) != 1 or not manifests[0].get("id"):
                    raise ValueError("Expected exactly one session manifest")
                name = manifests[0]["name"]
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.json", name):
                    raise ValueError("Invalid manifest filename")
                manifest_path = original/"manifests"/name
                fetch(prefix+"/manifests/"+name, manifest_path, min(4*1024**2, self.settings.max_download_bytes))
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("project_id") != project_id or manifest.get("session_id") != session_id:
                    raise ValueError("Manifest identity differs from request")
                if manifest.get("format") == "geometry_capture_parts_v1":
                    report = self._extract_parts(manifest, prefix, staged, fetch)
                    report.update(status="completed", project_id=project_id, session_id=session_id,
                                  manifest="manifests/"+name)
                    if time.monotonic()-started > self.settings.download_deadline_s:
                        raise TimeoutError()
                    (staged/"download-report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
                    try:
                        staged.rename(destination)
                    except OSError:
                        if destination.exists():
                            raise ArchiveServiceError("session_exists", "Session folder already exists") from None
                        raise
                    return ExtractionResult(session_id, destination)
                if manifest.get("format") != "capture_manifest_supabase_v1":
                    raise ArchiveServiceError("invalid_request", "Unsupported manifest format; expected capture_manifest_supabase_v1 or geometry_capture_parts_v1")
                observations = manifest.get("observations")
                if not isinstance(observations, list) or not observations or manifest.get("observation_count") != len(observations):
                    raise ValueError("Invalid observation count")
                jobs, seen, total = [], set(), (original/".session.json").stat().st_size+manifest_path.stat().st_size
                for observation in observations:
                    identity = observation.get("observation_id")
                    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", identity):
                        raise ValueError("Invalid observation ID")
                    files = observation.get("files")
                    if not isinstance(files, list) or not files:
                        raise ValueError("Observation contains no files")
                    for item in files:
                        filename, size, digest = item.get("name"), item.get("byte_length"), item.get("sha256")
                        if (not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", filename)
                                or filename.endswith(".") or filename.split(".")[0].upper() in
                                {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
                                or type(size) is not int or size <= 0 or not isinstance(digest, str)
                                or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)):
                            raise ValueError("Invalid manifest file description")
                        relative = f"observations/{identity}/{filename}"
                        key = prefix+"/"+relative
                        if item.get("object_key") != key or relative.casefold() in seen:
                            raise ValueError("Duplicate file or object outside the requested session")
                        seen.add(relative.casefold())
                        jobs.append((key, original/relative, size, digest.lower()))
                        total += size
                if len(jobs)+2 > self.settings.max_members or total > self.settings.max_download_bytes:
                    raise ValueError("Session exceeds download limits")

                def download_file(job):
                    key, path, size, digest = job
                    fetch(key, path, size)
                    with path.open("rb") as source:
                        actual = hashlib.file_digest(source, "sha256").hexdigest()
                    if path.stat().st_size != size or actual != digest:
                        raise ValueError("Downloaded file differs from manifest")

                with ThreadPoolExecutor(max_workers=4) as workers:
                    list(workers.map(download_file, jobs))
                if time.monotonic()-started > self.settings.download_deadline_s:
                    raise TimeoutError()
                (staged/"download-report.json").write_text(json.dumps({
                    "status": "completed", "project_id": project_id, "session_id": session_id,
                    "manifest": "manifests/"+name, "observation_count": len(observations),
                    "file_count": len(jobs)+2, "downloaded_bytes": total, "sha256_verified": True,
                }, indent=2)+"\n", encoding="utf-8")
                try:
                    staged.rename(destination)
                except OSError:
                    if destination.exists():
                        raise ArchiveServiceError("session_exists", "Session folder already exists") from None
                    raise
        except (StorageApiError, httpx.HTTPError, TimeoutError):
            raise ArchiveServiceError("download_failed", "Could not download session files from Supabase") from None
        except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, NotImplementedError, RuntimeError):
            raise ArchiveServiceError("invalid_request", "Invalid session manifest, file size or SHA-256") from None
        return ExtractionResult(session_id, destination)

    def _extract_parts(self, manifest, prefix, staged, fetch):
        """Independent ZIPs stay separate: repeated mesh/capture files cannot overwrite each other."""
        completion = manifest.get("completion_id")
        parts = manifest.get("parts")
        if (manifest.get("schema_version") != "1.0" or not isinstance(completion, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", completion)
                or not isinstance(parts, list) or not parts or len(parts) > self.settings.max_members):
            raise ValueError("Invalid capture parts manifest")
        seen, all_frames, jobs = set(), set(), []
        total = sum(path.stat().st_size for path in (staged/"original").rglob("*") if path.is_file())
        for part in parts:
            name, size, digest = part.get("name"), part.get("byte_length"), part.get("sha256")
            frames = part.get("frame_ids")
            if (not isinstance(name, str) or not re.fullmatch(r"capture-[0-9]{4,}\.zip", name)
                    or name.casefold() in seen or type(size) is not int or size <= 0
                    or not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
                    or not isinstance(frames, list) or not frames
                    or any(not isinstance(frame, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", frame) for frame in frames)
                    or len(set(frames)) != len(frames) or all_frames.intersection(frames)):
                raise ValueError("Invalid capture part")
            key = f"{prefix}/geometry/{completion}/{name}"
            if part.get("object_key") != key:
                raise ValueError("Part object outside requested session")
            seen.add(name.casefold()); all_frames.update(frames)
            jobs.append((part, key, size, digest.lower()))
            total += size
        if (type(manifest.get("frame_count")) is not int or manifest["frame_count"] != len(all_frames)
                or total > self.settings.max_download_bytes):
            raise ValueError("Invalid frame count or session download limit")
        archives = staged/"archives"
        archives.mkdir()
        unpacked = staged/"original"/"parts"
        unpacked.mkdir()
        expanded_bytes, member_count, outputs = 0, 0, []
        for part, key, size, digest in jobs:
            path = archives/part["name"]
            fetch(key, path, size)
            with path.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if path.stat().st_size != size or actual != digest:
                raise ValueError("Capture part size or SHA-256 differs from manifest")
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                expanded_bytes += sum(member.file_size for member in members)
                member_count += len(members)
            if expanded_bytes > self.settings.max_extract_bytes or member_count > self.settings.max_members:
                raise ValueError("Capture parts exceed extraction limits")
            folder = unpacked/path.stem
            extract_archive(path, folder, self.settings)
            outputs.append({"name": part["name"], "archive": path.relative_to(staged).as_posix(),
                            "directory": folder.relative_to(staged).as_posix(),
                            "frame_ids": part["frame_ids"], "segment_id": part.get("segment_id")})
        return {"format": "geometry_capture_parts_v1", "part_count": len(outputs),
                "frame_count": len(all_frames), "downloaded_bytes": total,
                "extracted_bytes": expanded_bytes, "extracted_members": member_count,
                "sha256_verified": True, "parts": outputs}
