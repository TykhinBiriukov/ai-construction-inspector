"""Portable references for stage reports, with legacy absolute-path readers."""
from pathlib import Path, PureWindowsPath


def artifact_reference(path, session):
    path, session = Path(path).resolve(), Path(session).resolve()
    return path.relative_to(session).as_posix() if path.is_relative_to(session) else str(path)


def resolve_artifact_reference(value, session, *, allow_external=False):
    if not isinstance(value, str) or not value:
        raise ValueError('Expected a nonempty artifact reference')
    session = Path(session).resolve()
    candidate = Path(value)
    if not candidate.is_absolute() and PureWindowsPath(value).drive:
        raise ValueError('Artifact reference belongs to another filesystem')
    if not candidate.is_absolute():
        if '..' in candidate.parts:
            raise ValueError('Artifact reference must stay inside the session')
        candidate = session / candidate
    candidate = candidate.resolve()
    if not allow_external and not candidate.is_relative_to(session):
        raise ValueError('Artifact reference must stay inside the session')
    return candidate
