import os
import re
import math
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

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
    localization_base_url: str = ""
    localization_service_token: str = field(default="", repr=False)
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

    database_url: str = field(default="", repr=False)
    bim_storage: str = "local"
    meta_base_url: str = "https://api.meta.ai/v1"
    meta_model: str = "muse-spark-1.3"
    meta_api_key: str = field(default="", repr=False)
    meta_timeout_s: float = 30.0
    agent_max_steps: int = 6
    remote_agents_json: str = field(default="[]", repr=False)
    remote_agent_secrets: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.meta_timeout_s) or not 1 <= self.meta_timeout_s <= 120:
            raise ValueError("INSPECTOR_META_TIMEOUT_S must be between 1 and 120")
        if not 1 <= self.agent_max_steps <= 12:
            raise ValueError("INSPECTOR_AGENT_MAX_STEPS must be between 1 and 12")
        if self.meta_base_url:
            parsed_meta = urlsplit(self.meta_base_url)
            if (parsed_meta.scheme not in ("https", "http") or not parsed_meta.hostname
                    or parsed_meta.username is not None or parsed_meta.password is not None
                    or parsed_meta.query or parsed_meta.fragment
                    or (parsed_meta.scheme == "http" and parsed_meta.hostname not in ("localhost", "127.0.0.1", "::1"))):
                raise ValueError("INSPECTOR_META_BASE_URL requires HTTPS (HTTP is allowed on loopback only)")
            parsed_meta.port
        if self.meta_api_key and (not self.meta_api_key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in self.meta_api_key)):
            raise ValueError("Invalid INSPECTOR_META_API_KEY format")
        if len(self.meta_model) > 200 or any(ord(c) < 32 for c in self.meta_model):
            raise ValueError("Invalid INSPECTOR_META_MODEL")
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
        for token in (self.geometry_api_token, self.geometry_service_token, self.localization_service_token):
            if token:
                if not token.isascii() or token != token.strip():
                    raise ValueError("Service tokens must be ASCII without surrounding whitespace")
                tokens.append(token)
        if len(tokens) != len(set(tokens)):
            raise ValueError("Operator, device and service tokens must differ")
        for name in ("geometry_base_url", "inspector_public_base_url", "localization_base_url"):
            value = getattr(self, name)
            if value:
                parsed = urlsplit(value)
                if (parsed.scheme not in ("https", "http") or not parsed.hostname
                        or parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment):
                    raise ValueError(f"{name} must be a configured HTTP(S) base URL")
                parsed.port  # Validate port syntax even though URL construction preserves it.
        if self.database_url:
            try:
                parsed_db = urlsplit(self.database_url)
                valid = (parsed_db.scheme in ("postgres", "postgresql") and parsed_db.hostname
                         and parsed_db.username and parsed_db.password and parsed_db.path.strip("/")
                         and parse_qs(parsed_db.query).get("sslmode", [""])[0] in ("require", "verify-ca", "verify-full"))
                parsed_db.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("INSPECTOR_DATABASE_URL requires a PostgreSQL URI with credentials and TLS")
        if self.bim_storage not in ("local", "supabase"):
            raise ValueError("INSPECTOR_BIM_STORAGE must be local or supabase")
        if self.bim_storage == "supabase" and not self.supabase_enabled:
            raise ValueError("Supabase BIM storage requires Supabase configuration")
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
        from .a2a_protocol import remote_profiles
        agents_json = get("INSPECTOR_REMOTE_AGENTS_JSON", "[]")
        profiles = remote_profiles(agents_json)
        credential_refs = {name for profile in profiles for name in (profile.token_env, profile.tool_token_env) if name}
        return cls(
            meta_base_url=get("INSPECTOR_META_BASE_URL", "https://api.meta.ai/v1"),
            meta_model=get("INSPECTOR_META_MODEL", "muse-spark-1.3"),
            meta_api_key=get("INSPECTOR_META_API_KEY", ""),
            meta_timeout_s=float(get("INSPECTOR_META_TIMEOUT_S", "30")),
            agent_max_steps=int(get("INSPECTOR_AGENT_MAX_STEPS", "6")),
            remote_agents_json=agents_json,
            remote_agent_secrets={name: get(name, "") for name in credential_refs},
            database_url=get("INSPECTOR_DATABASE_URL", ""),
            bim_storage=get("INSPECTOR_BIM_STORAGE", "local"),
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
            localization_base_url=get("INSPECTOR_LOCALIZATION_BASE_URL", ""),
            localization_service_token=get("INSPECTOR_LOCALIZATION_SERVICE_TOKEN", ""),
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
