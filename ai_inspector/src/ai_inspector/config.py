import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    operator_token: str = field(repr=False)
    device_token: str = field(repr=False)
    project_id: str = "demo-building"
    device_id: str = "iphone-1"
    policy_version: str = "demo-policy-v1"
    storage_id: str = "capture-s3"
    max_bim_bytes: int = 100 * 1024 * 1024
    capture_bucket: str = ""
    capture_region: str = "eu-central-1"
    max_manifest_bytes: int = 8 * 1024 * 1024
    max_capture_files: int = 40000
    max_capture_bytes: int = 50 * 1024**3
    source_url_lifetime_s: int = 900
    geometry_base_url: str = ""
    inspector_public_base_url: str = ""
    geometry_api_token: str = field(default="", repr=False)
    geometry_service_token: str = field(default="", repr=False)
    geometry_profile_version: str = "geometry-v1"
    worker_interval_s: float = 1.0
    geometry_timeout_s: float = 15.0
    geometry_accepts_rgb_only: bool = False
    supabase_url: str = ""
    supabase_storage_bucket: str = ""
    supabase_secret_key: str = field(default="", repr=False)
    geometry_poll_interval_s: float = 2.0
    cors_origins: tuple[str, ...] = ()
    max_capture_file_bytes: int = 50 * 1024 * 1024
    geometry_mode: str = "external"
    max_archive_bytes: int = 50 * 1024 * 1024
    max_unpacked_archive_bytes: int = 2 * 1024**3
    max_archive_entries: int = 40000

    def __post_init__(self) -> None:
        if not self.operator_token.strip() or not self.device_token.strip():
            raise ValueError("Set INSPECTOR_OPERATOR_TOKEN and INSPECTOR_DEVICE_TOKEN")
        if self.operator_token == self.device_token:
            raise ValueError("Operator and device tokens must differ")
        if not self.operator_token.isascii() or not self.device_token.isascii():
            raise ValueError("Bearer tokens must contain ASCII characters")
        for name in ("project_id", "device_id", "policy_version", "storage_id"):
            if not re.fullmatch(IDENTIFIER_PATTERN, getattr(self, name)):
                raise ValueError(f"Invalid {name}")
        if self.max_bim_bytes < 1:
            raise ValueError("max_bim_bytes must be positive")
        for name in ("max_manifest_bytes", "max_capture_files", "max_capture_bytes", "source_url_lifetime_s"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.source_url_lifetime_s > 3600:
            raise ValueError("source_url_lifetime_s must be at most 3600")
        if self.worker_interval_s <= 0 or self.geometry_timeout_s <= 0:
            raise ValueError("Worker interval and geometry timeout must be positive")
        if self.geometry_poll_interval_s <= 0 or self.max_capture_file_bytes < 1:
            raise ValueError("Geometry poll interval and capture file limit must be positive")
        if self.geometry_mode not in ("external", "mock"):
            raise ValueError("INSPECTOR_GEOMETRY_MODE must be external or mock")
        if min(self.max_archive_bytes, self.max_unpacked_archive_bytes, self.max_archive_entries) < 1:
            raise ValueError("Capture archive limits must be positive")
        for origin in self.cors_origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError("CORS origins must be HTTP(S) origins")
        tokens = [self.operator_token, self.device_token]
        for token in (self.geometry_api_token, self.geometry_service_token):
            if token:
                if not token.isascii() or token != token.strip():
                    raise ValueError("Service tokens must be ASCII without surrounding whitespace")
                tokens.append(token)
        if len(tokens) != len(set(tokens)):
            raise ValueError("Operator, device and service tokens must differ")
        for name in ("geometry_base_url", "inspector_public_base_url"):
            value = getattr(self, name)
            if value:
                parsed = urlsplit(value)
                if (parsed.scheme not in ("https", "http") or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment):
                    raise ValueError(f"{name} must be a configured HTTP(S) base URL")
                parsed.port  # Validate port syntax even though URL construction preserves it.
        configured = (bool(self.supabase_url), bool(self.supabase_storage_bucket), bool(self.supabase_secret_key))
        if any(configured) and not all(configured):
            raise ValueError("Set SUPABASE_URL, SUPABASE_STORAGE_BUCKET and SUPABASE_SECRET_KEY together")
        if self.supabase_enabled:
            parsed = urlsplit(self.supabase_url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/")
                    or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
                raise ValueError("SUPABASE_URL must be an HTTPS project origin")
            parsed.port
            if not re.fullmatch(IDENTIFIER_PATTERN, self.supabase_storage_bucket):
                raise ValueError("Invalid SUPABASE_STORAGE_BUCKET")
            if (not self.supabase_secret_key.isascii() or self.supabase_secret_key != self.supabase_secret_key.strip()
                    or any(ord(character) < 33 for character in self.supabase_secret_key)):
                raise ValueError("Invalid SUPABASE_SECRET_KEY format")

    @property
    def supabase_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_storage_bucket and self.supabase_secret_key)

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        # Process environment wins over local .env without modifying os.environ.
        values = {key: value for key, value in dotenv_values(env_file or Path.cwd() / ".env", interpolate=False).items() if value is not None}
        values.update(os.environ)
        get = values.get
        return cls(
            data_dir=Path(get("INSPECTOR_DATA_DIR", "data")).resolve(),
            operator_token=get("INSPECTOR_OPERATOR_TOKEN", ""),
            device_token=get("INSPECTOR_DEVICE_TOKEN", ""),
            project_id=get("INSPECTOR_PROJECT_ID", "demo-building"),
            device_id=get("INSPECTOR_DEVICE_ID", "iphone-1"),
            policy_version=get("INSPECTOR_POLICY_VERSION", "demo-policy-v1"),
            max_bim_bytes=int(get("INSPECTOR_MAX_BIM_BYTES", "104857600")),
            capture_bucket=get("INSPECTOR_CAPTURE_BUCKET", ""),
            capture_region=get("INSPECTOR_CAPTURE_REGION", "eu-central-1"),
            max_manifest_bytes=int(get("INSPECTOR_MAX_MANIFEST_BYTES", "8388608")),
            max_capture_files=int(get("INSPECTOR_MAX_CAPTURE_FILES", "40000")),
            max_capture_bytes=int(get("INSPECTOR_MAX_CAPTURE_BYTES", "53687091200")),
            source_url_lifetime_s=int(get("INSPECTOR_SOURCE_URL_LIFETIME_S", "900")),
            geometry_base_url=get("INSPECTOR_GEOMETRY_BASE_URL", ""),
            inspector_public_base_url=get("INSPECTOR_PUBLIC_BASE_URL", ""),
            geometry_api_token=get("INSPECTOR_GEOMETRY_API_TOKEN", ""),
            geometry_service_token=get("INSPECTOR_GEOMETRY_SERVICE_TOKEN", ""),
            geometry_profile_version=get("INSPECTOR_GEOMETRY_PROFILE_VERSION", "geometry-v1"),
            worker_interval_s=float(get("INSPECTOR_WORKER_INTERVAL_S", "1")),
            geometry_timeout_s=float(get("INSPECTOR_GEOMETRY_TIMEOUT_S", "15")),
            geometry_accepts_rgb_only=get("INSPECTOR_GEOMETRY_ACCEPTS_RGB_ONLY", "false").lower() == "true",
            supabase_url=get("SUPABASE_URL", ""),
            supabase_storage_bucket=get("SUPABASE_STORAGE_BUCKET", ""),
            supabase_secret_key=get("SUPABASE_SECRET_KEY", ""),
            geometry_poll_interval_s=float(get("INSPECTOR_GEOMETRY_POLL_INTERVAL_S", "2")),
            cors_origins=tuple(value.strip().rstrip("/") for value in get("INSPECTOR_CORS_ORIGINS", "").split(",") if value.strip()),
            max_capture_file_bytes=int(get("INSPECTOR_MAX_CAPTURE_FILE_BYTES", "52428800")),
            geometry_mode=get("INSPECTOR_GEOMETRY_MODE", "external"),
            max_archive_bytes=int(get("INSPECTOR_MAX_ARCHIVE_BYTES", "52428800")),
            max_unpacked_archive_bytes=int(get("INSPECTOR_MAX_UNPACKED_ARCHIVE_BYTES", "2147483648")),
            max_archive_entries=int(get("INSPECTOR_MAX_ARCHIVE_ENTRIES", "40000")),
        )
