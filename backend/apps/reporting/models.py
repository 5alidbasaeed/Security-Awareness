from django.conf import settings
from django.db import models

from apps.core.managers import AppendOnlyQuerySet


class GeneratedReportQuerySet(AppendOnlyQuerySet):
    def without_content(self):
        """Listings never need the file bytes — don't pull megabytes per row."""
        return self.defer("content")


class GeneratedReport(models.Model):
    """
    An archived report file. Immutable: regenerating produces a new row, so the
    archive doubles as an audit trail of what was reported, when and by whom.
    """

    class Kind(models.TextChoices):
        EXECUTIVE_SUMMARY = "executive_summary", "Executive summary"
        CAMPAIGN_RESULTS = "campaign_results", "Campaign results"
        DEPARTMENT_SUMMARY = "department_summary", "Department summary"
        TRAINING_COMPLIANCE = "training_compliance", "Training compliance"
        RISK_SCORES = "risk_scores", "Employee risk scores"
        EVIDENCE_PACKAGE = "evidence_package", "Compliance evidence package"

    kind = models.CharField(max_length=32, choices=Kind.choices)
    file_format = models.CharField(max_length=8)
    filename = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    content = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField()
    row_count = models.PositiveIntegerField(default=0)

    period_start = models.DateField()
    period_end = models.DateField()
    as_of = models.DateTimeField(help_text="Every figure is computed from the event log as it stood at this moment.")
    algorithm_version = models.CharField(max_length=32)
    scope_label = models.CharField(max_length=200)
    is_scoped = models.BooleanField(
        default=False, help_text="Built under a Department Manager's row scoping — only its author and org-wide roles may see it."
    )
    contains_employee_data = models.BooleanField(default=False)

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="generated_reports"
    )  # null = the scheduler
    generated_at = models.DateTimeField(auto_now_add=True)

    objects = GeneratedReportQuerySet.as_manager()

    class Meta:
        ordering = ["-generated_at", "-id"]
        permissions = [
            ("generate_report", "Can generate aggregate (non-individual) reports"),
            ("export_employee_level", "Can generate reports that name individual employees"),
        ]
        indexes = [models.Index(fields=["kind", "period_start", "period_end"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("GeneratedReport rows are immutable — generate a new report instead.")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.filename
