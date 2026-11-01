"""Immutable, checked stage artifacts in Storage; local files during computation.

Small files are bundled, large bundles are split into bounded Storage objects.
Previously published bytes are referenced instead of uploaded again.
"""
from dataclasses import dataclass
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
from tempfile import TemporaryDirectory
import time
import threading
from uuid import uuid4
import zipfile
from urllib.parse import urlsplit

import httpx
from storage3.exceptions import StorageApiError


CHUNK_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_RESTORE_BYTES = 30 * 1024**3
MAX_OBJECT_BYTES = 64 * 1024**2
WINDOWS_RESERVED_NAMES = ({'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
                          | {f'{prefix}{number}' for prefix in ('COM', 'LPT')
                             for number in ('1', '2', '3', '4', '5', '6', '7', '8', '9', '\u00b9', '\u00b2', '\u00b3')})
_BUNDLE_CACHE_LOCK = threading.Lock()
_MANIFEST_CACHE_LOCK = threading.Lock()


class ArtifactError(ValueError):
    """Stored or generated artifacts do not match their committed fingerprints."""


class ObjectStoreError(RuntimeError):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def safe_relative(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value) or any(c in value for c in ':?#<>"|*')
            or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute()
            or any(p in ('', '.', '..') or p.endswith(('.', ' '))
                   or p.split('.')[0].rstrip(' ').upper() in WINDOWS_RESERVED_NAMES for p in value.split('/'))):
        raise ArtifactError('Invalid artifact path')
    return value


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def fingerprint(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


class SupabaseObjectStore:
    def __init__(self, client, bucket, attempts=3, *, http_client=None,
                 max_object_bytes=MAX_OBJECT_BYTES, download_deadline_s=120):
        if type(attempts) is not int or not 1 <= attempts <= 5:
            raise ValueError('Storage attempts must be between 1 and 5')
        if type(max_object_bytes) is not int or not 1 <= max_object_bytes <= MAX_OBJECT_BYTES:
            raise ValueError('Storage object limit must be between 1 byte and 64 MiB')
        if not 0 < download_deadline_s <= 600:
            raise ValueError('Storage download deadline must be between 0 and 600 seconds')
        self.bucket = client.storage.from_(bucket)
        self.attempts = attempts
        self.http_client = http_client
        self.max_object_bytes = max_object_bytes
        self.download_deadline_s = download_deadline_s

    def _call(self, operation):
        for attempt in range(self.attempts):
            try:
                return operation()
            except (httpx.HTTPError, StorageApiError) as error:
                status = getattr(error, 'status', None) or getattr(error, 'status_code', None)
                if status is None and isinstance(error, httpx.HTTPStatusError):
                    status = error.response.status_code
                try:
                    status = int(status)
                except (TypeError, ValueError):
                    status = 0
                transient = not status or status in (408, 425, 429) or status >= 500
                if transient and attempt + 1 < self.attempts:
                    time.sleep(min(2**attempt, 4))
                    continue
                raise ObjectStoreError(f'storage_http_{status or "transport"}', retryable=transient) from None

    def download(self, key, *, max_bytes=None):
        """Bound memory before reading bytes; never call the SDK's buffering download.

        The caller owns an injected HTTP client. Otherwise a short-lived client is
        always closed here, including oversized/failed/redirected responses.
        """
        safe_relative(key)
        limit = self.max_object_bytes if max_bytes is None else max_bytes
        if type(limit) is not int or not 0 <= limit <= self.max_object_bytes:
            raise ValueError('Invalid Storage download byte limit')

        def operation():
            signed = self.bucket.create_signed_url(key, int(self.download_deadline_s) + 60)
            url = signed.get('signedURL') or signed.get('signedUrl')
            if not isinstance(url, str) or len(url) > 8192:
                raise ArtifactError('Storage returned an invalid signed URL')
            parsed = urlsplit(url)
            if (parsed.scheme not in ('https', 'http') or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                raise ArtifactError('Storage returned an invalid signed URL')
            context = (nullcontext(self.http_client) if self.http_client is not None else
                       httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False,
                                    follow_redirects=False))
            started = time.monotonic()
            with context as client:
                with client.stream('GET', url, headers={'Accept-Encoding': 'identity'},
                                   follow_redirects=False, timeout=httpx.Timeout(30, connect=10)) as response:
                    response.raise_for_status()
                    if response.headers.get('content-encoding', '').strip().lower() not in ('', 'identity'):
                        raise ArtifactError('Unexpected compression in Storage response')
                    declared = response.headers.get('content-length')
                    if declared is not None:
                        try:
                            declared = int(declared)
                        except (ValueError, TypeError):
                            raise ArtifactError('Invalid Storage content length') from None
                        if not 0 <= declared <= limit:
                            raise ArtifactError('Storage object exceeds its download size limit')
                    content = bytearray()
                    for block in response.iter_bytes(chunk_size=65536):
                        if len(content) + len(block) > limit:
                            raise ArtifactError('Storage object exceeds its download size limit')
                        if time.monotonic() - started > self.download_deadline_s:
                            raise httpx.ReadTimeout('Storage download deadline exceeded')
                        content.extend(block)
                    if declared is not None and len(content) != declared:
                        raise httpx.ReadError('Incomplete Storage response')
                    return bytes(content)

        return self._call(operation)

    def upload(self, key, data, content_type='application/octet-stream'):
        safe_relative(key)
        try:
            self._call(lambda: self.bucket.upload(key, data, {
                'content-type': content_type, 'upsert': 'false', 'cache-control': '31536000'}))
        except ObjectStoreError as error:
            if error.code not in ('storage_http_409', 'storage_http_400'):
                raise
            # An earlier response may have been lost after a successful upload.
            if self.download(key, max_bytes=len(data)) != data:
                raise ArtifactError('Immutable object collision') from None


@dataclass(frozen=True)
class ArtifactRef:
    key: str
    sha256: str


class ArtifactStore:
    def __init__(self, objects, chunk_bytes=CHUNK_BYTES, *, cache_dir=None):
        if chunk_bytes < 1:
            raise ValueError('Chunk size must be positive')
        self.objects, self.chunk_bytes = objects, chunk_bytes
        self.cache_dir = Path(cache_dir).resolve() if cache_dir is not None else None

    def _cache_path(self, ref, kind):
        if not isinstance(ref.sha256, str) or len(ref.sha256) != 64 or any(c not in '0123456789abcdef' for c in ref.sha256):
            raise ArtifactError('Invalid manifest fingerprint')
        return self.cache_dir / kind / ref.sha256

    def put_json(self, prefix, value):
        data = json_bytes(value)
        digest = hashlib.sha256(data).hexdigest()
        key = safe_relative(f'{prefix}/{digest}.json')
        self.objects.upload(key, data, 'application/json')
        if self.objects.download(key) != data:
            raise ArtifactError('Published manifest verification failed')
        return ArtifactRef(key, digest)

    def read_json(self, ref):
        cached = self._cache_path(ref, 'manifests') if self.cache_dir is not None else None
        data = None
        if cached is not None and cached.is_file() and cached.stat().st_size <= MAX_MANIFEST_BYTES:
            candidate = cached.read_bytes()
            if hashlib.sha256(candidate).hexdigest() == ref.sha256:
                data = candidate
        if data is None:
            data = self.objects.download(ref.key)
        if len(data) > MAX_MANIFEST_BYTES or hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ArtifactError('Artifact manifest fingerprint mismatch')
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError):
            raise ArtifactError('Invalid artifact manifest JSON') from None
        if not isinstance(result, dict):
            raise ArtifactError('Invalid artifact manifest object')
        if cached is not None and data is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            with _MANIFEST_CACHE_LOCK:
                if not cached.is_file() or sha_file(cached) != ref.sha256:
                    temporary = cached.with_name(cached.name + '-' + uuid4().hex + '.tmp')
                    try:
                        temporary.write_bytes(data)
                        temporary.replace(cached)
                    finally:
                        temporary.unlink(missing_ok=True)
        return result

    def _cached_bundle(self, ref):
        """Cache complete verified bundles so individual image GETs reuse bytes."""
        manifest = self.read_json(ref)
        path = self._cache_path(ref, 'bundles')
        with _BUNDLE_CACHE_LOCK:
            if (path.is_file() and path.stat().st_size == manifest.get('size')
                    and sha_file(path) == manifest.get('sha256')):
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '-' + uuid4().hex + '.tmp')
            try:
                self._download_bundle(ref, temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def _upload_chunks(self, path, prefix):
        chunks = []
        with Path(path).open('rb') as stream:
            while data := stream.read(self.chunk_bytes):
                digest = hashlib.sha256(data).hexdigest()
                key = f'{prefix}/chunks/{digest}.bin'
                self.objects.upload(key, data, 'application/octet-stream')
                check = self.objects.download(key)
                if hashlib.sha256(check).hexdigest() != digest or len(check) != len(data):
                    raise ArtifactError('Published artifact verification failed')
                chunks.append({'key': key, 'sha256': digest, 'size': len(data)})
        return chunks

    def publish(self, workspace, outputs, prefix, *, known_files=None, external_files=None):
        """Publish only outputs. References are portable workspace paths, never absolutes.

        known_files maps (sha256,size) to existing file descriptors. external_files
        maps paths to already verified immutable capture objects in the input bucket.
        """
        workspace = Path(workspace).resolve()
        known_files = known_files or {}
        external_files = external_files or {}
        files, packed = [], []
        seen = set()
        for output in outputs:
            root = workspace / safe_relative(output)
            if not root.exists():
                raise ArtifactError('Expected stage output is missing: ' + output)
            for path in sorted(root.rglob('*')) if root.is_dir() else [root]:
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise ArtifactError('Stage artifacts cannot contain links')
                if path.is_dir():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(workspace) or not path.is_file():
                    raise ArtifactError('Artifact is outside the workspace')
                name = safe_relative(path.relative_to(workspace).as_posix())
                if name.casefold() in seen:
                    raise ArtifactError('Duplicate artifact path')
                seen.add(name.casefold())
                digest, size = sha_file(path), path.stat().st_size
                entry = {'path': name, 'sha256': digest, 'size': size}
                previous = external_files.get(name) or known_files.get((digest, size))
                if previous is not None:
                    if previous['sha256'] != digest or previous['size'] != size:
                        raise ArtifactError('Referenced file fingerprint mismatch')
                    # File refs point directly at original objects or an earlier bundle.
                    entry['source'] = previous['source']
                else:
                    packed.append(path)
                files.append(entry)
        with TemporaryDirectory(prefix='.artifact-', dir=workspace.parent) as temporary:
            archive_path = Path(temporary) / 'bundle.zip'
            with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                for path in packed:
                    archive.write(path, path.relative_to(workspace).as_posix())
            bundle = None
            if packed:
                bundle = {'chunks': self._upload_chunks(archive_path, prefix),
                          'sha256': sha_file(archive_path), 'size': archive_path.stat().st_size}
                bundle_ref = self.put_json(f'{prefix}/bundles', bundle)
                for entry in files:
                    if 'source' not in entry:
                        entry['source'] = {'bundle_key': bundle_ref.key, 'bundle_sha256': bundle_ref.sha256,
                                           'member': entry['path']}
        manifest = {'schema_version': 1, 'outputs': list(outputs), 'files': files}
        ref = self.put_json(f'{prefix}/manifests', manifest)
        return ref, manifest

    @staticmethod
    def file_index(manifest):
        return {(entry['sha256'], entry['size']): entry for entry in manifest['files']}

    def _download_bundle(self, ref, target):
        manifest = self.read_json(ref)
        chunks = manifest.get('chunks')
        if (not isinstance(chunks, list) or not chunks or len(chunks) > 10000
                or not isinstance(manifest.get('size'), int) or not 0 < manifest['size'] <= MAX_RESTORE_BYTES):
            raise ArtifactError('Invalid artifact bundle')
        total = 0
        with target.open('wb') as stream:
            for chunk in chunks:
                data = self.objects.download(safe_relative(chunk['key']))
                if len(data) != chunk['size'] or hashlib.sha256(data).hexdigest() != chunk['sha256']:
                    raise ArtifactError('Artifact chunk fingerprint mismatch')
                total += len(data)
                if total > manifest['size']:
                    raise ArtifactError('Artifact bundle exceeds declared size')
                stream.write(data)
        if total != manifest['size'] or sha_file(target) != manifest['sha256']:
            raise ArtifactError('Artifact bundle fingerprint mismatch')

    def restore(self, ref, workspace, *, select_paths=None, local_files=None):
        manifest = self.read_json(ref)
        files = manifest.get('files')
        if (manifest.get('schema_version') != 1 or not isinstance(files, list)
                or len(files) > 100000):
            raise ArtifactError('Unsupported stage manifest')
        workspace = Path(workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        local_files = {} if local_files is None else local_files
        chosen, seen, total = [], set(), 0
        for entry in files:
            name = safe_relative(entry['path'])
            if name.casefold() in seen:
                raise ArtifactError('Duplicate artifact path')
            seen.add(name.casefold())
            if type(entry.get('size')) is not int or entry['size'] < 0:
                raise ArtifactError('Invalid file size')
            total += entry['size']
            if total > MAX_RESTORE_BYTES:
                raise ArtifactError('Restored stage exceeds size limit')
            if select_paths is None or name in select_paths:
                chosen.append(entry)
        existing_bytes = sum(path.stat().st_size for path in workspace.rglob('*') if path.is_file())
        additional_bytes = sum(entry['size'] for entry in chosen if not (workspace / entry['path']).is_file())
        if existing_bytes + additional_bytes > MAX_RESTORE_BYTES:
            raise ArtifactError('Restored workspace exceeds size limit')
        with TemporaryDirectory(prefix='.restore-', dir=workspace.parent) as temporary:
            bundles = {}
            try:
                for entry in chosen:
                    target = workspace / entry['path']
                    if not target.resolve().is_relative_to(workspace):
                        raise ArtifactError('Artifact target leaves workspace')
                    if target.is_file() and target.stat().st_size == entry['size'] and sha_file(target) == entry['sha256']:
                        local_files[(entry['sha256'], entry['size'])] = target
                        continue
                    previous = local_files.get((entry['sha256'], entry['size']))
                    if (previous is not None and previous.is_file()
                            and previous.resolve().is_relative_to(workspace)
                            and previous.stat().st_size == entry['size'] and sha_file(previous) == entry['sha256']):
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(previous, target)
                        local_files[(entry['sha256'], entry['size'])] = target
                        continue
                    source = entry['source']
                    staged = Path(temporary) / 'file.tmp'
                    if 'object_key' in source:
                        data = self.objects.download(safe_relative(source['object_key']))
                        if len(data) != entry['size'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                            raise ArtifactError('Source object changed after submission')
                        staged.write_bytes(data)
                    else:
                        bundle_key = safe_relative(source['bundle_key'])
                        member = safe_relative(source['member'])
                        identity = (bundle_key, source['bundle_sha256'])
                        if identity not in bundles:
                            if self.cache_dir is not None:
                                path = self._cached_bundle(ArtifactRef(*identity))
                            else:
                                path = Path(temporary) / f'{len(bundles)}.zip'
                                self._download_bundle(ArtifactRef(*identity), path)
                            bundles[identity] = zipfile.ZipFile(path)
                        archive = bundles[identity]
                        info = archive.getinfo(member)
                        mode = info.external_attr >> 16
                        if info.file_size != entry['size'] or stat.S_ISLNK(mode):
                            raise ArtifactError('Invalid artifact member')
                        with archive.open(info) as src, staged.open('wb') as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                    if staged.stat().st_size != entry['size'] or sha_file(staged) != entry['sha256']:
                        raise ArtifactError('Artifact file fingerprint mismatch')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(target)
                    local_files[(entry['sha256'], entry['size'])] = target
            finally:
                for archive in bundles.values():
                    archive.close()
        return manifest
