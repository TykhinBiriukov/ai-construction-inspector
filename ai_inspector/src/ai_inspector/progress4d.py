"""Read-only, chat-scoped bridge to the separately hosted 4D service."""
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi.responses import Response

from .errors import APIError


def validate_config(settings):
    if bool(settings.progress4d_base_url) != bool(settings.progress4d_service_token):
        raise ValueError('Configure both Progress4D endpoint and dedicated token')
    if settings.progress4d_base_url:
        parsed = urlsplit(settings.progress4d_base_url)
        if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Progress4D requires HTTPS or loopback HTTP')
    try:
        mapping = json.loads(settings.progress4d_chat_projects_json)
        if not isinstance(mapping, dict):
            raise ValueError
        for chat, project in mapping.items():
            if str(UUID(chat)) != chat or str(UUID(project)) != project:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Progress4D mapping must contain canonical chat UUID to project UUID entries') from None


def project_for_chat(service, principal, chat_id):
    # Check ownership even when the integration is disabled or the mapping is absent.
    with service.database.connection() as conn:
        service.workspace.check(conn, principal, chat_id)
    settings = service.settings
    if settings.saved_demo_dir:
        from pathlib import Path
        return json.loads((Path(settings.saved_demo_dir) / 'timeline.json').read_text())['project_id']
    if not settings.progress4d_base_url or not settings.progress4d_service_token:
        raise APIError(503, 'progress4d_unavailable', '4D service is not configured yet.')
    project = json.loads(settings.progress4d_chat_projects_json).get(chat_id)
    if not project:
        raise APIError(404, 'progress4d_not_linked', 'No 4D result is linked to this conversation yet.')
    return project


async def read_response(settings, project, path, byte_range=None, *, transport=None):
    # Never proxy arbitrary upstream paths, writes, URL query strings or headers.
    artifact = re.fullmatch(r'jobs/([0-9a-f-]{36})/artifacts/([A-Za-z0-9_.-]{1,160})', path)
    if path not in ('timeline', 'report') and not artifact:
        raise APIError(404, 'not_found', 'Unknown 4D resource')
    if settings.saved_demo_dir:
        from pathlib import Path
        from fastapi.responses import FileResponse
        root = Path(settings.saved_demo_dir)
        manifest = json.loads((root / 'progress-assets.json').read_text())
        if path in ('timeline', 'report'):
            return FileResponse(root / 'timeline.json', media_type='application/json', headers={'Cache-Control':'private, no-store'})
        if path not in manifest:
            raise APIError(404, 'not_found', 'Unknown saved artifact')
        entry = manifest[path]
        return FileResponse(root / entry['file'], media_type=entry['mime'], headers={'Cache-Control':'private, no-store'})
    headers = {'Authorization': 'Bearer ' + settings.progress4d_service_token}
    if byte_range:
        match = re.fullmatch(r'bytes=(\d{1,12})-(\d{1,12})', byte_range)
        if not artifact or not match or not 0 <= int(match[2]) - int(match[1]) < 2 * 1024 * 1024:
            raise APIError(416, 'invalid_range', 'Use a byte range of at most 2 MiB')
        headers['Range'] = byte_range
    url = settings.progress4d_base_url.rstrip('/') + f'/v1/projects/{project}/{path}'
    limit = 2 * 1024 * 1024 if byte_range else 16 * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False, transport=transport) as client:
            async with client.stream('GET', url, headers=headers) as response:
                if response.status_code not in (200, 206):
                    status = response.status_code if response.status_code in (404, 416) else 502
                    raise APIError(status, 'progress4d_read_failed', 'Could not read the 4D result. Try again.')
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > limit:
                        raise APIError(502, 'progress4d_too_large', '4D resource exceeds the transfer limit')
                allowed = ('content-type', 'content-range', 'accept-ranges', 'content-disposition')
                outgoing = {name: response.headers[name] for name in allowed if name in response.headers}
                outgoing.update({'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
                return Response(bytes(content), status_code=response.status_code, headers=outgoing)
    except httpx.HTTPError:
        raise APIError(502, 'progress4d_unavailable', '4D service is temporarily unavailable.') from None


def agent_result(service, principal, chat_id, expected_project, *, transport=None):
    """Read the bound saved result; never treat new attachments as processed input."""
    project = project_for_chat(service, principal, chat_id)
    if project != expected_project:
        raise APIError(409, 'progress4d_binding_changed', 'The monitoring project binding changed. Submit a new request.')
    settings = service.settings
    try:
        if settings.saved_demo_dir:
            from pathlib import Path
            value=json.loads((Path(settings.saved_demo_dir)/'timeline.json').read_text())
        else:
            with httpx.Client(timeout=30, follow_redirects=False, transport=transport) as client:
                with client.stream('GET', settings.progress4d_base_url.rstrip('/') + f'/v1/projects/{project}/timeline',
                                   headers={'Authorization': 'Bearer ' + settings.progress4d_service_token}) as response:
                    if response.status_code != 200:
                        raise APIError(502, 'progress4d_unavailable', 'The connected monitoring result is unavailable', retryable=True)
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 16 * 1024 * 1024:
                            raise ValueError
            value = json.loads(content)
        if value.get('project_id') != project or not isinstance(value.get('surveys'), list):
            raise ValueError
        report = value.get('report') or {}
        surveys = value['surveys']
        tasks = [{k: v for k, v in task.items() if k != 'evidence'} for task in report.get('tasks', [])]
        snapshots = report.get('snapshots') or []
        latest = [{k: v for k, v in task.items() if k not in ('evidence', 'known_present_product_ids', 'observed_removals')} for task in (snapshots[-1].get('tasks', []) if snapshots else [])]
        return {'latest_progress': latest, 'project_id': project, 'source': 'saved_monitoring_result', 'new_analysis': False,
                'survey_count': len(surveys), 'as_of': report.get('as_of'), 'tasks': tasks,
                'surveys': [{'captured_at': s['captured_at'], 'status': s['status'],
                             'source_kind': s.get('source_kind'),
                             'visualization': (s.get('result') or {}).get('visualization')} for s in surveys],
                'limitations': report.get('limitations', []),
                'viewer': {'kind': 'progress4d', 'chat_id': chat_id},
                'input_notice': 'These are previously processed project observations, not analysis of new uploads or external folder links.'}
    except httpx.HTTPError:
        raise APIError(503, 'progress4d_unavailable', 'Monitoring service is temporarily unavailable', retryable=True) from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise APIError(502, 'progress4d_invalid_result', 'Monitoring service returned an invalid result') from None
