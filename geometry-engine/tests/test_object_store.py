"""Bounded Storage downloads without live network calls or private SDK state."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from geometry_engine_api.services.artifacts import ArtifactError, ObjectStoreError, SupabaseObjectStore, safe_relative


class Body(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.reads, self.closed = chunks, 0, False

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


class Bucket:
    def __init__(self):
        self.signed = []

    def create_signed_url(self, key, seconds):
        self.signed.append((key, seconds))
        return {"signedURL": "https://storage.example.test/object/file?token=private-token"}

    def download(self, key):
        raise AssertionError("The buffering SDK download must never be used")


class ObjectStoreTests(unittest.TestCase):
    def store(self, handler, **kwargs):
        self.bucket = Bucket()
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        sdk = SimpleNamespace(storage=SimpleNamespace(from_=lambda name: self.bucket))
        return SupabaseObjectStore(sdk, "bucket", attempts=1, http_client=client, **kwargs), client

    def test_streams_and_verifies_length_without_closing_injected_client(self):
        body = Body([b"one", b"two"])
        def handler(request):
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            return httpx.Response(200, headers={"Content-Length": "6"}, stream=body)
        store, client = self.store(handler)
        self.assertEqual(store.download("session/result", max_bytes=6), b"onetwo")
        self.assertTrue(body.closed)
        self.assertFalse(client.is_closed)
        self.assertEqual(len(self.bucket.signed), 1)

    def test_large_declared_body_is_rejected_before_consuming_it(self):
        body = Body([b"never-read"])
        store, _ = self.store(lambda request: httpx.Response(200, stream=body,
            headers={"Content-Length": "70000000"}))
        with self.assertRaisesRegex(ArtifactError, "size limit"):
            store.download("session/result")
        self.assertEqual(body.reads, 0)
        self.assertTrue(body.closed)

    def test_missing_length_cannot_bypass_streaming_limit(self):
        body = Body([b"x" * 65536, b"y" * 65536, b"unread"])
        store, _ = self.store(lambda request: httpx.Response(200, stream=body))
        with self.assertRaisesRegex(ArtifactError, "size limit"):
            store.download("session/result", max_bytes=65536)
        self.assertEqual(body.reads, 2)
        self.assertTrue(body.closed)

    def test_redirects_and_compression_are_not_followed_or_expanded(self):
        requests = []
        def redirect(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://elsewhere.test/private"})
        store, _ = self.store(redirect)
        with self.assertRaises(ObjectStoreError) as caught:
            store.download("session/result")
        self.assertEqual(caught.exception.code, "storage_http_302")
        self.assertNotIn("private-token", str(caught.exception))
        self.assertEqual(len(requests), 1)
        body = Body([b"compressed"])
        store, _ = self.store(lambda request: httpx.Response(200, stream=body,
            headers={"Content-Encoding": "gzip"}))
        with self.assertRaisesRegex(ArtifactError, "compression"):
            store.download("session/result")
        self.assertEqual(body.reads, 0)
        self.assertTrue(body.closed)

    def test_incomplete_response_is_retryable_and_client_is_closed_if_owned(self):
        body = Body([b"short"])
        owned = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, stream=body, headers={"Content-Length": "12"})))
        sdk = SimpleNamespace(storage=SimpleNamespace(from_=lambda name: Bucket()))
        store = SupabaseObjectStore(sdk, "bucket", attempts=1)
        with patch("geometry_engine_api.services.artifacts.httpx.Client", return_value=owned):
            with self.assertRaises(ObjectStoreError) as caught:
                store.download("session/result")
        self.assertTrue(caught.exception.retryable)
        self.assertTrue(owned.is_closed)
        self.assertTrue(body.closed)

    def test_transport_retry_recreates_signed_url_and_never_returns_partial_bytes(self):
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("private signed URL must not leak")
            return httpx.Response(200, content=b"complete")
        store, _ = self.store(handler)
        store.attempts = 2
        with patch("geometry_engine_api.services.artifacts.time.sleep"):
            self.assertEqual(store.download("session/result"), b"complete")
        self.assertEqual(len(self.bucket.signed), 2)

    def test_windows_devices_invalid_names_and_alternate_streams_are_rejected(self):
        for name in ("NUL", "folder/CON.txt", "com1.png", "COM1 .txt", "LPT9", "CONIN$", "COM\u00b9.txt",
                     "file:stream", "bad<name", 'bad"name', "bad|name", "bad*name", "trailing. "):
            with self.subTest(name=name), self.assertRaises(ArtifactError):
                safe_relative(name)
        self.assertEqual(safe_relative("session/results/window frame.png"), "session/results/window frame.png")


if __name__ == "__main__":
    unittest.main()
