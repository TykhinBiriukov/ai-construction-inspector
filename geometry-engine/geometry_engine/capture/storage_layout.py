"""Recognize session-scoped capture manifests in short and phone storage layouts."""
import re


IDENTIFIER = r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}'


def capture_manifest_location(key: str, session_id: str) -> tuple[str, str, bool]:
    """Return storage prefix, filename under manifests/, and short-layout flag."""
    if not isinstance(key, str) or not isinstance(session_id, str):
        raise ValueError('Invalid manifest path or session identity')
    match = re.fullmatch(
        rf'(?P<prefix>(?:standalone/{IDENTIFIER}/{IDENTIFIER}/)?(?P<session>{IDENTIFIER}))'
        rf'/manifests/(?P<name>[a-f0-9]{{64}}/capture-manifest\.json)', key)
    if match is None or match['session'] != session_id:
        raise ValueError('Invalid capture manifest path or session identity')
    return match['prefix'], match['name'], match['prefix'] == session_id
