"""Read only explicitly pinned chat assets through the existing private blob store."""
import base64
import hashlib
import io
from pathlib import PurePosixPath

from .discrepancy import Evidence, InspectionRequest, MeasurementEvidence, fingerprint
from .errors import APIError


def selected_request(service, connection, principal, chat_id, file_ids):
    """Explicitly selected compact candidate JSON; referenced assets must also be selected."""
    from .a2a_protocol import decode_json
    if not file_ids:
        return None
    found = []
    files = service.workspace.files(connection, principal, chat_id)
    for file_id in file_ids:
        file = files.get(file_id) or {}
        if (not file.get("name", "").lower().endswith(".json") or not file.get("text")
                or file.get("text_truncated") or file.get("deleted")):
            continue
        try:
            value = decode_json(file["text"])
        except (ValueError, TypeError, RecursionError):
            continue
        if not isinstance(value, dict) or value.get("kind") != "inspection_request":
            continue
        try:
            parsed = InspectionRequest.model_validate(value)
        except ValueError:
            raise APIError(422, "invalid_inspection_input", "The selected inspection_request JSON does not match its contract") from None
        if not {a.file_id for a in parsed.assets} <= set(file_ids):
            raise APIError(422, "inspection_asset_not_selected", "Select the evidence files referenced by the candidate JSON for this message")
        found.append(parsed)
    if len(found) > 1:
        raise APIError(422, "ambiguous_inspection_request", "Select one candidate batch per message")
    return found[0] if found else None


def pin_assets(service, connection, principal, chat, request):
    if request.project_id != principal.project_id or request.project_id != chat["project_id"]:
        raise APIError(403, "inspection_scope_mismatch", "The candidate batch belongs to another project")
    source = request.source
    if source.session_id is not None:
        session = service._load(connection, "sessions", str(source.session_id))
        service._check_access(principal, session)
        if chat.get("session_id") != str(source.session_id) or not session.get("source"):
            raise APIError(409, "inspection_source_mismatch", "Select this chat's accepted capture source")
        result = session.get("geometry_result") or {}
        if (source.manifest_sha256 != session["source"]["manifest_sha256"]
                or (source.geometry_job_id is not None and str(source.geometry_job_id) != result.get("job_id"))
                or (source.geometry_revision is not None and source.geometry_revision != result.get("geometry_revision"))
                or (source.model_version is not None and source.model_version != session["model_version"])):
            raise APIError(409, "inspection_source_mismatch", "The source or geometry revision does not match the accepted capture")
        if source.model_sha256 is not None:
            inspection = service._load(connection, "inspections", session["inspection_id"])
            if source.model_sha256 != (inspection.get("bim_model") or {}).get("sha256"):
                raise APIError(409, "inspection_source_mismatch", "The reference model content does not match")
    elif any(value is not None for value in (source.manifest_sha256, source.geometry_job_id, source.geometry_revision)):
        raise APIError(422, "inspection_source_mismatch", "Capture bindings require a session ID")
    files = service.workspace.files(connection, principal, chat["chat_id"])
    pinned = {}
    for asset in request.assets:
        file = files.get(asset.file_id)
        imported = bool(file and file.get('origin') == 'geometry' and source.session_id is not None
            and file.get('session_id') == str(source.session_id)
            and file.get('geometry_job_id') == str(source.geometry_job_id)
            and file.get('manifest_sha256') == source.manifest_sha256
            and file.get('result_manifest_sha256') == result.get('result_manifest_sha256'))
        if not file or file.get("deleted") or (file.get("origin") != "upload" and not imported) or file.get("sha256") != asset.sha256:
            raise APIError(422, "inspection_asset_unavailable", "An evidence file is unavailable or its hash differs")
        if source.origin == 'geometry' and not imported:
            raise APIError(422, 'inspection_asset_unavailable', 'Geometry-origin evidence must come from the accepted server import')
        if asset.kind == "photo" and PurePosixPath(file["name"]).suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            raise APIError(422, "inspection_asset_unavailable", "Photo evidence must refer to an uploaded image")
        pinned[asset.asset_id] = {key: file[key] for key in ("file_id", "name", "sha256", "byte_length")}
    scope = "inspection-request:" + chat["chat_id"]
    body = request.model_dump(mode="json")
    if service.database.replay(connection, scope, str(request.request_id), body) is None:
        service.database.save_receipt(connection, scope, str(request.request_id), body, {"request_sha256": fingerprint(request)})
    return pinned


def read_evidence(service, run, asset_id):
    request = InspectionRequest.model_validate(run["inspection_request"])
    ref = next((a for a in request.assets if a.asset_id == asset_id), None)
    file = run.get("inspection_asset_snapshot", {}).get(asset_id)
    if ref is None or not file or file["file_id"] != ref.file_id or file["sha256"] != ref.sha256:
        raise APIError(403, "inspection_asset_forbidden", "This asset is outside the frozen inspection request")
    common = dict(asset_id=ref.asset_id, file_id=ref.file_id, sha256=ref.sha256, kind=ref.kind,
                  byte_length=file["byte_length"], media_type="application/octet-stream", available=False)
    suffix = PurePosixPath(file["name"]).suffix.lower()
    images = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    text = suffix in (".json", ".txt", ".md", ".csv")
    if suffix not in images and not text:
        return Evidence(**common, limitation="Opaque IFC/cloud/binary content requires a derived scoped representation; no geometry was parsed.").model_dump(mode="json")
    if file["byte_length"] > (8_000_000 if suffix in images else 128_000):
        return Evidence(**common, limitation="This evidence exceeds the bounded preview/text budget; provide a smaller derived representation.").model_dump(mode="json")
    try:
        path = service.bim_store.path_for(ref.sha256)
        with path.open("rb") as stream:
            raw = stream.read(file["byte_length"] + 1)
        if len(raw) != file["byte_length"] or hashlib.sha256(raw).hexdigest() != ref.sha256:
            raise APIError(409, "inspection_asset_changed", "Pinned evidence content could not be verified")
    except APIError:
        raise
    except (OSError, ValueError):
        raise APIError(503, "inspection_asset_read_unavailable", "Pinned evidence is temporarily unavailable", retryable=True) from None
    if text:
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeError:
            return Evidence(**common, limitation="A readable UTF-8 evidence representation is required.").model_dump(mode="json")
        common.update(media_type="application/json" if suffix == ".json" else "text/plain", available=True)
        if ref.kind == "measurement" and suffix == ".json":
            from .a2a_protocol import decode_json
            try:
                value = decode_json(decoded)
                if isinstance(value, dict) and value.get("kind") == "measurement_evidence":
                    report = MeasurementEvidence.model_validate(value)
                    return Evidence(**common, reported_measurements=report.measurements,
                        limitation="Verified file integrity and typed supplied measurements; not independent certification of the upstream algorithm.").model_dump(mode="json")
            except (ValueError, TypeError, RecursionError):
                raise APIError(422, "invalid_measurement_evidence", "The supplied measurement JSON is invalid") from None
        return Evidence(**common, text=decoded[:12000], truncated=len(decoded) > 12000,
                        limitation="Supplied file content; algorithm/model claims have not been independently certified.").model_dump(mode="json")
    common["media_type"] = images[suffix]
    try:
        from PIL import Image, ImageOps
        with Image.open(io.BytesIO(raw)) as source:
            width, height = source.size
            if width * height > 20_000_000 or source.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError
            orientation = source.getexif().get(274, 1)
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((768, 768))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=65)
            preview = output.getvalue()
            if len(preview) > 190_000:
                raise ValueError
            transform = f"Full image; source {width}x{height}; EXIF orientation {orientation}; transposed and resized to {image.width}x{image.height}; no metric inference from preview."
    except ImportError:
        if suffix not in (".jpg", ".jpeg") or len(raw) > 190_000 or not raw.startswith(b"\xff\xd8\xff"):
            return Evidence(**common, limitation="Native preview support is unavailable; supply an inline-sized JPEG or enable the evidence dependency.").model_dump(mode="json")
        preview, transform = raw, "Original bounded JPEG bytes; no conversion or metric inference."
    except (OSError, ValueError, Image.DecompressionBombError):
        return Evidence(**common, limitation="The image is unsupported or exceeds the safe preview budget; no visual conclusion is available.").model_dump(mode="json")
    common["available"] = True
    return Evidence(**common, image_data="data:image/jpeg;base64," + base64.b64encode(preview).decode("ascii"),
                    preview_sha256=hashlib.sha256(preview).hexdigest(), preview_transform=transform).model_dump(mode="json")
