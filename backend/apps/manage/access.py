"""
Access control for the management UI. Same shape as apps.dashboard.access, but
each view also needs a specific Django model permission — the management UI never
grants more than a role's permissions already allow, and row scoping (Department
Managers) is inherited by querying through core.scoping, exactly like the
read-only dashboard and the API.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods


def manage_access(*perms, methods=("GET", "POST")):
    """Require login, staff, and every listed permission. 403 page otherwise."""

    def decorator(func):
        @wraps(func)
        @login_required
        @require_http_methods(list(methods))
        @never_cache
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_staff or not all(user.has_perm(p) for p in perms):
                return render(request, "dashboard/403.html", status=403)
            return func(request, *args, **kwargs)

        return wrapper

    return decorator


def org_wide_content(func):
    """
    Email templates, landing pages, images, the template catalog and smart groups are shared by the
    whole organisation. A row-scoped Department Manager may read them (to pick one for their own
    campaign) but not change them: an edit would alter other departments' campaigns and, since an
    approval covers content, send those back to Draft. Reads pass through; changes are a 403.
    """

    @wraps(func)
    def wrapper(request, *args, **kwargs):
        from apps.core.scoping import managed_departments

        if request.method not in ("GET", "HEAD") and managed_departments(request.user) is not None:
            return render(request, "dashboard/403.html", status=403)
        return func(request, *args, **kwargs)

    return wrapper
