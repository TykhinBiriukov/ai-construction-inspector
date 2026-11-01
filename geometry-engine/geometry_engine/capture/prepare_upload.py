"""Create an independently verified short-layout upload folder from a raw capture."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from geometry_engine.capture.session_layout import raw_original_directory, raw_session
from geometry_engine.capture.storage_layout import IDENTIFIER, capture_manifest_location


def prepare_capture_upload(directory, output_root):
    session_id = raw_session(directory).name
    if not re.fullmatch(IDENTIFIER, session_id):
        raise ValueError('Invalid session directory name')
    original = raw_original_directory(directory)
    manifests = list((original / 'manifests').glob('*/capture-manifest.json'))
    if len(manifests) != 1:
        raise ValueError('Expected one standalone capture manifest')
    source_manifest = manifests[0]
    if not source_manifest.resolve().is_relative_to(original):
        raise ValueError('Manifest outside raw directory')
    source_bytes = source_manifest.read_bytes()
    manifest = json.loads(source_bytes)
    if (manifest.get('format') != 'standalone_capture_v1' or manifest.get('session_id') != session_id
            or manifest.get('project_id') != 'standalone'):
        raise ValueError('Expected a standalone capture matching the session directory')
    observations = manifest.get('observations')
    if not isinstance(observations, list) or not observations or manifest.get('observation_count') != len(observations):
        raise ValueError('Invalid observation count')
    output_root = Path(output_root).resolve()
    destination = output_root / session_id
    if destination.is_relative_to(original) or original.is_relative_to(destination):
        raise ValueError('Upload destination must be separate from the raw capture')
    if destination.exists():
        raise FileExistsError('Upload directory already exists; use a new output root')
    output_root.mkdir(parents=True, exist_ok=True)
    count = total = 0
    seen = set()
    with TemporaryDirectory(prefix='.capture-upload-', dir=output_root) as temporary:
        staged = Path(temporary) / session_id
        staged.mkdir()
        for observation in observations:
            identity = observation.get('observation_id')
            files = observation.get('files')
            if (not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identity)
                    or not isinstance(files, list) or not files):
                raise ValueError('Invalid observation or file list')
            for item in files:
                name, digest, size = item.get('name'), item.get('sha256'), item.get('byte_length')
                if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name)
                        or name.endswith('.') or name.split('.')[0].upper() in
                        {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}
                        or not isinstance(digest, str)
                        or not re.fullmatch(r'[a-fA-F0-9]{64}', digest) or type(size) is not int or size <= 0):
                    raise ValueError('Invalid capture file description')
                relative = f'observations/{identity}/{name}'
                if relative.casefold() in seen:
                    raise ValueError('Duplicate capture file')
                seen.add(relative.casefold())
                source = (original / relative).resolve()
                if not source.is_relative_to(original):
                    raise ValueError('Capture file outside raw directory')
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                with target.open('rb') as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                if target.stat().st_size != size or actual != digest.lower():
                    raise ValueError('Capture file size or SHA-256 differs from manifest')
                item['object_key'] = f'{session_id}/{relative}'
                count += 1; total += size
        encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode('utf-8')
        digest = hashlib.sha256(encoded).hexdigest()
        manifest_key = f'{session_id}/manifests/{digest}/capture-manifest.json'
        capture_manifest_location(manifest_key, session_id)
        path = staged / 'manifests' / digest / 'capture-manifest.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(encoded)
        if source_manifest.read_bytes() != source_bytes:
            raise ValueError('Source manifest changed while exporting')
        staged.rename(destination)
    return {'session_id': session_id, 'upload_directory': str(destination),
            'capture_manifest_path': manifest_key, 'file_count': count+1,
            'bytes': total+len(encoded), 'sha256_verified': True}
