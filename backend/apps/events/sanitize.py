"""
Defense-in-depth credential stripping. Gophish is configured
(capture_passwords=false) to never send a password field in the first
place — this exists anyway, per CLAUDE.md invariant #4, because that's
Gophish-side config, not something Django can verify from here, and a
cloned landing page with a non-native form could bypass it.

Applied to every webhook payload before it touches Event.metadata. Never
skip this because "Gophish already handles it."
"""

import json
import re

_SENSITIVE_KEY_PATTERN = re.compile(r"pass(word)?|credential|secret", re.IGNORECASE)


def strip_sensitive_fields(data):
    """Recursively removes any dict key that looks password/credential-like."""
    if isinstance(data, dict):
        return {
            key: strip_sensitive_fields(value)
            for key, value in data.items()
            if not _SENSITIVE_KEY_PATTERN.search(key)
        }
    if isinstance(data, list):
        return [strip_sensitive_fields(item) for item in data]
    if isinstance(data, str):
        # Gophish sometimes embeds submitted form data as a JSON-encoded
        # string (e.g. in a timeline entry's `details`) rather than a nested
        # object — try to sanitize inside it too.
        stripped = data.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return data
            return json.dumps(strip_sensitive_fields(parsed))
    return data
