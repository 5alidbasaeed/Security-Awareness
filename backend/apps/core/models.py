from django.conf import settings
from django.db import models


class AuditLogEntry(models.Model):
    """
    Append-only record of sensitive actions (campaign launches, target-list
    changes, data exports). Not modeled as a domain event — see apps.events
    for the phishing-simulation event log — but kept immutable for the same
    reason: an audit trail that can be edited isn't an audit trail.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_log_entries",
    )
    action = models.CharField(max_length=100)
    target_description = models.CharField(max_length=500)
    occurred_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name_plural = "audit log entries"

    def __str__(self):
        return f"{self.action} — {self.target_description}"
