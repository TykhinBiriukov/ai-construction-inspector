"""Immutable reference BIM objects; local files are only disposable caches."""
import hashlib
import os
from uuid import uuid4

from .errors import APIError
from .storage import LocalBIMStore
from .supabase_captures import SupabaseCaptureSourceStore


class SupabaseBIMStore(LocalBIMStore):
    def __init__(self, settings, client=None):
        super().__init__(settings.data_dir, settings.max_bim_bytes)
        self.settings = settings
        self.remote = SupabaseCaptureSourceStore(settings, client)

    def key(self, digest):
        LocalBIMStore.path_for(self, digest)  # Validate before constructing a remote key.
        return f'inspector-models/{self.settings.project_id}/{digest}'

    def publish(self, model):
        with self.remote.connection() as client, model.path.open('rb') as source:
            response = client.post(self.remote._url('object', self.key(model.sha256)),
                                   headers={**self.remote._headers(), 'Content-Type': 'application/octet-stream',
                                            'x-upsert': 'false'}, content=source)
            if response.status_code not in (200, 201, 409):
                # Storage sometimes reports duplicate objects as HTTP 400.
                try:
                    duplicate = response.json().get('error') == 'Duplicate' or response.json().get('code') == 'Duplicate'
                except ValueError:
                    duplicate = False
                if not duplicate:
                    raise APIError(503, 'model_storage_unavailable', 'BIM upload was not confirmed', retryable=True)
        # Read back and hash even after a duplicate response; never trust an unrelated object.
        self._download(model.sha256, model.byte_length)

    def _download(self, digest, expected_length=None):
        path = LocalBIMStore.path_for(self, digest)
        temporary = self.staging_dir / f'{uuid4()}.download'
        checksum, size = hashlib.sha256(), 0
        try:
            with self.remote.connection() as client, client.stream('GET', self.remote._url('object', self.key(digest)),
                                                                  headers=self.remote._headers()) as response:
                if response.status_code != 200:
                    raise APIError(503, 'model_unavailable', 'Stored BIM model is temporarily unavailable', retryable=True)
                with temporary.open('xb') as target:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise APIError(503, 'model_integrity_error', 'Stored BIM model exceeds the size limit')
                        checksum.update(chunk)
                        target.write(chunk)
            if checksum.hexdigest() != digest or (expected_length is not None and size != expected_length):
                raise APIError(503, 'model_integrity_error', 'Stored BIM model failed verification')
            os.replace(temporary, path)
            return path
        finally:
            temporary.unlink(missing_ok=True)

    def path_for(self, digest):
        path = LocalBIMStore.path_for(self, digest)
        if path.is_file():
            with path.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() == digest:
                    return path
        return self._download(digest)
