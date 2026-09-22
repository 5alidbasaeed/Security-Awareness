from django.conf import settings
from django.db import models

from apps.employees.models import Department


class Campaign(models.Model):
    """
    Django-side campaign metadata. `gophish_campaign_id` is a plain
    reference field, never a FK — Gophish's own database is a separate
    system that's never joined against. See CLAUDE.md invariant #2.
    """

    class Status(models.TextChoices):
        DRAFT = "draft"
        LAUNCHED = "launched"

    name = models.CharField(max_length=200)
    gophish_campaign_id = models.CharField(max_length=64, null=True, blank=True)
    template_name = models.CharField(max_length=200, help_text="Must match an existing Gophish email template name.")
    landing_page_url = models.URLField(help_text="Landing page URL, per Gophish's create-campaign API.")
    target_department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    launched_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns"
    )

    def __str__(self):
        return self.name
