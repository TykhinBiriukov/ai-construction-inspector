"""Map Worker bindings to the shared, validated model/runtime settings."""

from ai_inspector.config import Settings


def _text(env, name, default):
    value = getattr(env, name, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a text binding")
    return value


def _number(env, name, default, kind):
    value = getattr(env, name, default)
    if value is None:
        value = default
    try:
        if isinstance(value, bool) or (kind is int and not isinstance(value, (str, int))):
            raise ValueError
        return kind(value)
    except (ValueError, TypeError, OverflowError):
        # Never include a binding's value in configuration errors or logs.
        raise ValueError(f"Invalid {name}") from None


def meta_settings(env):
    """Credentials remain in the Worker environment; shared Settings checks limits."""
    return {
        "meta_base_url": _text(env, "INSPECTOR_META_BASE_URL", Settings.meta_base_url),
        "meta_model": _text(env, "INSPECTOR_META_MODEL", Settings.meta_model),
        "meta_api_key": _text(env, "INSPECTOR_META_API_KEY", Settings.meta_api_key),
        "meta_timeout_s": _number(env, "INSPECTOR_META_TIMEOUT_S", Settings.meta_timeout_s, float),
        "agent_max_steps": _number(env, "INSPECTOR_AGENT_MAX_STEPS", Settings.agent_max_steps, int),
    }
