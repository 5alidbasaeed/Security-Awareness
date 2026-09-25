"""
Who may open the dashboard, and the browser-side hardening every dashboard
page gets. Same rule as the /analytics/ API: staff with view access to risk
data. Row scoping (Department Managers) lives in scope.py.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

# Everything is self-hosted (htmx, Chart.js, our CSS/JS), so nothing external is
# ever allowed and no inline script or style is needed.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


RISK_PERMISSION = "risk_scoring.view_riskscoresnapshot"


def dashboard_access(view=None, *, methods=("GET",), also=()):
    """
    Use bare (`@dashboard_access`, GET only) or `@dashboard_access(methods=("GET", "POST"))`.
    `also` names extra permissions that open this one page without risk-data access (the
    Training page for a Training Manager, who owns training but may not see risk scores).
    """

    def decorator(func):
        @wraps(func)
        @login_required
        @require_http_methods(list(methods))
        @never_cache
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not (user.is_staff and any(user.has_perm(p) for p in (RISK_PERMISSION, *also))):
                return render(request, "dashboard/403.html", status=403)
            return func(request, *args, **kwargs)

        return wrapper

    return decorator(view) if view is not None else decorator
