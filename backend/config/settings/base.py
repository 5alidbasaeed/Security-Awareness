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
    "apps.intake",
    "apps.engagement",
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
    "enforce-training-policies": {
        "task": "apps.training.tasks.enforce_training_policies",
        "schedule": 86400.0,
    },
    "escalate-overdue-training": {
        "task": "apps.training.tasks.escalate_overdue_training",
        "schedule": 86400.0,  # the task itself limits each department to one email per 7 days
    },
    "send-scheduled-reports": {
        "task": "apps.reporting.tasks.send_scheduled_reports",
        "schedule": 86400.0,
    },
    "anonymise-stale-employees": {
        "task": "apps.engagement.retention.anonymize_stale_employees",
        "schedule": 86400.0,
    },
    "lapse-expired-exemptions": {
        "task": "apps.employees.tasks.lapse_expired_exemptions",
        "schedule": 3600.0,  # hourly: an expired exemption is also ignored at launch, this just tidies and audits it
    },
    "launch-scheduled-campaigns": {
        "task": "apps.campaigns.tasks.launch_scheduled_campaigns",
        "schedule": 300.0,  # every 5 minutes
    },
}

# Launched campaigns are reconciled against Gophish for this many days after launch, then left alone.
RECONCILE_WINDOW_DAYS = env.int("RECONCILE_WINDOW_DAYS", default=30)

# --- Uploaded images for the email / landing-page builders ---
# Written here, served to recipients by nginx from a read-only mount of the same volume.
EMAIL_IMAGE_ROOT = env("EMAIL_IMAGE_ROOT", default="/srv/email-images")
# The PUBLIC address recipients' mail clients fetch images from (nginx's /i/ path on the sending domain).
EMAIL_IMAGE_BASE_URL = env("EMAIL_IMAGE_BASE_URL", default="http://localhost/i/")

# --- Cache (shared throttle counters) ---
# Redis, so every gunicorn worker sees the same counters. A separate database number keeps these
# keys out of Celery's broker/result keyspace.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_CACHE_URL", default=env("REDIS_URL").rsplit("/", 1)[0] + "/1"),
    }
}

# --- Portal sign-in link throttling (public endpoint; see apps/portal/throttle.py) ---
PORTAL_LINK_EMAIL_LIMIT = env.int("PORTAL_LINK_EMAIL_LIMIT", default=3)  # links per address per window
PORTAL_LINK_IP_LIMIT = env.int("PORTAL_LINK_IP_LIMIT", default=20)  # requests per client per window
PORTAL_LINK_WINDOW_SECONDS = env.int("PORTAL_LINK_WINDOW_SECONDS", default=3600)
# Only set when a proxy in front overwrites this header (e.g. HTTP_X_REAL_IP). Empty = use the socket address.
PORTAL_CLIENT_IP_HEADER = env("PORTAL_CLIENT_IP_HEADER", default="")

# --- Test sends of a composed email, and landing-page cloning ---
# A test send can only go to the sender's own address or one of these domains (comma-separated) —
# never to arbitrary outside addresses, even though it goes through the real relay.
TEST_EMAIL_ALLOWED_DOMAINS = env.list("TEST_EMAIL_ALLOWED_DOMAINS", default=[])
TEST_EMAIL_LIMIT_PER_HOUR = env.int("TEST_EMAIL_LIMIT_PER_HOUR", default=10)
# Development only: let the landing-page cloner fetch private/internal addresses (SSRF risk — never on in production).
LANDING_CLONE_ALLOW_PRIVATE_HOSTS = env.bool("LANDING_CLONE_ALLOW_PRIVATE_HOSTS", default=False)

# --- Email (training reminders) ---
# Console backend by default — real SMTP via env override on a real
# deployment, same pattern as DJANGO_SETTINGS_MODULE per-environment.
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="noreply@example.com")
# Without a timeout a slow or black-holed SMTP relay blocks a web worker until the OS gives up.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# --- Training ---
TRAINING_DUE_DAYS = env.int("TRAINING_DUE_DAYS", default=14)

# --- Employee training portal (Phase 6.1) ---
# Absolute base URL employees reach the portal on (used in emailed sign-in links).
PORTAL_BASE_URL = env("PORTAL_BASE_URL", default="http://localhost:8000")
PORTAL_LINK_MAX_AGE_SECONDS = env.int("PORTAL_LINK_MAX_AGE_SECONDS", default=72 * 3600)
# CSV import with "deactivate anyone not in the file" is refused if it would switch off more than this share of active staff.
IMPORT_MAX_DEACTIVATE_PERCENT = env.int("IMPORT_MAX_DEACTIVATE_PERCENT", default=10)

# --- Engagement & integrations (Phase 6.3) ---
# Coaching emails employees the moment they fail a simulation (defaults off — turn on once real SMTP works).
COACHING_ENABLED = env.bool("COACHING_ENABLED", default=False)
# Forward every event to a SIEM or a Slack/Teams incoming webhook. Blank = off.
SIEM_WEBHOOK_URL = env("SIEM_WEBHOOK_URL", default="")
SIEM_WEBHOOK_TIMEOUT = env.int("SIEM_WEBHOOK_TIMEOUT", default=5)
# Smallest department size that may be shown in aggregate analytics/reports (privacy — Phase 6.4).
MIN_REPORTING_COHORT = env.int("MIN_REPORTING_COHORT", default=1)
# Program targets the dashboard grades against (a policy decision — set them to what your risk appetite says).
TARGET_MAX_FAILURE_RATE = env.int("TARGET_MAX_FAILURE_RATE", default=10)  # % of tested employees who click or submit
TARGET_MIN_REPORT_RATE = env.int("TARGET_MIN_REPORT_RATE", default=20)  # % of tested employees who report the email
TARGET_MIN_TRAINING_COMPLETION = env.int("TARGET_MIN_TRAINING_COMPLETION", default=95)  # % of assigned training done
TARGET_MIN_COVERAGE = env.int("TARGET_MIN_COVERAGE", default=90)  # % of eligible employees tested in the period
# Anonymise a deactivated employee's PII after this many days (event log stays; invariant #3). 0 = never.
PII_RETENTION_DAYS = env.int("PII_RETENTION_DAYS", default=0)

# Anonymous hits on staff-only pages (the dashboard, the /analytics/ API) go to the dashboard login.
LOGIN_URL = "dashboard:login"
LOGIN_REDIRECT_URL = "dashboard:overview"
LOGOUT_REDIRECT_URL = "dashboard:login"

# --- Campaign launch rate limiting (Phase 3) ---
# Applies to both the admin "Launch" action and the scheduler — see
# apps/campaigns/services.py::launch_campaign(), the one shared launch path.
CAMPAIGN_LAUNCH_RATE_LIMIT = env.int("CAMPAIGN_LAUNCH_RATE_LIMIT", default=5)
# Segregation of duties: the person who submitted a campaign cannot approve it. Set false only as a documented
# break-glass for a single-admin deployment (shown on the governance page).
REQUIRE_SEPARATE_APPROVER = env.bool("REQUIRE_SEPARATE_APPROVER", default=True)

# --- Gophish adapter config (consumed by apps.engine.GophishClient, Phase 1) ---
GOPHISH_API_URL = env("GOPHISH_API_URL", default="")
GOPHISH_API_KEY = env("GOPHISH_API_KEY", default="")
GOPHISH_WEBHOOK_SECRET = env("GOPHISH_WEBHOOK_SECRET", default="")
GOPHISH_DEFAULT_SEND_PROFILE = env("GOPHISH_DEFAULT_SEND_PROFILE", default="default")
# Public address of the phishing listener; what a campaign's landing links are built on.
PHISH_SERVER_URL = env("PHISH_SERVER_URL", default="http://localhost:8080")
