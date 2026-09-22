from django.db import models

from .managers import EventQuerySet


class Event(models.Model):
    """
    Append-only phishing-simulation/training event log. See the
    database-schema skill for the target shape this implements and
    CLAUDE.md invariant #3 for why it's immutable.
    """

    class EventType(models.TextChoices):
        EMAIL_SENT = "email_sent"
        EMAIL_DELIVERED = "email_delivered"
        EMAIL_OPENED = "email_opened"
        LINK_CLICKED = "link_clicked"
        CREDENTIAL_ATTEMPT = "credential_attempt"
        PHISHING_REPORTED = "phishing_reported"
        TRAINING_ASSIGNED = "training_assigned"
        TRAINING_STARTED = "training_started"
        TRAINING_COMPLETED = "training_completed"
        QUIZ_COMPLETED = "quiz_completed"

    class Source(models.TextChoices):
        GOPHISH = "gophish"
        DJANGO = "django"
        MANUAL = "manual"

    event_type = models.CharField(max_length=32, choices=EventType.choices)
    employee = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="events")
    campaign = models.ForeignKey("campaigns.Campaign", on_delete=models.PROTECT, related_name="events")
    source = models.CharField(max_length=16, choices=Source.choices)
    external_id = models.CharField(max_length=255, null=True, blank=True)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="unique_event_source_external_id",
            )
        ]
        indexes = [
            models.Index(fields=["employee", "occurred_at"]),
            models.Index(fields=["campaign", "event_type"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("Event rows are immutable — create a new Event instead of modifying this one.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.event_type} — {self.employee_id} @ {self.occurred_at}"
