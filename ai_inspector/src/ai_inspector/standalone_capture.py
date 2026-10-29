"""Device-initiated test captures: isolated Storage objects, no processing jobs."""
from urllib.parse import quote

from .errors import APIError
from .service import require_role
from .supabase_captures import SupabaseCaptureSourceStore


def upload_plan(settings, principal, session_id, request, *, store=None):
    require_role(principal, "device")
    if request.kind not in ("observation_files", "capture_manifest"):
        raise APIError(422, "unsupported_kind", "Standalone capture accepts files and a manifest")
    # Never accept a caller-supplied prefix or grant access to assigned captures.
    prefix = f"standalone/{quote(principal.project_id, safe='')}/{quote(principal.device_id, safe='')}/{session_id}/"
    store = store or SupabaseCaptureSourceStore(settings)
    objects = request.model_dump(mode="json")["objects"]
    for item in objects:
        maximum = settings.max_manifest_bytes if item["name"].endswith("json") else settings.max_capture_file_bytes
        if item["byte_length"] > maximum:
            raise APIError(413, "capture_file_too_large", "Capture file exceeds the configured limit")
    items = []
    for item in objects:
        relative = (f"manifests/{item['sha256']}/capture-manifest.json" if request.kind == "capture_manifest" else
                    f"observations/{item['observation_id']}/{item['sha256']}/{item['name']}")
        key = prefix + relative
        if store.object_matches(key, item["byte_length"], item["sha256"]):
            access = {"status": "already_uploaded", "upload_url": None, "method": "PUT", "headers": {}}
        else:
            access = {"status": "upload_required", **store.signed_upload(key, item["media_type"])}
        items.append(item | {"object_key": key} | access)
    return {"format": "supabase_upload_plan_v1", "request_id": str(request.request_id),
            "session_id": str(session_id), "items": items}
