"""Create ignored, role-specific connection files without printing credentials."""

import argparse
import json
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from ai_inspector.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ensure-tokens", action="store_true")
    parser.add_argument("--public-url")
    parser.add_argument("--mock-geometry", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if args.ensure_tokens:
        values = dotenv_values(env_file, interpolate=False)
        for key in ("INSPECTOR_OPERATOR_TOKEN", "INSPECTOR_DEVICE_TOKEN", "INSPECTOR_GEOMETRY_API_TOKEN", "INSPECTOR_GEOMETRY_SERVICE_TOKEN"):
            if not values.get(key):
                set_key(env_file, key, token_urlsafe(32))
        print("Server identity settings are ready; credentials were not printed.")
    if args.public_url:
        url = args.public_url.rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("Public URL must be an HTTPS origin")
        settings = Settings.from_env(env_file)
        folder = root / ".local"
        folder.mkdir(exist_ok=True)
        mode = "mock" if args.mock_geometry else "external" if settings.geometry_base_url else "unconfigured"
        device = {
            "inspector_base_url": url,
            "device_websocket_url": url.replace("https://", "wss://", 1) + f"/v1/devices/{settings.device_id}/events/ws",
            "device_id": settings.device_id, "project_id": settings.project_id,
            "device_token": settings.device_token, "schema_version": "0.1",
            "capture_manifest_format": "capture_manifest_supabase_v1", "completion_format": "supabase_capture_v1",
            "geometry_mode": mode,
        }
        operator = {"inspector_base_url": url, "operator_token": settings.operator_token,
                    "device_id": settings.device_id, "project_id": settings.project_id,
                    "session_create_path": "/v1/capture-sessions", "geometry_mode": mode}
        (folder / "ios-connection.json").write_text(json.dumps(device, indent=2) + "\n", encoding="utf-8")
        (folder / "web-connection.json").write_text(json.dumps(operator, indent=2) + "\n", encoding="utf-8")
        (folder / "connection-public.json").write_text(json.dumps({"base_url": url, "device_id": settings.device_id, "geometry_mode": mode}, indent=2) + "\n", encoding="utf-8")
        print("Public API:", url)
        print("Private iOS configuration: .local/ios-connection.json")
        print("Private operator configuration: .local/web-connection.json")


if __name__ == "__main__":
    main()
