"""
Throttling for the one public, state-changing endpoint: requesting a portal sign-in link.
That form emails whoever it is asked about, so unthrottled it is an inbox-flooding tool
aimed at employees (and a way to tie up web workers on the mail relay).

Two independent limits, both counted in the shared cache (Redis in real runs, so every
gunicorn worker sees the same counters):
  * per address  — a person only ever needs a couple of links an hour;
  * per client   — one source can't spray the whole staff list.
Every attempt is counted, including unknown addresses, so probing is limited too.
Going over a limit changes nothing the caller can see: the response is identical and the
email is simply not sent (no oracle for "was that address real?").
"""

import hashlib

from django.conf import settings
from django.core.cache import cache


def _hit(key: str, window: int) -> int:
    """Atomically count one event under `key`; returns how many have happened in the window."""
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # the key expired between add() and incr()
        cache.set(key, 1, window)
        return 1


def link_request_allowed(request, email: str) -> bool:
    window = settings.PORTAL_LINK_WINDOW_SECONDS
    # REMOTE_ADDR by default: a client can forge X-Forwarded-For. Behind a proxy every request would share the
    # proxy's address (one bucket for everyone), so a deployment can name the ONE header its proxy overwrites.
    client = request.META.get(settings.PORTAL_CLIENT_IP_HEADER or "REMOTE_ADDR") or request.META.get("REMOTE_ADDR", "unknown")
    client_ok = _hit(f"portal:link:client:{client}", window) <= settings.PORTAL_LINK_IP_LIMIT
    if not email:
        return client_ok
    # Only a hash is stored: the throttle must not become a list of employee addresses.
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    address_ok = _hit(f"portal:link:address:{digest}", window) <= settings.PORTAL_LINK_EMAIL_LIMIT
    return client_ok and address_ok
