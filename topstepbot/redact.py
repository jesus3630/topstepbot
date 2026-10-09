"""Strip API keys and session tokens from text and JSON before they are shown or saved."""

from __future__ import annotations

from collections.abc import Iterable

_SECRET_MARKERS = ("apikey", "token", "password", "secret", "authorization")


def is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "").replace("_", "")
    return any(marker in lowered for marker in _SECRET_MARKERS)


def redact_text(text: str, secrets: Iterable[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in redacted:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def redact(value, secrets: Iterable[str]):
    """Return a copy with secret field names and known secret strings removed."""
    known = [secret for secret in secrets if secret]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if is_secret_key(str(key)):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact(item, known)
        return cleaned
    if isinstance(value, list):
        return [redact(item, known) for item in value]
    if isinstance(value, str):
        return redact_text(value, known)
    return value
