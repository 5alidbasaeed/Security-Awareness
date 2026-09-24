from .base import *  # noqa: F403

DEBUG = False

# Django's admin/dashboard is internal/VPN-restricted at the network layer (see
# CLAUDE.md invariant #8) — this is defense in depth on top of that, not a
# substitute for it.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS is opt-in: enabling it before HTTPS works end to end can lock browsers out for the whole
# duration. Set DJANGO_SECURE_HSTS_SECONDS (e.g. 31536000) once TLS is confirmed.
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=0)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
# Behind a TLS-terminating proxy the app only sees HTTP, so SECURE_SSL_REDIRECT would loop forever
# unless it trusts the proxy's X-Forwarded-Proto. Opt in ONLY when the proxy sets/overwrites that header.
if env.bool("DJANGO_BEHIND_TLS_PROXY", default=False):  # noqa: F405
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
