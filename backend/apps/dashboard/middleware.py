from .access import CONTENT_SECURITY_POLICY

# URL namespaces rendered with the dashboard chrome (and so held to its strict CSP).
DASHBOARD_APPS = {"dashboard", "reporting", "manage"}


class DashboardSecurityHeadersMiddleware:
    """
    Adds a strict CSP to dashboard responses only. Django's own admin relies on
    inline scripts/styles, so the policy must not leak onto /admin/.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        if match is not None and match.app_name in DASHBOARD_APPS:
            response["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            response["Referrer-Policy"] = "same-origin"
            response["X-Content-Type-Options"] = "nosniff"
        return response
