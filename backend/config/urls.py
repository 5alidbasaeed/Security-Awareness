from django.contrib import admin
from django.urls import include, path

from .health import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("webhooks/", include("apps.events.urls")),
    path("analytics/", include("apps.risk_scoring.urls")),
    path("reports/", include("apps.reporting.urls")),
    path("api/", include("apps.api.urls")),
    path("manage/", include("apps.manage.urls")),
    path("portal/", include("apps.portal.urls")),
    path("", include("apps.dashboard.urls")),
]
