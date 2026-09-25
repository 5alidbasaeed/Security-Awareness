from django.contrib import admin
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from .health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    # Browsers (and the Django admin, which declares no icon) ask for /favicon.ico directly.
    path("favicon.ico", RedirectView.as_view(url=static("dashboard/favicon.svg"), permanent=True)),
    path("webhooks/", include("apps.events.urls")),
    path("analytics/", include("apps.risk_scoring.urls")),
    path("reports/", include("apps.reporting.urls")),
    path("api/", include("apps.api.urls")),
    path("manage/", include("apps.manage.urls")),
    path("portal/", include("apps.portal.urls")),
    path("", include("apps.dashboard.urls")),
]
