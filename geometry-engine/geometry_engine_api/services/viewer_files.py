"""Materialize a committed viewer once; serve its checked files locally."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from uuid import uuid4

from .artifacts import ArtifactError, ArtifactRef, json_bytes, safe_relative, sha_file


_CACHE_LOCK = threading.Lock()


class LocalViewerCacheError(ArtifactError):
    pass


class ViewerFiles:
    def __init__(self, artifacts, cache_root):
        self.artifacts, self.root = artifacts, Path(cache_root).resolve()

    def file(self, export, name):
        name = safe_relative(name)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / str(export.id) / export.result_manifest_sha256
        ref = ArtifactRef(export.result_manifest_key, export.result_manifest_sha256)
        # Local cache construction is serialized; completed requests read locally.
        with _CACHE_LOCK:
            try:
                return self._checked_file(destination, ref, name)
            except LocalViewerCacheError:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(prefix='.viewer-', dir=destination.parent) as temporary:
                    staging = Path(temporary) / 'ready'
                    manifest = self.artifacts.restore(ref, staging)
                    (staging / '.stage-manifest.json').write_bytes(json_bytes(manifest))
                    if destination.exists():
                        if (destination.is_symlink() or getattr(destination, 'is_junction', lambda: False)()
                                or not destination.resolve().is_relative_to(self.root)):
                            raise ArtifactError('Viewer cache path leaves cache root')
                        # Retire only this derived cache inside the controlled temp
                        # directory; source artifacts remain immutable in Storage.
                        destination.rename(Path(temporary) / ('retired-' + uuid4().hex))
                    staging.rename(destination)
                return self._checked_file(destination, ref, name)

    @staticmethod
    def _checked_file(destination, ref, name):
        marker = destination / '.stage-manifest.json'
        if not marker.is_file():
            raise LocalViewerCacheError('Viewer cache is not populated')
        if sha_file(marker) != ref.sha256:
            raise LocalViewerCacheError('Viewer cache manifest changed')
        manifest = json.loads(marker.read_bytes())
        relative = f'viewer/{name}'
        entry = next((entry for entry in manifest['files'] if entry['path'] == relative), None)
        if entry is None:
            raise FileNotFoundError('Viewer asset not found')
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination) or not target.is_file():
            raise LocalViewerCacheError('Viewer cache file is missing')
        if target.stat().st_size != entry['size'] or sha_file(target) != entry['sha256']:
            raise LocalViewerCacheError('Viewer cache file changed')
        return target
