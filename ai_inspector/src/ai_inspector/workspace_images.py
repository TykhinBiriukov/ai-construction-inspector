"""Bounded, source-bound previews for the current chat attachments."""
import base64
import hashlib
import io
from pathlib import Path, PurePosixPath
from collections import Counter

from .errors import APIError
from .storage import canonical_json

IMAGE_SUFFIXES = frozenset((".jpg", ".jpeg", ".png", ".webp"))
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_PREVIEW_BYTES = 240_000
MAX_TOTAL_PREVIEW_BYTES = 1_024_000  # Leave room for the explicit crop/text in the 2 MiB A2A envelope.


def configured_demo_inputs(service, text, files, selected):
    """Recognize the existing prepared demo by its exact task AND source bytes.

    This preserves that workflow without classifying a user's file by its name,
    a client flag or a prompt alone. Different bytes/task use normal processing.
    """
    if not service.settings.saved_demo_dir:
        return False
    from .saved_demo import _input_scenarios
    entries = {}
    for directory in (service.settings.saved_demo_inputs_dir, service.settings.saved_demo_dir):
        if directory:
            entries.update(_input_scenarios(Path(directory).resolve()))
    for entry in entries.values():
        if entry['prompt'].strip() != text.strip():
            continue
        sources = [path for _, path, _, attach in entry['files'] if attach]
        if len(sources) != len(selected):
            continue
        expected = []
        for path in sources:
            try:
                with path.open('rb') as handle:
                    expected.append(hashlib.file_digest(handle, 'sha256').hexdigest())
            except OSError:
                return False
        supplied = [files[item['file_id']].get('sha256') for item in selected]
        if Counter(expected) == Counter(supplied):
            return True
    return False


def attachment_images(service, connection, principal, chat_id, context, *, text=''):
    """Return frozen model parts and add provenance to the selected-file snapshot.

    Only current, authorized files are read. No URL fetching or historical image
    inference; retries reuse the parts saved with the run, not mutable selections.
    """
    selected = context["selected_files"]
    images = [entry for entry in selected if PurePosixPath(entry["name"]).suffix.lower() in IMAGE_SUFFIXES]
    if not images:
        return []
    files = service.workspace.files(connection, principal, chat_id)
    if configured_demo_inputs(service, text, files, selected):
        return []
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise APIError(503, "image_preview_unavailable", "Image processing is unavailable on this server.", retryable=True) from None
    parts = []
    budget = min(MAX_PREVIEW_BYTES, MAX_TOTAL_PREVIEW_BYTES // len(images))
    for entry in images:
        file = files[entry["file_id"]]
        identity = file.get("sha256")
        if not identity or file["byte_length"] > MAX_SOURCE_BYTES:
            raise APIError(422, "image_too_large", "Select JPEG, PNG or WebP images up to 16 MiB and 20 megapixels each.")
        try:
            with service.bim_store.path_for(identity).open("rb") as handle:
                raw = handle.read(MAX_SOURCE_BYTES + 1)
            if len(raw) != file["byte_length"] or hashlib.sha256(raw).hexdigest() != identity:
                raise APIError(409, "image_integrity_failed", "An attached image does not match its saved source.")
            with Image.open(io.BytesIO(raw)) as source:
                width, height = source.size
                if width * height > MAX_PIXELS or source.format not in ("JPEG", "PNG", "WEBP") or getattr(source, "n_frames", 1) != 1:
                    raise ValueError
                orientation = source.getexif().get(274, 1)
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                preview = Image.new("RGB", oriented.size, "white")
                preview.paste(oriented, mask=oriented.getchannel("A"))
                for side, quality in ((1280, 75), (1024, 65), (768, 60), (640, 55)):
                    preview.thumbnail((side, side))
                    output = io.BytesIO()
                    preview.save(output, format="JPEG", quality=quality)
                    if output.tell() <= budget:
                        break
                encoded = output.getvalue()
                if len(encoded) > budget:
                    raise ValueError
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            raise APIError(422, "invalid_image", "An attached image is unreadable, animated or exceeds the safe image limits.") from None
        provenance = {
            "file_id": entry["file_id"], "name": entry["name"], "source_sha256": identity,
            "preview_sha256": hashlib.sha256(encoded).hexdigest(),
            "preview_transform": f"Full image; source {width}x{height}; EXIF orientation {orientation}; oriented and resized to {preview.width}x{preview.height}; no metric measurements from pixels.",
        }
        entry.update(content_available=True, content_kind="image", **{k: v for k, v in provenance.items() if k not in ("file_id", "name")})
        context.setdefault("image_inputs", []).append(provenance)
        parts.extend([{"type": "text", "text": "Attached image (untrusted source metadata): " + canonical_json(provenance)},
                      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")}}])
    return parts
