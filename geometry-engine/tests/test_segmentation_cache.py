"""Cache correctness across new runs, partial inference failure and stale leases."""
import hashlib
import json
from pathlib import Path
from io import BytesIO
import tempfile
import unittest
from uuid import uuid4
import zipfile

import numpy as np
from PIL import Image

from geometry_engine.segmentation.cache import (
    CachedSamBackend, CacheBusyError, CacheLeaseLostError, decode_detections,
)
from geometry_engine.segmentation.sam_backend import Detection


class MemoryIndex:
    def __init__(self):
        self.ready, self.leases, self.lookups = {}, {}, []
        self.lose_on_publish = False

    def lookup(self, keys):
        self.lookups.append(list(keys))
        return {key: dict(self.ready[key]) for key in keys if key in self.ready}

    def claim(self, key, token, seconds):
        if key in self.ready or key in self.leases:
            return False
        self.leases[key] = token
        return True

    def heartbeat(self, key, token, seconds):
        return self.leases.get(key) == token

    def publish(self, key, token, object_key, digest, size):
        if self.lose_on_publish or self.leases.get(key) != token:
            return False
        self.ready[key] = {"artifact_key": object_key, "artifact_sha256": digest, "artifact_size": size}
        del self.leases[key]
        return True

    def invalidate(self, key, digest):
        if self.ready.get(key, {}).get("artifact_sha256") == digest:
            del self.ready[key]

    def release(self, key, token):
        if self.leases.get(key) == token:
            del self.leases[key]


class MemoryStorage:
    def __init__(self):
        self.blobs, self.downloads = {}, []

    def upload(self, key, data, content_type):
        if key in self.blobs:
            raise FileExistsError(key)
        self.blobs[key] = data

    def download(self, key):
        self.downloads.append(key)
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]


class CountingBackend:
    model = "fixture-model"
    model_info = {"name": model, "version": "fixture-v1"}

    def __init__(self):
        self.calls = []
        self.fail_prompt = None
        self.empty = False
        self.invalid = False

    def predict(self, image, prompts):
        self.calls.append((image.copy(), prompts))
        if self.fail_prompt in prompts:
            raise RuntimeError("Provider failed")
        if self.empty:
            return []
        mask = np.asarray(image)[:, :, 0] > 30
        return [Detection(prompt, .4, mask if not self.invalid else mask.astype(np.uint8), "object-1")
                for prompt in prompts]


class SegmentationCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repository, self.storage = MemoryIndex(), MemoryStorage()
        self.image = Image.fromarray(np.arange(180, dtype=np.uint8).reshape(6, 10, 3))

    def cache(self, backend=None, **kwargs):
        backend = backend or CountingBackend()
        cache = CachedSamBackend(backend, self.repository, self.storage,
                                 self.directory / uuid4().hex, **kwargs)
        return cache, backend

    def test_fresh_worker_batch_reuses_all_masks_without_inference(self):
        images = [self.image, self.image.transpose(Image.Transpose.ROTATE_90)]
        first, provider = self.cache()
        first.prepare(iter(images), ("wall", "window"))
        original = [first.predict(image, ("wall", "window")) for image in images]
        self.assertEqual(len(provider.calls), 4)
        self.assertEqual(len(self.repository.lookups), 1)
        second, provider2 = self.cache()
        second.prepare(iter(images), ("wall", "window"))
        restored = [second.predict(image, ("wall", "window")) for image in images]
        self.assertEqual(len(self.repository.lookups), 2)
        self.assertEqual(provider2.calls, [])
        self.assertEqual(second.stats["hits"], 4)
        for source, output in zip(original, restored):
            for a, b in zip(source, output):
                np.testing.assert_array_equal(a.mask, b.mask)
                self.assertEqual((a.label, a.score, a.object_id), (b.label, b.score, b.object_id))

    def test_new_image_and_prompt_only_request_missing_pairs(self):
        first, _ = self.cache()
        first.prepare([self.image], ("wall",))
        first.predict(self.image, ("wall",))
        second, provider = self.cache()
        extra = Image.new("RGB", (10, 6), "white")
        second.prepare([self.image, extra], ("wall", "door"))
        second.predict(self.image, ("wall", "door"))
        second.predict(extra, ("wall", "door"))
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(provider.calls[0][1], ("door",))

    def test_completed_prompt_survives_failure_and_raw_low_score_masks_are_retained(self):
        cache, provider = self.cache()
        provider.fail_prompt = "door"
        cache.prepare([self.image], ("wall", "door"))
        with self.assertRaisesRegex(RuntimeError, "Provider failed"):
            cache.predict(self.image, ("wall", "door"))
        self.assertEqual(len(self.repository.ready), 1)
        self.assertEqual(self.repository.leases, {})
        retry, healthy = self.cache()
        retry.prepare([self.image], ("wall", "door"))
        result = retry.predict(self.image, ("wall", "door"))
        self.assertEqual([call[1] for call in healthy.calls], [("door",)])
        self.assertEqual(result[0].score, .4)

    def test_valid_empty_result_is_cached_but_invalid_mask_is_not(self):
        cache, provider = self.cache()
        provider.empty = True
        cache.prepare([self.image], ("window",))
        self.assertEqual(cache.predict(self.image, ("window",)), [])
        second, provider2 = self.cache()
        second.prepare([self.image], ("window",))
        self.assertEqual(second.predict(self.image, ("window",)), [])
        self.assertEqual(provider2.calls, [])
        provider2.invalid = True
        with self.assertRaises(ValueError):
            second.predict(self.image, ("door",))
        self.assertEqual(len(self.repository.ready), 1)

    def test_epoch_model_and_rotation_invalidate_request_but_exif_does_not(self):
        cache, backend = self.cache()
        baseline = next(cache.requests(self.image, ("wall",)))[0]
        image = self.image.copy()
        exif = Image.Exif()
        exif[274] = 6
        image.info["exif"] = exif.tobytes()
        self.assertEqual(next(cache.requests(image, ("wall",)))[0], baseline)
        epoch, _ = self.cache(cache_epoch="new-alias-revision")
        self.assertNotEqual(next(epoch.requests(image, ("wall",)))[0], baseline)
        self.assertNotEqual(cache.model_info, epoch.model_info)
        backend.cache_identity = {"name": "new-model"}
        self.assertNotEqual(next(cache.requests(image, ("wall",)))[0], baseline)
        clean, _ = self.cache()
        self.assertNotEqual(next(clean.requests(image.transpose(Image.Transpose.ROTATE_90), ("wall",)))[0], baseline)

    def test_corrupt_or_missing_blob_is_invalidated_and_rebuilt(self):
        for corrupt in (True, False):
            with self.subTest(corrupt=corrupt):
                first, _ = self.cache()
                first.prepare([self.image], ("wall",))
                first.predict(self.image, ("wall",))
                entry = next(iter(self.repository.ready.values()))
                if corrupt:
                    self.storage.blobs[entry["artifact_key"]] = b"broken"
                else:
                    del self.storage.blobs[entry["artifact_key"]]
                retry, provider = self.cache()
                retry.prepare([self.image], ("wall",))
                retry.predict(self.image, ("wall",))
                self.assertEqual(len(provider.calls), 1)

    def test_busy_or_lost_lease_does_not_publish_or_duplicate_inference(self):
        cache, provider = self.cache()
        key, _ = next(cache.requests(self.image, ("wall",)))
        self.repository.leases[key] = uuid4()
        with self.assertRaises(CacheBusyError):
            cache.predict(self.image, ("wall",))
        self.assertEqual(provider.calls, [])
        self.repository.leases.clear()
        self.repository.lose_on_publish = True
        with self.assertRaises(CacheLeaseLostError):
            cache.predict(self.image, ("wall",))
        self.assertEqual(self.repository.ready, {})

    def test_cached_masks_reject_request_mismatch(self):
        cache, _ = self.cache()
        key, request = next(cache.requests(self.image, ("wall",)))
        cache.predict(self.image, ("wall",))
        blob = self.storage.blobs[self.repository.ready[key]["artifact_key"]]
        self.assertEqual(hashlib.sha256(blob).hexdigest(), self.repository.ready[key]["artifact_sha256"])
        with self.assertRaises(ValueError):
            decode_detections(blob, dict(request, prompt="door"))

    def test_service_preserves_native_mask_orientation_and_refilters_without_inference(self):
        from test_segmentation import SegmentationTests
        from geometry_engine.segmentation.service import SegmentationService, SegmentationSettings
        fixture = SegmentationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        metadata_path = fixture.original / "frames/000001/metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["pose"]["T_world_from_camera"] = [[0, -1, 0, 0], [1, 0, 0, 0],
                                                  [0, 0, 1, 0], [0, 0, 0, 1]]
        metadata_path.write_text(json.dumps(metadata))
        cache, first_provider = self.cache()
        report = SegmentationService(cache, SegmentationSettings(prompts=("wall",),
            min_confidence=.5, min_mask_pixels=1)).segment(fixture.session)
        self.assertEqual(report["detection_count"], 0)
        self.assertEqual(len(first_provider.calls), 1)
        second, provider = self.cache()
        report = SegmentationService(second, SegmentationSettings(prompts=("wall",),
            min_confidence=.3, min_mask_pixels=1)).segment(fixture.session, fixture.session / "segmentation_2")
        self.assertEqual(report["detection_count"], 1)
        self.assertEqual(report["inference_cache"]["hits"], 1)
        self.assertEqual(provider.calls, [])
        data = json.loads((fixture.session / "segmentation_2/detections.json").read_text())
        mask_path = fixture.session / "segmentation_2" / data["frames"][0]["detections"][0]["mask"]
        with Image.open(mask_path) as mask:
            np.testing.assert_array_equal(np.asarray(mask) != 0, fixture.pixels[:, :, 0] > 30)

    def test_failed_storage_verification_does_not_publish_cache_entry(self):
        cache, _ = self.cache()
        self.storage.download = lambda key: b"truncated"
        with self.assertRaisesRegex(IOError, "checksum"):
            cache.predict(self.image, ("wall",))
        self.assertEqual(self.repository.ready, {})
        self.assertEqual(self.repository.leases, {})

    def test_transport_failure_reading_cache_does_not_trigger_paid_inference(self):
        first, _ = self.cache()
        first.prepare([self.image], ("wall",))
        first.predict(self.image, ("wall",))
        def unavailable(key):
            raise TimeoutError("storage unavailable")
        self.storage.download = unavailable
        second, provider = self.cache()
        with self.assertRaises(TimeoutError):
            second.prepare([self.image], ("wall",))
        self.assertEqual(provider.calls, [])
        self.assertEqual(len(self.repository.ready), 1)

    def test_array_header_cannot_allocate_more_than_the_archive_contains(self):
        cache, _ = self.cache()
        _, request = next(cache.requests(self.image, ("wall",)))
        header = BytesIO()
        np.lib.format.write_array_header_1_0(header, {
            "descr": "|u1", "fortran_order": False, "shape": (10**12,)})
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("metadata.npy", header.getvalue())
        with self.assertRaisesRegex(ValueError, "array shape"):
            decode_detections(buffer.getvalue(), request)

    def test_stage_fingerprint_changes_with_epoch_and_effective_backend_revision(self):
        from geometry_engine.inspection import stage_configuration
        from geometry_engine.segmentation.service import SegmentationService
        from geometry_engine_api.services.artifacts import fingerprint
        first, backend = self.cache()
        next_epoch, _ = self.cache(cache_epoch="2")
        initial = fingerprint(stage_configuration("segmentation", segmentation_service=SegmentationService(first)))
        changed = fingerprint(stage_configuration("segmentation", segmentation_service=SegmentationService(next_epoch)))
        self.assertNotEqual(initial, changed)
        backend.cache_identity = {"name": "fixture-model", "adapter_version": "2"}
        changed_adapter = fingerprint(stage_configuration("segmentation", segmentation_service=SegmentationService(first)))
        self.assertNotEqual(initial, changed_adapter)


if __name__ == "__main__":
    unittest.main()
