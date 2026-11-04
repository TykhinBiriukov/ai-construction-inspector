"""Persistent browser-only operator sessions; never return the operator secret."""
import hashlib
import hmac
import re
import secrets
import time

COOKIE = "__Host-inspector-session"
MAX_AGE = 30 * 24 * 60 * 60


def cookie_settings(settings, *, public=False):
    """Keep hosted __Host cookies; HTTP is permitted only by the loopback profile."""
    secure = settings.browser_origin.startswith('https://')
    suffix = 'public' if public else 'session'
    name = ('__Host-inspector-' if secure else 'inspector-local-') + suffix
    return name, secure


def mint(secret):
    payload = f"{int(time.time()) + MAX_AGE}.{secrets.token_hex(16)}"
    signature = hmac.new(secret.encode(), ("browser-session:" + payload).encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def verify(secret, value):
    match = re.fullmatch(r"(\d{10})\.([a-f0-9]{32})\.([a-f0-9]{64})", value or "")
    if not match or int(match[1]) <= time.time():
        return False
    payload = match[1] + "." + match[2]
    expected = hmac.new(secret.encode(), ("browser-session:" + payload).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, match[3])
