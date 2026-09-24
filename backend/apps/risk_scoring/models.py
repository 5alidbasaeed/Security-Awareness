from django.db import models

from apps.core.managers import AppendOnlyQuerySet


class RiskScoreSnapshot(models.Model):
    """
    One computed risk score for one employee at one point in time. Never
    updated: a new algorithm version or new events produce a NEW row, and
    "current score" is the latest row per employee. See CLAUDE.md invariant #6.
    """

    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE, related_name="risk_scores")
    score = models.DecimalField(max_digits=5, decimal_places=2, help_text="0 (lowest risk) to 100 (highest).")
    computed_at = models.DateTimeField(auto_now_add=True)
    algorithm_version = models.CharField(max_length=32)
    contributing_metrics = models.JSONField(default=dict, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-computed_at", "-id"]
        indexes = [models.Index(fields=["employee", "computed_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise TypeError("RiskScoreSnapshot rows are immutable — compute a new snapshot instead.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} — {self.score} ({self.algorithm_version})"
