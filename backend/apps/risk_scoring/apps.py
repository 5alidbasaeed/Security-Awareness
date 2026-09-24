from django.apps import AppConfig


class RiskScoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.risk_scoring"

    def ready(self):
        from . import signals  # noqa: F401 — connects the post_save receiver
