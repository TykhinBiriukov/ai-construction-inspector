"""Download one capture into a private worker workspace and pin its IFC bytes."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from .archive import download_archive
from .artifacts import ArtifactError, sha_file
from .session_download import SessionDownloadService


class InspectionInputs:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client

    def download(self, lease, workspace):
        workspace = Path(workspace).resolve()
        with TemporaryDirectory(prefix='.inputs-', dir=workspace.parent) as temporary:
            settings = replace(self.settings, data_dir=Path(temporary))
            result = SessionDownloadService(settings, self.client).extract(
                None, lease.session_id, manifest_path=lease.capture_manifest_path)
            source = result.directory / 'original'
            shutil.copytree(source, workspace / 'original')
            shutil.copyfile(result.directory / 'download-report.json', workspace / 'download-report.json')
        report = json.loads((workspace / 'download-report.json').read_text(encoding='utf-8'))
        manifest_path = workspace / 'original' / report['manifest']
        expected_hash = Path(lease.capture_manifest_path).parent.name
        if len(expected_hash) == 64 and sha_file(manifest_path) != expected_hash:
            raise ArtifactError('Capture manifest fingerprint differs from its object key')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        # Source capture assets are already content-checked by SessionDownloadService.
        # Reference them in the download checkpoint instead of uploading copies.
        references = {
            manifest_path.relative_to(workspace).as_posix(): {
                'sha256': sha_file(manifest_path), 'size': manifest_path.stat().st_size,
                'source': {'object_key': lease.capture_manifest_path}}}
        for observation in manifest.get('observations', []):
            for item in observation['files']:
                path = f"original/observations/{observation['observation_id']}/{item['name']}"
                references[path] = {'sha256': item['sha256'].lower(), 'size': item['byte_length'],
                                    'source': {'object_key': item['object_key']}}
        signed = self.client.storage.from_(settings.storage_bucket).create_signed_url(
            lease.ifc_object_key, int(settings.download_deadline_s) + 60)
        download_archive(signed['signedURL'], workspace / 'reference.ifc',
                         replace(settings, max_download_bytes=min(settings.max_download_bytes, 512 * 1024**2)))
        # Freeze the IFC in this job's download artifacts; the user's source key may change.
        digest = sha_file(workspace / 'reference.ifc')
        (workspace / 'inputs.json').write_text(json.dumps({
            'schema_version': 1, 'session_id': lease.session_id,
            'capture_manifest_path': lease.capture_manifest_path,
            'capture_manifest_sha256': sha_file(manifest_path),
            'ifc_object_key': lease.ifc_object_key, 'ifc_sha256': digest,
        }, indent=2) + '\n', encoding='utf-8')
        return references


INPUT_OUTPUTS = ('original', 'reference.ifc', 'download-report.json', 'inputs.json')
