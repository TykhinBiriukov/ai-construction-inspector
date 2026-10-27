"""Download and unpack a session archive; no geometry processing or HTTP routes."""

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
import os
import stat
import time
from urllib.parse import urlsplit
import zipfile

import httpx
from dotenv import load_dotenv
from storage3.exceptions import StorageApiError
from supabase import Client


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    supabase_url: str = ""
    supabase_secret_key: str = field(default="", repr=False)
    storage_bucket: str = "inspection-captures"
    max_download_bytes: int = 1024**3
    max_extract_bytes: int = 3 * 1024**3
    max_members: int = 50_000
    download_deadline_s: float = 600

    @classmethod
    def from_env(cls):
        default = Path(__file__).resolve().parents[2] / "data"
        load_dotenv(default.parent / ".env")
        return cls(
            data_dir=Path(os.getenv("GEOMETRY_DATA_DIR", str(default))).resolve(),
            supabase_url=os.getenv("SUPABASE_URL", "").strip(),
            supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", "").strip(),
            storage_bucket=os.getenv("SUPABASE_STORAGE_BUCKET", "inspection-captures").strip(),
        )


def validate_url(url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Expected an HTTP(S) URL without embedded credentials")
    if len(url) > 8192 or parsed.fragment:
        raise ValueError("Invalid archive URL")


def download_archive(url: str, destination: Path, settings: Settings, *, transport=None):
    validate_url(url)
    started = time.monotonic()
    total = 0
    timeout = httpx.Timeout(30, connect=10)
    # Download directly from the configured storage without following redirects.
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, transport=transport) as client:
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            length = response.headers.get("Content-Length")
            if length is not None and (int(length) < 0 or int(length) > settings.max_download_bytes):
                raise ValueError("Archive exceeds the download size limit")
            with destination.open("wb") as output:
                for chunk in response.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > settings.max_download_bytes:
                        raise ValueError("Archive exceeds the download size limit")
                    if time.monotonic() - started > settings.download_deadline_s:
                        raise TimeoutError("Archive download deadline exceeded")
                    output.write(chunk)
    if not total:
        raise ValueError("Downloaded archive is empty")


class ArchiveError(ValueError):
    """Archive violates the input package contract."""


def extract_archive(archive_path: Path, destination: Path, settings: Settings) -> Path:
    destination.mkdir(exist_ok=False)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > settings.max_members or sum(member.file_size for member in members) > settings.max_extract_bytes:
            raise ArchiveError("Archive exceeds extraction limits")
        entries = []
        seen = set()
        for member in members:
            name = member.filename.replace("\\", "/")
            relative = PurePosixPath(name)
            mode = member.external_attr >> 16
            if (not name or "\x00" in member.orig_filename or relative.is_absolute()
                    or PureWindowsPath(name).drive or ".." in relative.parts or not relative.parts):
                raise ArchiveError("Unsafe archive path")
            for part in relative.parts:
                if ":" in part or part != part.strip() or part.endswith(".") or part.split(".")[0].upper() in reserved:
                    raise ArchiveError("Unsafe archive filename")
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or member.flag_bits & 1:
                raise ArchiveError("Links, special files and encrypted ZIP entries are unsupported")
            key = relative.as_posix().casefold()
            if key in seen:
                raise ArchiveError("Duplicate archive paths")
            seen.add(key)
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ArchiveError("Unsafe archive path")
            entries.append((member, target))
        total = 0
        for member, target in entries:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                while chunk := source.read(65536):
                    total += len(chunk)
                    if total > settings.max_extract_bytes:
                        raise ArchiveError("Archive exceeds extraction limits")
                    output.write(chunk)
    if not any(destination.iterdir()):
        raise ArchiveError("Archive contains no files")
    return destination


@dataclass(frozen=True)
class ExtractionResult:
    session_id: str
    directory: Path


class ArchiveServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ArchiveService:
    def __init__(self, settings: Settings, client: Client, *, download_fn=download_archive):
        self.settings = settings
        self.client = client
        self.download = download_fn

    def extract(self, session_id: str) -> ExtractionResult:
        import re
        from tempfile import TemporaryDirectory

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id):
            raise ArchiveServiceError("invalid_request", "Invalid session_id")
        root = self.settings.data_dir.resolve()
        destination = root / session_id
        if destination.exists():
            raise ArchiveServiceError("session_exists", "Session folder already exists")
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".extract-", dir=root) as temporary:
            work = Path(temporary)
            try:
                # SDK authorizes access to the private file; stream it to disk to bound memory use.
                signed = self.client.storage.from_(self.settings.storage_bucket).create_signed_url(
                    f"{session_id}.zip", int(self.settings.download_deadline_s) + 60,
                )
                archive_url = signed["signedURL"]
                self.download(archive_url, work / "archive.zip", self.settings)
            except (StorageApiError, httpx.HTTPError, TimeoutError, ValueError):
                raise ArchiveServiceError("download_failed", "Could not download archive") from None
            try:
                content = extract_archive(work / "archive.zip", work / "content", self.settings)
            except (ArchiveError, zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
                raise ArchiveServiceError("invalid_archive", str(error)) from None
            try:
                content.rename(destination)
            except OSError:
                if destination.exists():
                    raise ArchiveServiceError("session_exists", "Session folder already exists") from None
                raise
        return ExtractionResult(session_id=session_id, directory=destination)
