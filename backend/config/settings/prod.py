from .base import *  # noqa: F401,F403

DEBUG = False

# Django's admin/dashboard is internal/VPN-restricted at the network layer (see
# CLAUDE.md invariant #8) — this is defense in depth on top of that, not a
# substitute for it.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
