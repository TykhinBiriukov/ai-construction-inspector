"""Attach committed Geometry results using GET only; never submit computation."""
import hashlib
import io
import json
from uuid import UUID, uuid5

import httpx

from . import capture_import
from .errors import APIError
from .schemas import InspectionInput
from .service import require_role, utc_now
from .storage import canonical_json


def plan(service, principal, run, sid):
    require_role(principal, 'operator')
    if principal.project_id != service.settings.project_id:
        raise APIError(403, 'forbidden', 'Saved results belong to the configured project')
    with service.database.connection() as conn:
        row = conn.execute('SELECT data FROM sessions WHERE session_id=?', (sid,)).fetchone()
        existing = json.loads(row['data']) if row else None
        if existing:
            service._check_access(principal, existing)
            if existing['processing_state'] not in ('cancelled', 'completed', 'failed'):
                raise APIError(409, 'capture_in_progress', 'Stop the current processing before attaching a saved result')
        prior = service.geometry_review.states(conn, sid, run['chat_id'])
        if any(s['status'] == 'cancelled' for s in prior.values()):
            raise APIError(409, 'review_cancelled', 'This chat review was cancelled. Use a new chat for a new test.')
        files = service.workspace.files(conn, principal, run['chat_id'])
        selected = [files[f] for f in run.get('file_ids', []) if f in files and not files[f].get('deleted') and files[f]['name'].lower().endswith('.ifc')]
        if len(selected) > 1:
            raise capture_import.invalid('Select exactly one reference IFC')
        model = {k: selected[0][k] for k in ('name', 'sha256', 'byte_length')} if selected else None
    adapter = service.geometry_engine
    store = adapter.storage
    with store.connection() as client:
        response = client.post(store._url('object/list', '').rstrip('/'), headers=store._headers(),
                               json={'prefix': sid+'/results', 'limit': 65})
        store._status(response, sid+'/results')
        rows = response.json()
    if not isinstance(rows, list) or len(rows) > 64:
        raise capture_import.invalid('Saved result inventory is too large or invalid')
    candidates = []
    with httpx.Client(transport=getattr(service, 'geometry_transport', None), timeout=service.settings.geometry_timeout_s,
                      follow_redirects=False) as client:
        for row in rows:
            try: remote_id = str(UUID(row['name']))
            except (ValueError, KeyError, TypeError): continue
            with client.stream('GET', service.settings.geometry_base_url.rstrip('/')+'/inspections/'+remote_id,
                               headers=adapter.auth_headers()) as response:
                if response.status_code == 404: continue
                if response.status_code != 200:
                    raise APIError(503, 'geometry_unavailable', 'Could not read saved Geometry job metadata', retryable=True)
                raw = bytearray()
                for chunk in response.iter_bytes(65536):
                    raw.extend(chunk)
                    if len(raw) > service.settings.max_manifest_bytes:
                        raise capture_import.invalid('Saved job metadata exceeds the limit')
                from .a2a_protocol import decode_json
                body = decode_json(raw.decode())
            if (body.get('job_id') == remote_id and body.get('session_id') == sid and body.get('status') == 'completed'
                    and body.get('result_manifest_sha256') and body.get('ifc_sha256')
                    and (model is None or body['ifc_sha256'] == model['sha256'])):
                candidates.append(body)
    if not candidates:
        raise APIError(404, 'saved_geometry_not_found', 'No completed Geometry result matches this session and selected IFC. No processing was started.')
    body = max(candidates, key=lambda b: (b.get('finished_at') or '', b['job_id']))
    key = body['capture_manifest_path']
    # Restrict every imported key to the operator-supplied session namespace.
    if not key.startswith(sid+'/manifests/') or any(p in ('', '.', '..') for p in key.split('/')):
        raise capture_import.invalid('Saved result references another capture namespace')
    raw = capture_import.read(store, key, service.settings.max_manifest_bytes)
    from .a2a_protocol import decode_json
    manifest = decode_json(raw.decode())
    inventory = capture_import.validate_manifest(manifest, sid, service.settings)
    digest = hashlib.sha256(raw).hexdigest()
    if key != sid+'/manifests/'+digest+'/capture-manifest.json':
        raise capture_import.invalid('Saved capture manifest hash changed')
    if model is None:
        key = body['ifc_object_key']
        if not key.startswith(sid+'/') or any(p in ('', '.', '..') for p in key.split('/')) or not key.endswith('.ifc'):
            raise capture_import.invalid('Saved IFC is outside this session')
        raw = capture_import.read(store, key, service.settings.max_bim_bytes)
        model = {'name': 'reference.ifc', 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_length': len(raw), 'object_key': key}
    if model['sha256'] != body['ifc_sha256']:
        raise capture_import.invalid('Saved IFC hash differs from the completed result')
    return {'session_id': sid, 'saved_result': True, 'remote': body, 'model': model,
            'snapshot': {'manifest': manifest, 'inventory': inventory, 'manifest_key': body['capture_manifest_path'],
                         'manifest_sha256': digest, 'storage_origin': store.settings.supabase_url,
                         'storage_bucket': store.settings.supabase_storage_bucket}}


def execute(service, principal, run, plan):
    require_role(principal, 'operator')
    sid, snapshot, model, remote = plan['session_id'], plan['snapshot'], plan['model'], plan['remote']
    capture_import.check_store(service, snapshot)
    job_id = str(uuid5(UUID(plan['operation_id']), 'saved-result'))
    with service.database.connection() as conn:
        row = conn.execute('SELECT data FROM sessions WHERE session_id=?', (sid,)).fetchone()
        existing = json.loads(row['data']) if row else None
        if existing:
            service._check_access(principal, existing)
            if (existing.get('geometry_job') or {}).get('job_id') == job_id:
                return existing
            if existing['processing_state'] not in ('cancelled', 'completed', 'failed'):
                raise APIError(409, 'capture_in_progress', 'Another operation is processing this capture')
    raw = (capture_import.read(service.geometry_engine.storage, model['object_key'], service.settings.max_bim_bytes)
           if model.get('object_key') else service.bim_store.path_for(model['sha256']).read_bytes())
    if len(raw) != model['byte_length'] or hashlib.sha256(raw).hexdigest() != model['sha256']:
        raise capture_import.invalid('Saved reference changed')
    inspection, _ = service.create_inspection(principal, InspectionInput(schema_version='0.1', request_id=UUID(plan['operation_id']),
        project_id=principal.project_id, room_id='recorded-capture', prompt=run['text'],
        model_is_synthetic=snapshot['manifest']['capture_origin']=='synthetic'), io.BytesIO(raw), model['name'], 'application/octet-stream')
    command = {'adapter': 'inspections_v1', 'schema_version': '0.1', 'read_only_result': True,
        'expected_result_manifest_sha256': remote['result_manifest_sha256'], 'job_id': job_id,
        'operation_key': plan['operation_id'], 'project_id': principal.project_id, 'session_id': sid,
        'manifest_sha256': snapshot['manifest_sha256'], 'model_sha256': model['sha256'],
        'base_url': service.settings.geometry_base_url.rstrip('/'),
        'storage_origin': snapshot['storage_origin'], 'storage_bucket': snapshot['storage_bucket'],
        'payload': {k: remote[k] for k in ('session_id', 'capture_manifest_path', 'ifc_object_key', 'parameters')},
        'export': {'manifest': snapshot['manifest']}}
    job = {'job_id': job_id, 'remote_job_id': remote['job_id'], 'adapter': 'inspections_v1', 'status': 'queued',
           'dispatch_status': 'delivered', 'dispatch_error': None, 'geometry_revision': None,
           'processing_mode': 'saved_result', 'processing_profile_version': 'geometry-engine-inspections-v1'}
    session = {'schema_version': '0.1', 'session_id': sid, 'inspection_id': inspection['inspection_id'],
        'project_id': principal.project_id, 'device_id': 'recorded-import', 'room_id': inspection['room_id'],
        'model_version': inspection['model_version'], 'model_is_synthetic': inspection['model_is_synthetic'],
        'policy_version': inspection['policy_version'], 'capture_origin': snapshot['manifest']['capture_origin'],
        'input_mode': 'synthetic_fixture' if snapshot['manifest']['capture_origin']=='synthetic' else 'recorded_capture',
        'parent_session_id': None, 'created_at': utc_now(), 'revision': 1, 'capture_state': 'sealed',
        'processing_state': 'queued_geometry', 'device_acknowledged_at': None, 'capture_task': None,
        'completion': None, 'source': {'status': 'accepted', 'capture_ref': {'format': 'saved_geometry_result_v1',
            'source_job_id': remote['job_id'], 'manifest_key': snapshot['manifest_key']},
            'manifest_sha256': snapshot['manifest_sha256'], 'observation_count': snapshot['inventory']['observation_count'],
            'validation_error': None, 'validation_scope': 'saved_result_binding'},
        'geometry_job': job, 'display_view_id': None, 'result': None, 'error': None, 'geometry_result': None,
        'processing_scope': 'geometry_only_v1', 'storage': {'provider': 'supabase', 'storage_id': 'geometry-import',
            'key_prefix': sid+'/', 'project_url': snapshot['storage_origin'], 'bucket': snapshot['storage_bucket'], 'folder_status': 'ready'}}
    with service.database.transaction() as conn:
        row = conn.execute('SELECT data FROM sessions WHERE session_id=?', (sid,)).fetchone()
        if row:
            current = json.loads(row['data']); service._check_access(principal, current)
            if current['processing_state'] not in ('cancelled', 'completed', 'failed'):
                raise APIError(409, 'capture_in_progress', 'Another operation is processing this capture')
            # Preserve the replaced context as an audit receipt; never delete old jobs/results.
            service.database.save_receipt(conn, 'saved-result-predecessor:'+sid, plan['operation_id'], {}, current)
            session['created_at'] = current['created_at']; session['revision'] = current['revision'] + 1
            conn.execute('UPDATE sessions SET inspection_id=?,device_id=?,data=? WHERE session_id=?',
                         (inspection['inspection_id'], 'recorded-import', canonical_json(session), sid))
        else:
            conn.execute('INSERT INTO sessions(session_id,inspection_id,project_id,device_id,data,storage_ready) VALUES (?,?,?,?,?,1)',
                         (sid, inspection['inspection_id'], principal.project_id, 'recorded-import', canonical_json(session)))
        conn.execute('INSERT INTO geometry_jobs VALUES (?,?,?,?,?)', (job_id, sid, plan['operation_id'], canonical_json(command), canonical_json(job)))
        conn.execute("INSERT INTO work_items(work_id,session_id,kind,operation_id) VALUES (?,?,'geometry-poll',?)", ('poll:'+job_id, sid, job_id))
    return session
