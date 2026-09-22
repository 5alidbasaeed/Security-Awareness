from django.apps import AppConfig


class TrainingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.training"

    def ready(self):
        from . import signals  # noqa: F401 — import registers the post_save receiver
