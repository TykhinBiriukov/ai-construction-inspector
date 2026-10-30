import base64
import hashlib
import io
import math
import warnings
import threading

from PIL import Image, ImageDraw, ImageOps

from .models import Asset, Region

Image.MAX_IMAGE_PIXELS = 16_000_000
_PDF_LOCK = threading.Lock()


def decode(asset: Asset) -> Image.Image:
    try:
        raw = base64.b64decode(asset.data_base64, validate=True)
        if len(raw) > 6_000_000:
            raise ValueError("Image exceeds 6 MB")
        if asset.mime_type == "application/pdf":
            import pypdfium2 as pdfium
            with _PDF_LOCK, pdfium.PdfDocument(raw) as doc:
                if len(doc) > 200 or asset.page > len(doc):
                    raise ValueError("PDF page outside limits")
                page = doc[asset.page - 1]
                try:
                    width, height = page.get_size()
                    if min(width, height) <= 0 or max(width, height) > 20000:
                        raise ValueError("PDF page dimensions outside limits")
                    scale = min(2, 2400 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        return bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                expected = "PNG" if asset.mime_type == "image/png" else "JPEG"
                if image.format != expected or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("MIME mismatch or animated image")
                if image.width * image.height > 16_000_000 or min(image.size) < 16:
                    raise ValueError("Image dimensions outside allowed range")
                return ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:
        raise ValueError("Invalid image or image exceeds limits") from exc


def jpeg(image: Image.Image, edge: int = 1600) -> bytes:
    result = image.copy()
    result.thumbnail((edge, edge))
    buffer = io.BytesIO()
    result.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def data_url(image: Image.Image) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg(image)).decode("ascii")


def crop(image: Image.Image, region: Region) -> Image.Image:
    x, y, w, h = region.bbox
    width, height = image.size
    box = (math.floor(x * width), math.floor(y * height),
           min(width, math.ceil((x + w) * width)), min(height, math.ceil((y + h) * height)))
    return image.crop(box)


def evidence_image(image: Image.Image, region: Region) -> bytes:
    result = image.copy()
    x, y, w, h = region.bbox
    width, height = image.size
    ImageDraw.Draw(result).rectangle((x*width, y*height, min(width-1, (x+w)*width),
                                     min(height-1, (y+h)*height)), outline="red", width=max(2, width//300))
    return jpeg(result)


def evidence_context(image: Image.Image, region: Region) -> Image.Image:
    """Show the exact evidence rectangle plus nearby pixels to detect clipping."""
    x, y, w, h = region.bbox
    width, height = image.size
    pad_x, pad_y = max(w * .2, .06), max(h * .2, .06)
    left, top = max(0, math.floor((x-pad_x)*width)), max(0, math.floor((y-pad_y)*height))
    right, bottom = min(width, math.ceil((x+w+pad_x)*width)), min(height, math.ceil((y+h+pad_y)*height))
    result = image.crop((left, top, right, bottom))
    ImageDraw.Draw(result).rectangle((x*width-left, y*height-top,
                                    min(right-left-1, (x+w)*width-left),
                                    min(bottom-top-1, (y+h)*height-top)),
                                   outline="red", width=max(2, width//400))
    return result


def identity(asset: Asset, image: Image.Image) -> dict:
    return {"asset_id": asset.asset_id, "kind": asset.kind, "page": asset.page,
            "sha256": hashlib.sha256(base64.b64decode(asset.data_base64)).hexdigest(),
            "width": image.width, "height": image.height,
            "coordinate_system": "rendered selected PDF page" if asset.mime_type == "application/pdf" else "EXIF-oriented image",
            "bbox_format": "normalized top-left x,y,width,height",
            "pdf_render_max_edge": 2400 if asset.mime_type == "application/pdf" else None}
