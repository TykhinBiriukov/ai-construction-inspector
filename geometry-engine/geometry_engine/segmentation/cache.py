"""Content-addressed SAM inference cache, independent of scenes and camera poses.

The repository stores a small index in PostgreSQL. Each immutable Storage blob
contains validated, unfiltered masks in the pixels actually sent to the model.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
import threading
from uuid import uuid4
import zipfile

import numpy as np
from PIL import Image

from .sam_backend import Detection


CACHE_VERSION = "sam-inference-v1"
PREPROCESSING_VERSION = "rgb-no-exif-upright-v1"
MAX_BLOB_BYTES = 64 * 1024 * 1024
MAX_DECODED_PIXELS = 256 * 1024 * 1024


class CacheBusyError(RuntimeError):
    """Another live worker owns this inference; retry without issuing a duplicate."""
    retryable = True


class CacheLeaseLostError(RuntimeError):
    retryable = True


def image_png(image):
    clean = Image.fromarray(np.asarray(image.convert("RGB")))
    buffer = BytesIO()
    clean.save(buffer, format="PNG")
    return buffer.getvalue()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validate(detections, size, prompt):
    width, height = size
    if not 0 < width * height <= 25_000_000 or len(detections) > 512:
        raise ValueError("Invalid inference cache dimensions or detection count")
    if width * height * len(detections) > MAX_DECODED_PIXELS:
        raise ValueError("Inference cache decoded masks exceed size limit")
    for detection in detections:
        mask = np.asarray(detection.mask)
        if (mask.shape != (height, width) or mask.dtype != np.bool_
                or detection.label != prompt or not np.isfinite(detection.score)
                or not 0 <= detection.score <= 1):
            raise ValueError("Invalid model detection; nothing was cached")


def encode_detections(detections, request):
    size = tuple(request["size"])
    _validate(detections, size, request["prompt"])
    records, arrays = [], {}
    for index, item in enumerate(detections):
        records.append({"label": item.label, "score": float(item.score),
                        "object_id": str(item.object_id) if item.object_id is not None else None})
        arrays[f"mask_{index}"] = np.packbits(item.mask.reshape(-1))
    metadata = _canonical({"request": request, "detections": records})
    arrays["metadata"] = np.frombuffer(metadata, dtype=np.uint8)
    output = BytesIO()
    np.savez_compressed(output, **arrays)
    blob = output.getvalue()
    if len(blob) > MAX_BLOB_BYTES:
        raise ValueError("Inference cache artifact exceeds size limit")
    return blob


def decode_detections(blob, request):
    if len(blob) > MAX_BLOB_BYTES:
        raise ValueError("Inference cache artifact exceeds size limit")
    # Inspect ZIP sizes before numpy allocation; never deserialize pickle.
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        entries = archive.infolist()
        if (len(entries) > 513 or sum(row.file_size for row in entries) > MAX_BLOB_BYTES
                or any(row.file_size > MAX_BLOB_BYTES for row in entries)
                or len({row.filename for row in entries}) != len(entries)):
            raise ValueError("Invalid inference cache archive")
        for entry in entries:
            with archive.open(entry) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError("Unsupported inference cache array encoding")
                if (dtype != np.dtype(np.uint8) or len(shape) != 1 or shape[0] < 0
                        or shape[0] > entry.file_size or shape[0] > MAX_BLOB_BYTES):
                    raise ValueError("Invalid inference cache array shape")
    with np.load(BytesIO(blob), allow_pickle=False) as arrays:
        metadata = arrays["metadata"]
        if metadata.dtype != np.uint8 or metadata.ndim != 1 or metadata.size > 1_000_000:
            raise ValueError("Invalid inference cache metadata")
        document = json.loads(metadata.tobytes())
        if document["request"] != request:
            raise ValueError("Inference cache request mismatch")
        rows = document["detections"]
        width, height = request["size"]
        if not isinstance(rows, list) or len(rows) > 512 or width * height * len(rows) > MAX_DECODED_PIXELS:
            raise ValueError("Invalid inference cache detection count")
        if set(arrays.files) != {"metadata", *[f"mask_{i}" for i in range(len(rows))]}:
            raise ValueError("Invalid inference cache mask list")
        result = []
        for index, row in enumerate(rows):
            packed = arrays[f"mask_{index}"]
            if packed.dtype != np.uint8 or packed.shape != ((width * height + 7) // 8,):
                raise ValueError("Invalid inference cache mask encoding")
            mask = np.unpackbits(packed, count=width * height).reshape(height, width).astype(bool)
            result.append(Detection(row["label"], float(row["score"]), mask, row["object_id"]))
    _validate(result, (width, height), request["prompt"])
    return result


class CachedSamBackend:
    """Wrap a SAM backend with batch lookup and fenced, per-prompt persistence.

    repository: lookup(keys), claim(key, token, seconds), heartbeat(...),
      publish(key, token, object_key, sha256, size), invalidate(key, sha256), release(...).
    blob_store: download(key)->bytes; upload(key, bytes, content_type)->None.
    """
    def __init__(self, backend, repository, blob_store, local_cache_dir, *, cache_epoch="1",
                 lease_seconds=900, download_workers=4):
        if not isinstance(cache_epoch, str) or not cache_epoch or len(cache_epoch) > 128:
            raise ValueError("SAM cache epoch must be a nonempty short string")
        if lease_seconds < 30 or not 1 <= download_workers <= 16:
            raise ValueError("Invalid cache lease or download concurrency")
        self.backend, self.repository, self.blob_store = backend, repository, blob_store
        self.local = Path(local_cache_dir).resolve()
        self.local.mkdir(parents=True, exist_ok=True)
        self.cache_epoch, self.lease_seconds = cache_epoch, lease_seconds
        self.download_workers = download_workers
        self.model = getattr(backend, "model", None)
        self._entries = {}
        self._requests = {}
        self.stats = {"hits": 0, "misses": 0, "prefetched": 0, "invalidated": 0}

    @property
    def model_info(self):
        base = getattr(self.backend, "model_info", {"name": self.model})
        identity = getattr(self.backend, "cache_identity", base)
        return {**base, "inference_cache": {"schema": CACHE_VERSION, "epoch": self.cache_epoch,
                "preprocessing": PREPROCESSING_VERSION, "backend": identity}}

    def requests(self, image, prompts):
        identity = getattr(self.backend, "cache_identity", getattr(self.backend, "model_info", {"name": self.model}))
        if not 0 < image.width * image.height <= 25_000_000:
            raise ValueError("Invalid inference cache image dimensions")
        image_hash = hashlib.sha256(image_png(image)).hexdigest()
        for prompt in prompts:
            request = {"schema": CACHE_VERSION, "epoch": self.cache_epoch,
                       "preprocessing": PREPROCESSING_VERSION, "model": identity,
                       "size": list(image.size), "image_sha256": image_hash, "prompt": prompt}
            yield hashlib.sha256(_canonical(request)).hexdigest(), request

    def prepare(self, images, prompts):
        """images is a lazy iterable: only one full RGB image is held at a time."""
        self.stats = {"hits": 0, "misses": 0, "prefetched": 0, "invalidated": 0}
        self._requests = dict(pair for image in images for pair in self.requests(image, prompts))
        self._entries = self.repository.lookup(list(self._requests))
        if self._entries:
            with ThreadPoolExecutor(max_workers=self.download_workers) as pool:
                # Fetch immutable blobs concurrently; validate before trusting any mask.
                list(pool.map(self._prefetch, list(self._entries.items())))

    def _prefetch(self, pair):
        key, entry = pair
        try:
            try:
                blob = self.blob_store.download(entry["artifact_key"])
            except Exception as error:
                if getattr(error, "code", None) == "storage_http_404":
                    raise FileNotFoundError("Cached SAM artifact is missing") from None
                raise
            if (len(blob) != entry["artifact_size"]
                    or hashlib.sha256(blob).hexdigest() != entry["artifact_sha256"]):
                raise ValueError("Inference cache artifact checksum mismatch")
            decode_detections(blob, self._requests[key])
            (self.local / f"{key}.npz").write_bytes(blob)
            self.stats["prefetched"] += 1
        except (ValueError, KeyError, TypeError, OSError, EOFError, OverflowError, zipfile.BadZipFile) as error:
            # Missing/corrupt cache entries can be rebuilt; transport/auth errors
            # must escape instead of silently creating more paid inference calls.
            if isinstance(error, OSError) and not isinstance(error, FileNotFoundError):
                raise
            self.repository.invalidate(key, entry["artifact_sha256"])
            self._entries.pop(key, None)
            self.stats["invalidated"] += 1

    def predict(self, image, prompts):
        result = []
        for key, request in self.requests(image, prompts):
            self._requests[key] = request
            if key in self._entries:
                result.extend(decode_detections((self.local / f"{key}.npz").read_bytes(), request))
                self.stats["hits"] += 1
                continue
            token = uuid4()
            if not self.repository.claim(key, token, self.lease_seconds):
                ready = self.repository.lookup([key])
                if key in ready:
                    self._entries[key] = ready[key]
                    self._prefetch((key, ready[key]))
                    if key in self._entries:
                        result.extend(decode_detections((self.local / f"{key}.npz").read_bytes(), request))
                        self.stats["hits"] += 1
                        continue
                raise CacheBusyError("SAM inference is already owned by another worker; retry later")
            stopped, lost = threading.Event(), threading.Event()
            def heartbeat():
                while not stopped.wait(self.lease_seconds / 3):
                    try:
                        if not self.repository.heartbeat(key, token, self.lease_seconds):
                            lost.set()
                            return
                    except Exception:
                        lost.set()
                        return
            thread = threading.Thread(target=heartbeat, name="sam-cache-heartbeat", daemon=True)
            thread.start()
            try:
                detections = self.backend.predict(image, (request["prompt"],))
                blob = encode_detections(detections, request)
                digest = hashlib.sha256(blob).hexdigest()
                object_key = f"cache/sam/v1/{key}/{token.hex}/{digest}.npz"
                if lost.is_set():
                    raise CacheLeaseLostError("SAM cache lease was lost before publication")
                self.blob_store.upload(object_key, blob, "application/octet-stream")
                uploaded = self.blob_store.download(object_key)
                if len(uploaded) != len(blob) or hashlib.sha256(uploaded).hexdigest() != digest:
                    raise IOError("Uploaded SAM cache artifact failed checksum verification")
                if lost.is_set() or not self.repository.publish(key, token, object_key, digest, len(blob)):
                    raise CacheLeaseLostError("SAM cache lease was lost during publication")
                (self.local / f"{key}.npz").write_bytes(blob)
                self._entries[key] = {"artifact_key": object_key, "artifact_sha256": digest,
                                      "artifact_size": len(blob)}
                self.stats["misses"] += 1
                result.extend(detections)
            finally:
                stopped.set()
                thread.join(timeout=5)
                # Token fencing means a stale worker cannot remove its successor.
                self.repository.release(key, token)
        return result
