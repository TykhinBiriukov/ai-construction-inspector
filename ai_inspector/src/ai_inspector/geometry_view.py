"""Read-only, chat-scoped presentation of accepted Geometry and review evidence.

No model calls, capture dispatch, new verdicts or browser-visible upstream URLs.
"""
import hashlib
import json
from urllib.parse import urlsplit

import httpx

from .discrepancy import InspectionReport
from .errors import APIError
from .geometry_review import identity
from .service import require_role


def image_identity(entry):
    return hashlib.sha256((entry['stage'] + '/' + entry['path'] + ':' + entry['sha256']).encode()).hexdigest()


class GeometryView:
    def __init__(self, service):
        self.service = service

    def context(self, principal, chat_id, session_id=None):
        require_role(principal, 'operator')
        with self.service.database.connection() as conn:
            chat = self.service.agent._chat(conn, principal, chat_id)
            sid = session_id or chat.get('session_id')
            if not sid:
                raise APIError(404, 'geometry_view_not_found', 'No capture is linked to this conversation')
            states = self.service.geometry_review.states(conn, sid, chat_id)
            runs = [] if sid == chat.get('session_id') or states else conn.execute(
                'SELECT data FROM agent_runs WHERE chat_id=? ORDER BY sequence DESC LIMIT 200', (chat_id,)).fetchall()
            linked = sid == chat.get('session_id') or states or any(json.loads(r['data']).get('session_id') == sid for r in runs)
            if not linked:
                raise APIError(404, 'geometry_view_not_found', 'Capture not found in this conversation')
            session = self.service._load(conn, 'sessions', sid)
            self.service._check_access(principal, session)
            inspection = self.service._load(conn, 'inspections', session['inspection_id'])
            self.service._check_access(principal, inspection)
            result = session.get('geometry_result') or {}
            if result.get('adapter') == 'inspections_v1':
                source = session.get('source') or {}
                if (source.get('status') != 'accepted' or result.get('session_id') != sid
                        or result.get('project_id') != principal.project_id
                        or result.get('manifest_sha256') != source.get('manifest_sha256')
                        or result.get('model_sha256') != (inspection.get('bim_model') or {}).get('sha256')
                        or result.get('model_version') != session.get('model_version')
                        or result.get('job_id') != (session.get('geometry_job') or {}).get('job_id')):
                    raise APIError(409, 'inspection_source_mismatch', 'The view belongs to a different accepted capture or model')
        return session, inspection, next(iter(states.values()), None)

    def private(self, session):
        result = session.get('geometry_result')
        if not result or result.get('adapter') != 'inspections_v1':
            return {'photo_files': {}, 'artifacts': []}
        return self.service.geometry_engine.private_files(result['job_id'])

    def snapshot(self, principal, chat_id, session_id=None):
        session, inspection, state = self.context(principal, chat_id, session_id)
        result = session.get('geometry_result') or {}
        report = result.get('report') or {}
        private = self.private(session)
        photos = private['photo_files']
        views_by_owner, legacy_by_owner, image_hashes, hash_counts = {}, {}, {}, {}
        for entry in photos.values():
            image_id = image_identity(entry)
            image_hashes[image_id] = entry['sha256']
            hash_counts[entry['sha256']] = hash_counts.get(entry['sha256'], 0) + 1
            if not entry.get('photo_refs'):
                for owner in entry.get('object_ids', []):
                    legacy_by_owner.setdefault(owner, []).append(entry)
            for ref in entry.get('photo_refs', []):
                views_by_owner.setdefault((ref['object_id'], ref['frame_id']), {})[ref['role']] = {
                    'image_id': image_id, 'byte_length': entry['size'], 'used_in_review': False}
        observations, by_source = [], {}
        for kind in ('objects', 'surfaces'):
            for index, source in enumerate(report.get(kind, [])):
                row = {'id': (('object:' + source['id']) if kind == 'objects' else 'surface:' + str(index)),
                       'kind': 'object' if kind == 'objects' else 'surface', 'source': source,
                       'decision': None, 'case_id': None, 'frames': []}
                if kind == 'objects':
                    for frame in source.get('photos', []):
                        views = {role: dict(view) for role, view in views_by_owner.get((source['id'], frame['frame_id']), {}).items()}
                        row['frames'].append({'frame_id': frame['frame_id'], 'views': views})
                    for entry in legacy_by_owner.get(source['id'], []):
                        row['frames'].append({'frame_id': None, 'views': {'unreported': {
                            'image_id': image_identity(entry), 'byte_length': entry['size'], 'used_in_review': None}}})
                observations.append(row)
                by_source[id(source)] = row
        # The saved projection controls ordering/case IDs; legacy workflows keep
        # their original mapping. Only server-accepted reports can supply decisions.
        if state and state.get('status') != 'cancelled' and state.get('packages'):
            candidates = self.service.geometry_review.candidates(report, projection_version=state.get('projection_version', 1))
            rows_by_case = {identity(state['review_id'], 'case:' + str(i)): by_source[id(source)]
                            for i, (_, _, source) in enumerate(candidates)}
            for value in state.get('reports', []):
                reviewed = InspectionReport.model_validate(value)
                source = reviewed.source
                if (reviewed.project_id != principal.project_id or str(reviewed.chat_id) != chat_id
                        or str(source.session_id) != session['session_id'] or str(source.geometry_job_id) != result.get('job_id')
                        or source.manifest_sha256 != (session.get('source') or {}).get('manifest_sha256')
                        or source.model_sha256 != result.get('model_sha256')
                        or source.geometry_revision != result.get('geometry_revision')):
                    raise APIError(409, 'inspection_source_mismatch', 'The review belongs to a different Geometry source')
                assets = {a.asset_id: a for a in reviewed.assets}
                for category, decision in reviewed.cases.rows():
                    row = rows_by_case.get(decision.case_id)
                    if row is None:
                        raise APIError(409, 'inspection_source_mismatch', 'The review case has no saved Geometry correspondence')
                    row.update(case_id=decision.case_id, category=category,
                               decision=decision.model_dump(mode='json'),
                               measurements=[m.model_dump(mode='json') for m in reviewed.measurements.get(decision.case_id, [])])
                    used = {assets[aid].sha256 for aid in decision.evidence_used if aid in assets}
                    for frame in row['frames']:
                        for view in frame['views'].values():
                            digest = image_hashes[view['image_id']]
                            view['used_in_review'] = digest in used if hash_counts[digest] == 1 else None
        limit = self.model_limit
        artifacts = [{key: a[key] for key in ('artifact_id', 'byte_length', 'sha256')} |
                     {'available': a['byte_length'] <= limit} for a in result.get('artifacts', [])]
        model = inspection.get('bim_model')
        if model and not any(a['artifact_id'] == 'ifc' for a in artifacts):
            artifacts.insert(0, {'artifact_id': 'ifc', 'byte_length': model['byte_length'],
                                 'sha256': model['sha256'], 'available': model['byte_length'] <= limit})
        return {'schema_version': '0.1', 'chat_id': chat_id, 'session_id': session['session_id'],
                'geometry_job_id': result.get('job_id'), 'processing_state': session['processing_state'],
                'review_status': state.get('status') if state else 'not_requested',
                'result_file_id': state.get('result_file_id') if state else None,
                'origin': {'analysis_origin': result.get('analysis_origin'), 'capture_origin': session.get('capture_origin'),
                           'is_synthetic': bool(result.get('is_synthetic') or result.get('model_is_synthetic'))},
                'comparison': {k: report.get(k) for k in ('units', 'difference_convention', 'tolerance', 'summary')},
                'acceptance_tolerance_m': state['request'].get('acceptance_tolerance_m') if state else None,
                'alignment': {'available': False, 'reason': 'Scan-to-IFC transform is not supplied by the accepted compact result'},
                'observations': observations, 'artifacts': artifacts,
                'limitations': result.get('limitations', [])}

    @property
    def model_limit(self):
        return min(self.service.settings.max_capture_file_bytes, 32 * 1024**2)

    def image(self, principal, chat_id, session_id, image_id):
        session, _, _ = self.context(principal, chat_id, session_id)
        matches = [(url, entry) for url, entry in self.private(session)['photo_files'].items()
                   if image_identity(entry) == image_id]
        if not matches:
            raise APIError(404, 'geometry_image_not_found', 'This image is not part of the accepted capture')
        url, entry = matches[0]
        command = self.remote_command(session)
        self.check_origin(url, command, session)
        data = self.service.geometry_review.photo(session, url, entry)
        if data.startswith(b'\xff\xd8\xff'):
            media = 'image/jpeg'
        elif data.startswith(b'\x89PNG\r\n\x1a\n'):
            media = 'image/png'
        elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            media = 'image/webp'
        else:
            raise APIError(422, 'geometry_image_invalid', 'The evidence file is not a supported image')
        return data, media

    def artifact(self, principal, chat_id, session_id, artifact_id):
        session, inspection, _ = self.context(principal, chat_id, session_id)
        if artifact_id not in ('ifc', 'mesh', 'segmented_mesh', 'point_cloud'):
            raise APIError(404, 'geometry_artifact_not_found', 'Model not found')
        if artifact_id == 'ifc':
            model = inspection.get('bim_model')
            if not model:
                raise APIError(404, 'geometry_artifact_not_found', 'No reference model is pinned')
            if session.get('geometry_result') and session['geometry_result'].get('model_sha256') != model['sha256']:
                raise APIError(409, 'inspection_source_mismatch', 'The pinned reference model changed')
            if model['byte_length'] > self.model_limit:
                raise APIError(413, 'geometry_view_size_limit', 'This model exceeds the browser preview size limit')
            try:
                with self.service.bim_store.path_for(model['sha256']).open('rb') as stream:
                    data = stream.read(model['byte_length'] + 1)
            except OSError:
                raise APIError(503, 'geometry_view_unavailable', 'The pinned reference model is temporarily unavailable', retryable=True) from None
            self.verify(data, model['byte_length'], model['sha256'])
            return data
        private = self.private(session)
        descriptor = next((a for a in private['artifacts'] if a['artifact_id'] == artifact_id), None)
        if descriptor is None:
            raise APIError(404, 'geometry_artifact_not_found', 'This model is not available in the accepted result')
        if descriptor['byte_length'] > self.model_limit:
            raise APIError(413, 'geometry_view_size_limit', 'This model exceeds the browser preview size limit')
        result = session['geometry_result']
        command = self.remote_command(session)
        adapter = self.service.geometry_engine
        url = descriptor['url']
        self.check_origin(url, command, session)
        try:
            with httpx.Client(transport=getattr(self.service, 'geometry_transport', None),
                              timeout=self.service.settings.geometry_timeout_s, follow_redirects=False) as client:
                for attempt in range(2):
                    with client.stream('GET', url, headers=adapter.auth_headers()) as response:
                        if response.status_code in (401, 403) and attempt == 0:
                            _, fresh = adapter.poll(session, command, client)
                            if (fresh is None or fresh['result_manifest_sha256'] != result['result_manifest_sha256']
                                    or fresh['report'] != result['report']):
                                raise APIError(409, 'inspection_asset_changed', 'The saved Geometry result changed')
                            new = next((a for a in fresh['artifacts'] if a['artifact_id'] == artifact_id), {})
                            if any(new.get(k) != descriptor[k] for k in ('sha256', 'byte_length')):
                                raise APIError(409, 'inspection_asset_changed', 'The model identity changed')
                            url = new['url']
                            continue
                        if response.status_code != 200:
                            raise APIError(503, 'geometry_view_unavailable', 'The model is temporarily unavailable', retryable=True)
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > descriptor['byte_length']:
                                raise APIError(409, 'inspection_asset_changed', 'The model length changed')
                    self.verify(data, descriptor['byte_length'], descriptor['sha256'])
                    return bytes(data)
        except httpx.HTTPError:
            raise APIError(503, 'geometry_view_unavailable', 'The model is temporarily unavailable', retryable=True) from None

    def remote_command(self, session):
        result = session['geometry_result']
        with self.service.database.connection() as conn:
            row = conn.execute('SELECT command_body FROM geometry_jobs WHERE job_id=?', (result['job_id'],)).fetchone()
        if not row or not row['command_body']:
            raise APIError(409, 'geometry_context_mismatch', 'The saved Geometry command is unavailable')
        command = json.loads(row['command_body'])
        adapter = self.service.geometry_engine
        adapter.require_current_command(command)
        adapter.check_config(command)
        if any(command.get(k) != result.get(k) for k in ('job_id', 'session_id', 'project_id', 'manifest_sha256', 'model_sha256')):
            raise APIError(409, 'geometry_context_mismatch', 'The model command belongs to another source')
        return command

    @staticmethod
    def check_origin(url, command, session):
        parsed, base = urlsplit(url), urlsplit(command['base_url'])
        prefix = base.path.rstrip('/') + '/inspections/' + session['geometry_result']['remote_job_id'] + '/result/files/'
        if (parsed.scheme != base.scheme or parsed.netloc != base.netloc or parsed.username or parsed.password
                or parsed.fragment or not parsed.path.startswith(prefix)):
            raise APIError(409, 'inspection_asset_changed', 'The evidence origin differs from the accepted job')

    @staticmethod
    def verify(data, size, digest):
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise APIError(409, 'inspection_asset_changed', 'The model failed integrity verification')
