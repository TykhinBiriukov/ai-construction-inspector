"""Server-owned shortcuts to existing demo conversations, never new analysis."""
import json
from uuid import UUID
from .errors import APIError

MODES = (('geometry', 'Photo ↔ 3D'), ('drawing', 'Photo ↔ drawing'), ('monitoring', 'Stage monitoring · 4D'))


def validate_modes(value):
    try:
        mapping = json.loads(value)
        if not isinstance(mapping, dict) or set(mapping) - {key for key, _ in MODES}:
            raise ValueError
        for chat in mapping.values():
            if str(UUID(chat)) != chat:
                raise ValueError
        if len(set(mapping.values())) != len(mapping):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Demo modes must map known mode names to distinct canonical chat UUIDs') from None


def available(service, principal):
    mapping = json.loads(service.settings.demo_mode_chats_json)
    modes = []
    with service.database.connection() as conn:
        for key, label in MODES:
            chat_id = mapping.get(key)
            if chat_id:
                try:
                    service.workspace.check(conn, principal, chat_id)
                except APIError as error:
                    if error.status_code not in (403, 404):
                        raise
                    chat_id = None
            modes.append({'id': key, 'label': label, 'chat_id': chat_id})
    return {'modes': modes}
