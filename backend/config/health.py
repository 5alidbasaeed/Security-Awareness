"""
Phase 0 exit-criterion check: proves Django can actually reach Postgres and
Redis through the Docker network wiring, not just that the container boots.
"""

import redis
from django.conf import settings
from django.db import connections
from django.http import JsonResponse


def healthz(request):
    checks = {}

    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    try:
        redis.Redis.from_url(settings.CELERY_BROKER_URL).ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001 - health endpoint reports any failure verbatim
        checks["redis"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse({"healthy": healthy, "checks": checks}, status=200 if healthy else 503)
