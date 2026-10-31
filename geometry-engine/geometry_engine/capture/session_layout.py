"""Raw capture storage and processed stage storage, without copying raw files.

Manifest references keep the logical ``original/...`` namespace. Resolution maps
it to raw/ID/original (or a derived input snapshot) instead of requiring symlinks.
Legacy session directories remain readable.
"""
from pathlib import Path


def processed_session(directory):
    path = Path(directory).resolve()
    if path.parent.name == 'raw':
        path = path.parent.parent / 'processed' / path.name
    return path


def raw_session(directory):
    path = Path(directory).resolve()
    if path.parent.name == 'processed':
        return path.parent.parent / 'raw' / path.name
    return path


def original_directory(directory):
    session = processed_session(directory)
    # Explicit derived input snapshot, e.g. normalized cameras + reconstructed mesh.
    if (session / 'input').is_dir():
        root = (session / 'input').resolve()
        if not root.is_relative_to(session):
            raise ValueError('Derived inputs must stay inside the processed session')
        return root
    raw = raw_session(session)
    root = (raw / 'original').resolve()
    if not root.is_relative_to(raw):
        raise ValueError('Original inputs must stay inside the raw session')
    return root


def raw_original_directory(directory):
    raw = raw_session(processed_session(directory))
    root = (raw / 'original').resolve()
    if not root.is_relative_to(raw):
        raise ValueError('Original inputs must stay inside the raw session')
    return root


def prepare_session(directory):
    session = processed_session(directory)
    if not original_directory(session).is_dir():
        raise ValueError('Raw session original directory does not exist')
    session.mkdir(parents=True, exist_ok=True)
    return session


def session_reference(path, session):
    path, session = Path(path).resolve(), processed_session(session)
    original = original_directory(session)
    if path.is_relative_to(original):
        return 'original/' + path.relative_to(original).as_posix()
    return path.relative_to(session).as_posix()
