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
