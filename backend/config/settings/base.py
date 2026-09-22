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
    # apps.training and apps.risk_scoring are still empty skeletons (Phase 2/4).
    "apps.core",
    "apps.employees",
    "apps.campaigns",
    "apps.events",
    "apps.training",
    "apps.risk_scoring",
    "apps.engine",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
}

# --- Gophish adapter config (consumed by apps.engine.GophishClient, Phase 1) ---
GOPHISH_API_URL = env("GOPHISH_API_URL", default="")
GOPHISH_API_KEY = env("GOPHISH_API_KEY", default="")
GOPHISH_WEBHOOK_SECRET = env("GOPHISH_WEBHOOK_SECRET", default="")
GOPHISH_DEFAULT_SEND_PROFILE = env("GOPHISH_DEFAULT_SEND_PROFILE", default="default")
