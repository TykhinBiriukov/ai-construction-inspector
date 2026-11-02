"""Inspector-owned adapter for Geometry's existing /inspections API.

The accepted source and Inspector job remain authoritative. Export manifests are
compatibility representations, not new captures. No Geometry code is imported.
"""
import hashlib
import base64
import io
import json
import re
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from typing import Literal

from .a2a_protocol import decode_json
from .errors import APIError
from .storage import canonical_json
from .supabase_captures import SupabaseCaptureSourceStore

HASH = re.compile(r'^[a-f0-9]{64}$')


def invalid():
    return APIError(503, 'invalid_geometry_result', 'Geometry response could not be bound to the saved job and source', retryable=True)


def unavailable(code='geometry_unavailable'):
    return APIError(503, code, 'Geometry integration will retry the saved operation', retryable=True)


class Wire(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Dimension(Wire):
    ifc: FiniteFloat | None
    scan: FiniteFloat | None
    difference: FiniteFloat | None


class Dimensions(Wire):
    width: Dimension
    height: Dimension


class Displacement(Wire):
    along_wall: FiniteFloat | None
    vertical: FiniteFloat | None
    normal_to_wall: FiniteFloat | None


class Quality(Wire):
    boundary_supported_on_all_sides: bool | None
    absolute_accuracy_verified: bool | None
    issues: list[str] = Field(max_length=100)


class Photo(Wire):
    frame_id: str = Field(min_length=1, max_length=256)
    rgb_url: str | None
    mask_url: str | None
    overlay_url: str | None


class ComparedObject(Wire):
    id: str = Field(min_length=1, max_length=256)
    type: Literal['door', 'window']
    scan_object_id: str | None
    ifc_guid: str | None
    match_status: Literal['matched', 'ambiguous', 'scan_object_not_matched', 'ifc_object_not_observed', 'ifc_reference_unavailable']
    dimensions: Dimensions
    displacement: Displacement
    quality: Quality
    photos: list[Photo] = Field(max_length=100)


class Surface(Wire):
    ifc_guid: str
    type: str
    status: str
    compared_area_m2: FiniteFloat | None
    p95_absolute_deviation: FiniteFloat | None
    area_fraction_outside_tolerance: FiniteFloat | None
    whole_element_verified: bool


class ResultLinks(Wire):
    point_cloud: str
    mesh: str
    segmented_mesh: str
    ifc: str


class CompactResult(Wire):
    schema_version: Literal['2.0']
    job_id: UUID
    session_id: str
    units: Literal['meter']
    difference_convention: Literal['scan_minus_ifc']
    tolerance: FiniteFloat
    links_expires_at: str
    summary: dict[str, int]
    objects: list[ComparedObject] = Field(max_length=4000)
    surfaces: list[Surface] = Field(max_length=4000)
    artifacts: ResultLinks


def without_links(value):
    """Stable evidence snapshot: expiring credentials never enter model input."""
    if isinstance(value, dict):
        return {k: without_links(v) for k, v in value.items()
                if not k.endswith('_url') and k not in ('artifacts', 'links_expires_at')}
    if isinstance(value, list):
        return [without_links(v) for v in value]
    return value


class GeometryEngineAdapter:
    def auth_headers(self):
        if self.settings.geometry_api_username:
            credentials = (self.settings.geometry_api_username + ':' + self.settings.geometry_api_token).encode('ascii')
            return {'Authorization': 'Basic ' + base64.b64encode(credentials).decode('ascii')}
        return {'Authorization': 'Bearer ' + self.settings.geometry_api_token}

    def __init__(self, service):
        self.service, self.settings = service, service.settings
        transport = getattr(service, 'geometry_transport', None)
        self.storage = SupabaseCaptureSourceStore(self.settings,
            httpx.Client(transport=transport, timeout=self.settings.geometry_timeout_s) if transport else None)

    @staticmethod
    def require_current_command(command):
        # Retain historical commands verbatim, but never reinterpret or replay
        # their retired wire contract against a different service API.
        if not isinstance(command, dict) or command.get('adapter') != 'inspections_v1':
            raise APIError(409, 'geometry_contract_retired',
                'This saved Geometry command uses the retired API. Its source and history were retained; automatic replay is disabled.')

    def private_files(self, job_id):
        with self.service.database.connection() as connection:
            row = connection.execute('SELECT response_body FROM receipts WHERE scope=? AND operation_id=?',
                                     ('geometry-result-files', job_id)).fetchone()
        if row is None:
            raise invalid()
        return json.loads(row['response_body'])

    def plan(self, session, inventory, inspection, job_id, operation_key):
        if not self.settings.supabase_enabled:
            raise unavailable('geometry_storage_not_configured')
        if session['storage'].get('provider') == 'supabase' and (
                session['storage'].get('project_url') != self.settings.supabase_url.rstrip('/')
                or session['storage'].get('bucket') != self.settings.supabase_storage_bucket):
            raise unavailable('geometry_storage_configuration_changed')
        model = inspection.get('bim_model')
        if not model or not model['filename'].lower().endswith('.ifc'):
            raise unavailable('geometry_ifc_required')
        observations = inventory.get('observations', [])
        segments = {o.get('segment_id') for o in observations}
        if not observations or len(segments) != 1 or None in segments or any(
                not {'frame.json', 'rgb.jpg', 'depth.f32'} <= {f['name'] for f in o['files']} for o in observations):
            raise APIError(422, 'geometry_input_unsupported', 'This Geometry API requires RGB-D observations in one ARKit segment')
        prefix = session['session_id']
        files = []
        exported = []
        by_identity = {(f['observation_id'], f['name']): f for f in inventory['files']}
        for observation in observations:
            rows = []
            for original in observation['files']:
                source = by_identity[observation['observation_id'], original['name']]
                target = f"{prefix}/observations/{observation['observation_id']}/{source['sha256']}/{source['name']}"
                rows.append({k: source[k] for k in ('name', 'sha256', 'byte_length')} | {'object_key': target})
                files.append({'source': source, 'target': target})
            exported.append({'observation_id': observation['observation_id'], 'segment_id': observation['segment_id'], 'files': rows})
        manifest = {'schema_version': '0.1', 'format': 'standalone_capture_v1',
            'project_id': 'standalone', 'session_id': session['session_id'],
            'observation_count': len(exported), 'observations': exported,
            'segments': [{'segment_id': next(iter(segments)), 'units': 'm',
                          'coordinate_convention': 'arkit_world_camera_v1', 'matrix_layout': 'rows_column_vectors'}],
            'inspector_source': {'project_id': session['project_id'], 'job_id': job_id,
                                 'capture_ref': session['source']['capture_ref']}}
        raw = canonical_json(manifest).encode()
        manifest_key = f'{prefix}/manifests/{hashlib.sha256(raw).hexdigest()}/capture-manifest.json'
        model_key = f"{prefix}/references/{model['sha256']}.ifc"
        return {'adapter': 'inspections_v1', 'schema_version': '0.1', 'job_id': job_id,
            'operation_key': operation_key, 'project_id': session['project_id'], 'session_id': session['session_id'],
            'manifest_sha256': session['source']['manifest_sha256'], 'model_sha256': model['sha256'],
            'base_url': self.settings.geometry_base_url.rstrip('/'),
            'storage_origin': self.settings.supabase_url, 'storage_bucket': self.settings.supabase_storage_bucket,
            'payload': {'session_id': session['session_id'], 'capture_manifest_path': manifest_key,
                        'ifc_object_key': model_key, 'parameters': {'tolerance_m': 0.1,
                         'include_boundary_candidates': True, 'interior_point_ifc_m': None}},
            'export': {'files': files, 'manifest': manifest, 'model': model}}

    def check_config(self, command):
        self.require_current_command(command)
        if (command['base_url'] != self.settings.geometry_base_url.rstrip('/')
                or command['storage_origin'] != self.settings.supabase_url
                or command['storage_bucket'] != self.settings.supabase_storage_bucket):
            raise unavailable('geometry_configuration_changed')
        if not self.settings.geometry_api_token:
            raise unavailable('geometry_credentials_not_configured')

    def _bytes(self, session, item):
        if item['byte_length'] > self.settings.max_capture_file_bytes:
            raise APIError(413, 'geometry_input_too_large', 'A source file exceeds the configured export bound')
        source = self.service.capture.store_for(session)
        if session['storage'].get('provider') == 'supabase':
            with source.connection() as client:
                stream = io.BytesIO()
                source._stream_to(client, item['pinned_object_key'], stream, item['byte_length'], item['sha256'])
                return stream.getvalue()
        return source._verify_object(item, read=True)

    def export_next(self, session, command, work):
        """One bounded page per capture worker claim; checkpoint before next page."""
        self.check_config(command)
        progress = session['geometry_job'].get('export_position', 0)
        entries = command['export']['files']
        limit = min(progress + 4, len(entries))
        for entry in entries[progress:limit]:
            item = entry['source']
            data = self._bytes(session, item)
            if item['name'] == 'frame.json':
                frame = decode_json(data.decode('utf-8'))
                if (frame.get('schema_version') != '0.2' or frame.get('session_id') != session['session_id']
                        or frame.get('depth_unit') != 'm'
                        or frame.get('image_orientation') != 'native_buffer_unrotated_no_exif_rotation'
                        or not str(frame.get('depth_to_rgb_mapping', '')).startswith('Unrotated aligned buffers;')
                        or frame.get('source_to_stored_image') != [[1, 0, 0], [0, 1, 0], [0, 0, 1]]):
                    raise APIError(422, 'geometry_input_unsupported', 'This Geometry API requires original unrotated RGB-D frame.json 0.2')
            with self.storage.connection() as client:
                self.storage._publish_pin(client, entry['target'], io.BytesIO(data), len(data), item['sha256'])
        if limit == len(entries):
            model = command['export']['model']
            path = self.service.bim_store.path_for(model['sha256'])
            with path.open('rb') as source:
                if path.stat().st_size != model['byte_length'] or hashlib.file_digest(source, 'sha256').hexdigest() != model['sha256']:
                    raise invalid()
                with self.storage.connection() as client:
                    self.storage._publish_pin(client, command['payload']['ifc_object_key'], source, model['byte_length'], model['sha256'])
            data = canonical_json(command['export']['manifest']).encode()
            with self.storage.connection() as client:
                self.storage._publish_pin(client, command['payload']['capture_manifest_path'], io.BytesIO(data), len(data), hashlib.sha256(data).hexdigest())
        with self.service.database.transaction() as connection:
            if not self.service.capture._owns_lease(connection, work):
                return False
            current = self.service._load(connection, 'sessions', session['session_id'])
            current['geometry_job']['export_position'] = limit
            connection.execute('UPDATE geometry_jobs SET data=? WHERE job_id=?', (canonical_json(current['geometry_job']), command['job_id']))
            self.service.capture._save_session(connection, current)
            if limit < len(entries):
                self.service.geometry_results._requeue(connection, work)
                return False
        return True

    def request(self, method, command, suffix, client=None, **kwargs):
        self.check_config(command)
        owns = client is None
        client = client or httpx.Client(transport=getattr(self.service, 'geometry_transport', None), timeout=self.settings.geometry_timeout_s)
        try:
            with client.stream(method, command['base_url'] + suffix,
                    headers={**self.auth_headers(), **kwargs.pop('headers', {})}, timeout=self.settings.geometry_timeout_s,
                    follow_redirects=False, **kwargs) as response:
                if response.status_code not in (200, 201, 202):
                    raise unavailable()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > self.settings.max_manifest_bytes:
                        raise invalid()
                body = decode_json(raw.decode('utf-8'))
                if not isinstance(body, dict):
                    raise invalid()
                return body
        except httpx.HTTPError:
            raise unavailable() from None
        except (ValueError, UnicodeError, RecursionError):
            raise invalid() from None
        finally:
            if owns:
                client.close()

    def validate_status(self, body, command, remote_id=None):
        try:
            identity = str(UUID(body['job_id']))
        except (KeyError, ValueError, TypeError):
            raise invalid() from None
        if (remote_id is not None and identity != remote_id) or body.get('session_id') != command['session_id']:
            raise invalid()
        if any(body.get(k) != command['payload'][k] for k in ('capture_manifest_path', 'ifc_object_key', 'parameters')):
            raise invalid()
        if body.get('status') not in ('queued', 'running', 'completed', 'failed', 'cancelled'):
            raise invalid()
        if body.get('ifc_sha256') not in (None, command['model_sha256']):
            raise invalid()
        return identity

    def submit(self, command, client=None):
        body = self.request('POST', command, '/inspections', client,
                            headers={'Idempotency-Key': command['operation_key'], 'Content-Type': 'application/json'},
                            content=canonical_json(command['payload']).encode('utf-8'))
        self.validate_status(body, command)
        return body

    def storage_json(self, key, digest):
        if not isinstance(digest, str) or not HASH.fullmatch(digest) or not isinstance(key, str) or any(p in ('', '.', '..') for p in key.split('/')):
            raise invalid()
        with self.storage.connection() as client:
            raw = bytearray()
            with client.stream('GET', self.storage._url('object', key), headers=self.storage._headers(), follow_redirects=False) as response:
                if response.status_code != 200:
                    raise unavailable()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > self.settings.max_manifest_bytes:
                        raise invalid()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise invalid()
        try:
            value = decode_json(raw.decode('utf-8'))
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise invalid() from None

    def file_entry(self, command, remote_id, url, manifest, indexes):
        if not isinstance(url, str):
            raise invalid()
        base, parsed = urlsplit(command['base_url']), urlsplit(url)
        prefix = base.path.rstrip('/') + f'/inspections/{remote_id}/result/files/'
        if (parsed.scheme != base.scheme or parsed.netloc != base.netloc or parsed.username or parsed.password
                or parsed.fragment or not parsed.path.startswith(prefix) or not parsed.query):
            raise invalid()
        rest = unquote(parsed.path[len(prefix):])
        stage, _, path = rest.partition('/')
        if not path or any(p in ('', '.', '..') for p in path.split('/')):
            raise invalid()
        if stage not in indexes:
            ref = manifest.get('stages', {}).get(stage)
            if not isinstance(ref, dict):
                raise invalid()
            index = self.storage_json(ref.get('key'), ref.get('sha256'))
            if index.get('schema_version') != 1 or not isinstance(index.get('files'), list):
                raise invalid()
            entries = index['files']
            if any(not isinstance(e, dict) or not isinstance(e.get('path'), str) for e in entries):
                raise invalid()
            indexes[stage] = {e['path']: e for e in entries}
            if len(indexes[stage]) != len(entries):
                raise invalid()
        entry = indexes[stage].get(path)
        if (not entry or type(entry.get('size')) is not int or entry['size'] <= 0
                or not isinstance(entry.get('sha256'), str) or not HASH.fullmatch(entry['sha256'])):
            raise invalid()
        return {k: entry[k] for k in ('path', 'sha256', 'size')} | {'stage': stage, 'url': url}

    def poll(self, session, command, client=None):
        remote_id = session['geometry_job']['remote_job_id']
        body = self.request('GET', command, '/inspections/' + remote_id, client)
        self.validate_status(body, command, remote_id)
        status = body['status']
        result = None
        if status == 'completed':
            if body.get('ifc_sha256') != command['model_sha256']:
                raise invalid()
            manifest = self.storage_json(body.get('result_manifest_key'), body.get('result_manifest_sha256'))
            if (manifest.get('job_id') != remote_id or manifest.get('session_id') != command['session_id']
                    or manifest.get('ifc_sha256') != command['model_sha256']):
                raise invalid()
            payload = self.request('GET', command, f'/inspections/{remote_id}/result', client)
            try:
                report = CompactResult.model_validate(payload).model_dump(mode='json')
            except ValueError:
                raise invalid() from None
            if report['job_id'] != remote_id or report['session_id'] != command['session_id']:
                raise invalid()
            after = self.request('GET', command, '/inspections/' + remote_id, client)
            self.validate_status(after, command, remote_id)
            if after.get('status') != 'completed' or after.get('result_manifest_sha256') != body['result_manifest_sha256']:
                raise invalid()
            if len({o['id'] for o in report['objects']}) != len(report['objects']):
                raise invalid()
            indexes, descriptors, photos = {}, [], {}
            artifact_paths = {'point_cloud': 'reconstruction/point_cloud.ply', 'mesh': 'reconstruction/mesh.ply',
                              'segmented_mesh': 'mesh_segmentation/mesh.ply', 'ifc': 'reference.ifc'}
            for name, url in report['artifacts'].items():
                entry = self.file_entry(command, remote_id, url, manifest, indexes)
                if entry['path'] != artifact_paths[name] or (name == 'ifc' and entry['sha256'] != command['model_sha256']):
                    raise invalid()
                descriptors.append({'artifact_id': name, 'kind': 'point_cloud' if name == 'point_cloud' else 'reconstructed_model',
                    'media_type': 'application/octet-stream', 'byte_length': entry['size'], 'sha256': entry['sha256'], 'url': url})
            submitted_frames = {o['observation_id'] for o in command['export']['manifest']['observations']}
            for obj in report['objects']:
                for photo in obj['photos']:
                    if photo['frame_id'] not in submitted_frames:
                        raise invalid()
                    for key in ('rgb_url', 'mask_url', 'overlay_url'):
                        if photo[key]:
                            entry = self.file_entry(command, remote_id, photo[key], manifest, indexes)
                            if key == 'rgb_url' and (entry['stage'] != 'input'
                                    or entry['path'] != f"input/frames/{photo['frame_id']}/rgb.jpg"):
                                raise invalid()
                            owners = photos.get(photo[key], {}).get('object_ids', [])
                            refs = photos.get(photo[key], {}).get('photo_refs', [])
                            ref = {'object_id': obj['id'], 'frame_id': photo['frame_id'], 'role': key.removesuffix('_url')}
                            photos[photo[key]] = entry | {'object_ids': list(dict.fromkeys(owners + [obj['id']])),
                                'photo_refs': refs if ref in refs else refs + [ref]}
            result = {'job_id': command['job_id'], 'remote_job_id': remote_id, 'session_id': command['session_id'],
                'project_id': command['project_id'], 'manifest_sha256': command['manifest_sha256'],
                'model_version': session['model_version'], 'model_sha256': command['model_sha256'],
                'geometry_revision': 1, 'job_revision': 3, 'processing_scope': 'geometry_only_v1',
                'analysis_origin': 'recorded_model' if session.get('input_mode') == 'recorded_capture' else 'live_model',
                'is_synthetic': session.get('capture_origin') == 'synthetic',
                'model_is_synthetic': session.get('model_is_synthetic', False),
                'observation_accounting': 'unreported_by_geometry', 'evidence_observation_ids': [],
                'excluded_observations': [], 'artifacts': descriptors, 'adapter': 'inspections_v1',
                'result_manifest_sha256': body['result_manifest_sha256'],
                'report': without_links(report), 'photo_files': photos,
                'verification_scope': 'job_binding_and_committed_artifact_metadata',
                'pipeline_version': manifest.get('pipeline_version'),
                'implementation_fingerprint': manifest.get('implementation'),
                'limitations': ['Geometry compact v2 does not report observation accounting, measurement uncertainty or alignment proof.']}
        return {'queued': 'queued', 'running': 'processing', 'completed': 'ready', 'failed': 'failed', 'cancelled': 'cancelled'}[status], result
