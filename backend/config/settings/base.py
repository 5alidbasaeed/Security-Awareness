"""
Shared Django settings. Environment-specific overrides live in dev.py / prod.py —
see the backend-conventions project skill for the rationale (one settings module
per environment rather than scattered DEBUG branching).
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR.parent / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Project apps — see the backend-conventions skill for what each owns.
    "apps.core",
    "apps.employees",
    "apps.campaigns",
    "apps.events",
    "apps.training",
    "apps.risk_scoring",
    "apps.engine",
    "apps.dashboard",
    "apps.reporting",
    "apps.api",
    "apps.manage",
    "apps.portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.dashboard.middleware.DashboardSecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database: Postgres only. Gophish's MySQL database is never configured or
# queried here — see CLAUDE.md invariant #2. ---
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Served by WhiteNoise from the Django container itself — the dashboard has no
# CDN or external asset host (VPN-only deployment, CLAUDE.md invariant #8).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Celery ---
CELERY_BROKER_URL = env("REDIS_URL")
CELERY_RESULT_BACKEND = env("REDIS_URL")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

# Reconciliation fallback per CLAUDE.md invariant #3 — webhooks are the
# primary event-ingestion path; this exists only to catch a missed delivery.
# Without this schedule, apps.events.tasks.reconcile_campaign is dead code
# that never runs on its own.
CELERY_BEAT_SCHEDULE = {
    "reconcile-active-campaigns": {
        "task": "apps.events.tasks.reconcile_all_active_campaigns",
        "schedule": 900.0,  # every 15 minutes
    },
    "send-training-reminders": {
        "task": "apps.training.tasks.send_training_reminders",
        "schedule": 3600.0,  # hourly — the task itself enforces the 24h-per-assignment cooldown
    },
    # Scores decay with time, so they move even with no new events — recompute daily.
    # Event-triggered recomputes (risk_scoring.signals) cover the immediate updates.
    "recompute-risk-scores": {
        "task": "apps.risk_scoring.tasks.recompute_all_scores",
        "schedule": 86400.0,
    },
    # Standing monthly archive: last month's org-wide executive summary (idempotent).
    "monthly-executive-summary": {
        "task": "apps.reporting.tasks.generate_monthly_executive_summary",
        "schedule": 86400.0,
    },
    "launch-scheduled-campaigns": {
        "task": "apps.campaigns.tasks.launch_scheduled_campaigns",
        "schedule": 300.0,  # every 5 minutes
    },
}

# Launched campaigns are reconciled against Gophish for this many days after launch, then left alone.
RECONCILE_WINDOW_DAYS = env.int("RECONCILE_WINDOW_DAYS", default=30)

# --- Email (training reminders) ---
# Console backend by default — real SMTP via env override on a real
# deployment, same pattern as DJANGO_SETTINGS_MODULE per-environment.
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="noreply@example.com")

# --- Training ---
TRAINING_DUE_DAYS = env.int("TRAINING_DUE_DAYS", default=14)

# --- Employee training portal (Phase 6.1) ---
# Absolute base URL employees reach the portal on (used in emailed sign-in links).
PORTAL_BASE_URL = env("PORTAL_BASE_URL", default="http://localhost:8000")
PORTAL_LINK_MAX_AGE_SECONDS = env.int("PORTAL_LINK_MAX_AGE_SECONDS", default=72 * 3600)

# Anonymous hits on staff-only pages (the dashboard, the /analytics/ API) go to the dashboard login.
LOGIN_URL = "dashboard:login"
LOGIN_REDIRECT_URL = "dashboard:overview"
LOGOUT_REDIRECT_URL = "dashboard:login"

# --- Campaign launch rate limiting (Phase 3) ---
# Applies to both the admin "Launch" action and the scheduler — see
# apps/campaigns/services.py::launch_campaign(), the one shared launch path.
CAMPAIGN_LAUNCH_RATE_LIMIT = env.int("CAMPAIGN_LAUNCH_RATE_LIMIT", default=5)

# --- Gophish adapter config (consumed by apps.engine.GophishClient, Phase 1) ---
GOPHISH_API_URL = env("GOPHISH_API_URL", default="")
GOPHISH_API_KEY = env("GOPHISH_API_KEY", default="")
GOPHISH_WEBHOOK_SECRET = env("GOPHISH_WEBHOOK_SECRET", default="")
GOPHISH_DEFAULT_SEND_PROFILE = env("GOPHISH_DEFAULT_SEND_PROFILE", default="default")
