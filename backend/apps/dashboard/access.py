"""
Who may open the dashboard, and the browser-side hardening every dashboard
page gets. Same rule as the /analytics/ API: staff with view access to risk
data. Row scoping (Department Managers) lives in scope.py.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

# Everything is self-hosted (htmx, Chart.js, our CSS/JS), so nothing external is
# ever allowed and no inline script or style is needed.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def dashboard_access(view):
    @wraps(view)
    @login_required
    @require_GET
    @never_cache
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not (user.is_staff and user.has_perm("risk_scoring.view_riskscoresnapshot")):
            return render(request, "dashboard/403.html", status=403)
        return view(request, *args, **kwargs)

    return wrapper
