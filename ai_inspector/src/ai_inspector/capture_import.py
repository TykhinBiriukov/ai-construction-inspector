"""Import a recorded standalone capture from the configured single-project Geometry store."""
import hashlib
import io
import json
import re
from uuid import UUID

from .a2a_protocol import decode_json
from .capture_sources import S3CaptureSourceStore, _date, _uuid
from .errors import APIError
from .schemas import InspectionInput
from .service import require_role, utc_now
from .storage import canonical_json


def invalid(message):
    return APIError(422, 'capture_import_invalid', message)


def read(store, key, limit):
    with store.connection() as client, client.stream('GET', store._url('object', key), headers=store._headers()) as response:
        store._status(response, key)
        data = bytearray()
        for chunk in response.iter_bytes(65536):
            data.extend(chunk)
            if len(data) > limit:
                raise invalid('Stored object exceeds the import size limit')
    return bytes(data)


def discover(service, session_id):
    store = service.geometry_engine.storage
    prefix = session_id + '/manifests'
    with store.connection() as client:
        response = client.post(store._url('object/list', '').rstrip('/'), headers=store._headers(),
                               json={'prefix': prefix, 'limit': 2, 'sortBy': {'column': 'name', 'order': 'asc'}})
        store._status(response, prefix)
        rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise APIError(404, 'capture_not_found', 'No saved capture was found for this session ID')
    if len(rows) != 1 or not re.fullmatch(r'[a-f0-9]{64}', str(rows[0].get('name', ''))):
        raise invalid('Expected one immutable capture manifest; the session is ambiguous')
    digest = rows[0]['name']
    key = prefix + '/' + digest + '/capture-manifest.json'
    raw = read(store, key, service.settings.max_manifest_bytes)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise invalid('Capture manifest hash differs from its storage path')
    manifest = decode_json(raw.decode('utf-8'))
    inventory = validate_manifest(manifest, session_id, service.settings)
    return {'manifest_key': key, 'manifest_sha256': digest, 'manifest_byte_length': len(raw),
            'manifest': manifest, 'inventory': inventory,
            'storage_origin': store.settings.supabase_url, 'storage_bucket': store.settings.supabase_storage_bucket}


def validate_manifest(m, sid, settings):
    if not isinstance(m, dict):
        raise invalid('Manifest must be an object')
    if (m.get('schema_version') != '0.1' or m.get('format') != 'standalone_capture_v1'
            or m.get('session_id') != sid or m.get('project_id') != 'standalone'
            or m.get('capture_origin') not in ('device', 'synthetic')):
        raise invalid('Unsupported standalone capture identity or format')
    _date(m.get('capture_closed_at'), 'capture_closed_at')
    segments = m.get('segments')
    if not isinstance(segments, list) or len(segments) != 1:
        raise invalid('Geometry requires a single aligned ARKit segment')
    segment = segments[0]
    if not isinstance(segment, dict):
        raise invalid('Segment must be an object')
    segment_id = _uuid(segment.get('segment_id'), 'segment_id')
    if (segment.get('units'), segment.get('coordinate_convention'), segment.get('matrix_layout')) != (
            'm', 'arkit_world_camera_v1', 'rows_column_vectors'):
        raise invalid('Unsupported coordinate system')
    observations = m.get('observations')
    if not isinstance(observations, list) or not observations or type(m.get('observation_count')) is not int or m['observation_count'] != len(observations):
        raise invalid('Invalid observation inventory')
    seen, files, normalized, total = set(), [], [], 0
    for observation in observations:
        if not isinstance(observation, dict):
            raise invalid('Observation must be an object')
        oid = _uuid(observation.get('observation_id'), 'observation_id')
        if oid in seen or observation.get('segment_id') != segment_id:
            raise invalid('Duplicate observation or inconsistent segment')
        seen.add(oid)
        rows = observation.get('files')
        if not isinstance(rows, list) or not 3 <= len(rows) <= 4:
            raise invalid('Every observation must include RGB, depth and frame metadata')
        names, entries = set(), []
        for f in rows:
            if not isinstance(f, dict):
                raise invalid('File descriptor must be an object')
            name = f.get('name')
            if name not in ('rgb.jpg', 'depth.f32', 'confidence.u8', 'frame.json') or name in names:
                raise invalid('Invalid or duplicate capture filename')
            names.add(name)
            digest, size = f.get('sha256'), f.get('byte_length')
            if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
                raise invalid('Invalid source hash')
            if type(size) is not int or not 0 < size <= settings.max_capture_file_bytes:
                raise invalid('Invalid source size')
            if name == 'frame.json' and size > settings.max_manifest_bytes:
                raise invalid('Frame metadata is too large')
            key = f.get('object_key')
            if key not in (f'{sid}/observations/{oid}/{name}', f'{sid}/observations/{oid}/{digest}/{name}'):
                raise invalid('Source file is outside this capture')
            entry = {'name': name, 'object_key': key, 'sha256': digest, 'byte_length': size,
                     'pinned_object_key': f'{sid}/observations/{oid}/{digest}/{name}', 'observation_id': oid}
            entries.append(entry); files.append(entry); total += size
            if len(files) > settings.max_capture_files or total > settings.max_capture_bytes:
                raise invalid('Capture exceeds the configured inventory limits')
        if not {'rgb.jpg', 'depth.f32', 'frame.json'} <= names:
            raise invalid('RGB-D capture is required')
        normalized.append({'observation_id': oid, 'segment_id': segment_id, 'files': entries})
    return {'observations': normalized, 'files': files, 'observation_count': len(normalized), 'excluded_observations': []}


def plan(service, principal, run, session_id):
    require_role(principal, 'operator')
    if principal.project_id != service.settings.project_id:
        raise APIError(403, 'forbidden', 'Capture import is restricted to the configured project')
    with service.database.connection() as conn:
        row = conn.execute('SELECT data FROM sessions WHERE session_id=?', (session_id,)).fetchone()
        files = service.workspace.files(conn, principal, run['chat_id'])
        selected = [files[f] for f in run.get('file_ids', []) if f in files and not files[f].get('deleted') and files[f]['name'].lower().endswith('.ifc')]
        if len(selected) > 1:
            raise invalid('Select one reference IFC')
        model = {k: selected[0][k] for k in ('name', 'sha256', 'byte_length')} if selected else None
        if row:
            session = json.loads(row['data']); service._check_access(principal, session)
            inspection = service._load(conn, 'inspections', session['inspection_id'])
            if session['capture_state'] == 'cancelled' or not session.get('source'):
                raise invalid('This session has no reusable saved capture')
            if not inspection.get('bim_model'):
                raise invalid('This Inspector session has no pinned IFC; importing capture-only Inspector sessions is not supported yet')
            if model and model['sha256'] != inspection['bim_model']['sha256']:
                raise invalid('The selected IFC differs from the session reference; select its original IFC')
            return {'session_id': session_id, 'existing': True}
    snapshot = discover(service, session_id)
    if model is None:
        raw = read(service.geometry_engine.storage, session_id + '/reference.ifc', service.settings.max_bim_bytes)
        model = {'name': 'reference.ifc', 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_length': len(raw),
                 'object_key': session_id + '/reference.ifc'}
    return {'session_id': session_id, 'existing': False, 'snapshot': snapshot, 'model': model}


def execute(service, principal, run, command):
    require_role(principal, 'operator')
    sid = command['session_id']
    if command['existing']:
        session = service.get_session(principal, sid)
        if session['capture_state'] == 'cancelled':
            raise invalid('Session was cancelled')
        return session
    snapshot, model = command['snapshot'], command['model']
    check_store(service, snapshot)
    with service.database.connection() as conn:
        row = conn.execute('SELECT data FROM sessions WHERE session_id=?', (sid,)).fetchone()
        if row:
            session = json.loads(row['data']); service._check_access(principal, session)
            if session.get('import_operation_id') != command['operation_id']:
                raise APIError(409, 'capture_import_conflict', 'Another operation already imported this session; retry with its existing session')
            return session
    if model.get('object_key'):
        raw = read(service.geometry_engine.storage, model['object_key'], service.settings.max_bim_bytes)
    else:
        raw = service.bim_store.path_for(model['sha256']).read_bytes()
    if len(raw) != model['byte_length'] or hashlib.sha256(raw).hexdigest() != model['sha256']:
        raise invalid('The selected reference changed since the command was saved')
    inspection, _ = service.create_inspection(principal, InspectionInput(schema_version='0.1',
        request_id=UUID(command['operation_id']), project_id=principal.project_id, room_id='recorded-capture',
        prompt=run['text'], model_is_synthetic=snapshot['manifest']['capture_origin']=='synthetic'),
        io.BytesIO(raw), model['name'], 'application/octet-stream')
    completion_id = command['operation_id']
    ref = {k: snapshot[k] for k in ('manifest_key', 'manifest_sha256', 'manifest_byte_length')}
    ref.update(format='geometry_standalone_import_v1', storage_id='geometry-import')
    session = {'schema_version': '0.1', 'session_id': sid, 'inspection_id': inspection['inspection_id'],
        'project_id': principal.project_id, 'device_id': 'recorded-import', 'room_id': inspection['room_id'],
        'model_version': inspection['model_version'], 'model_is_synthetic': inspection['model_is_synthetic'],
        'policy_version': inspection['policy_version'], 'capture_origin': snapshot['manifest']['capture_origin'],
        'input_mode': 'synthetic_fixture' if snapshot['manifest']['capture_origin']=='synthetic' else 'recorded_capture',
        'parent_session_id': None, 'created_at': utc_now(), 'revision': 1, 'capture_state': 'submitted',
        'processing_state': 'validating', 'device_acknowledged_at': None, 'capture_task': None,
        'completion': {'completion_id': completion_id, 'status': 'pending', 'error': None},
        'source': {'status': 'pending', 'capture_ref': ref, 'manifest_sha256': snapshot['manifest_sha256'],
                   'observation_count': snapshot['inventory']['observation_count'], 'validation_error': None},
        'geometry_job': None, 'display_view_id': None, 'result': None, 'error': None, 'geometry_result': None,
        'processing_scope': 'geometry_only_v1', 'import_operation_id': completion_id,
        'storage': {'provider': 'supabase', 'storage_id': 'geometry-import', 'key_prefix': sid+'/',
                    'project_url': snapshot['storage_origin'], 'bucket': snapshot['storage_bucket'], 'folder_status': 'ready'}}
    with service.database.transaction() as conn:
        conn.execute('INSERT INTO sessions(session_id,inspection_id,project_id,device_id,data,storage_ready) VALUES (?,?,?,?,?,1)',
                     (sid, inspection['inspection_id'], principal.project_id, 'recorded-import', canonical_json(session)))
        body = {'capture_ref': ref, 'import_snapshot': snapshot, 'position': 0}
        conn.execute('INSERT INTO capture_completions VALUES (?,?,?,?,NULL)',
                     (sid, completion_id, canonical_json(body), canonical_json(session['completion'])))
        conn.execute("INSERT INTO work_items(work_id,session_id,kind,operation_id) VALUES (?,?,'validate',?)",
                     ('validate:'+sid+':'+completion_id, sid, completion_id))
    return session


def check_store(service, snapshot):
    settings = service.geometry_engine.storage.settings
    if (snapshot['storage_origin'], snapshot['storage_bucket']) != (settings.supabase_url, settings.supabase_storage_bucket):
        raise APIError(503, 'geometry_configuration_changed', 'Restore the saved import storage configuration', retryable=True)


def validate_page(service, session, body, work):
    snapshot = body['import_snapshot']; check_store(service, snapshot)
    inventory = snapshot['inventory']; position = body['position']
    observations = inventory['observations']
    if position == len(observations):
        return inventory  # Last page committed before a worker restart.
    observation = observations[position]
    store = service.geometry_engine.storage
    by_name = {f['name']: f for f in observation['files']}
    with store.connection() as client:
        for item in sorted(observation['files'], key=lambda f: f['name'] != 'frame.json'):
            raw = io.BytesIO()
            store._stream_to(client, item['object_key'], raw, item['byte_length'], item['sha256'])
            if item['name'] == 'frame.json':
                frame = decode_json(raw.getvalue().decode('utf-8'))
                if not isinstance(frame, dict):
                    raise invalid('Frame metadata must be an object')
                S3CaptureSourceStore._validate_frame(frame, session, observation, by_name)
                if (frame.get('schema_version') != '0.2'
                        or frame.get('image_orientation') != 'native_buffer_unrotated_no_exif_rotation'
                        or not str(frame.get('depth_to_rgb_mapping', '')).startswith('Unrotated aligned buffers;')
                        or frame.get('source_to_stored_image') != [[1,0,0],[0,1,0],[0,0,1]]):
                    raise invalid('Geometry requires original unrotated frame.json 0.2')
            store._publish_pin(client, item['pinned_object_key'], raw, item['byte_length'], item['sha256'])
    with service.database.transaction() as conn:
        if not service.capture._owns_lease(conn, work):
            return None
        body['position'] = position + 1
        conn.execute('UPDATE capture_completions SET request_body=? WHERE session_id=? AND completion_id=?',
                     (canonical_json(body), session['session_id'], work['operation_id']))
        if body['position'] < len(observations):
            service.geometry_results._requeue(conn, work)
            return None
    return inventory
