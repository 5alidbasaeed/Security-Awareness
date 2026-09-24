"""
Suspicious emails employees report (Phase 6.2 "report button"). Reports of the
platform's own simulations already arrive from Gophish as phishing_reported
events; this is the queue for everything else an employee flags, so the
security team can triage real threats. Reports can come from the employee
portal form or from a mail-processing integration via the API.
"""

from django.conf import settings
from django.db import models


class ReportedEmail(models.Model):
    class Verdict(models.TextChoices):
        NEW = "new", "New"
        SAFE = "safe", "Safe"
        SPAM = "spam", "Spam"
        MALICIOUS = "malicious", "Malicious"
        SIMULATION = "simulation", "Our simulation"

    class Source(models.TextChoices):
        PORTAL = "portal", "Employee portal"
        API = "api", "API / mail integration"

    reporter = models.ForeignKey("employees.Employee", on_delete=models.PROTECT, related_name="reported_emails")
    subject = models.CharField(max_length=300, blank=True)
    sender = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True, help_text="What the employee said about it.")
    headers = models.TextField(blank=True, help_text="Raw headers, if the integration supplied them.")
    source = models.CharField(max_length=16, choices=Source.choices)
    reported_at = models.DateTimeField(auto_now_add=True)
    verdict = models.CharField(max_length=16, choices=Verdict.choices, default=Verdict.NEW)
    triaged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    triaged_at = models.DateTimeField(null=True, blank=True)
    triage_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-reported_at"]
        indexes = [models.Index(fields=["verdict", "reported_at"])]

    def __str__(self):
        return f"{self.subject or '(no subject)'} — {self.reporter}"
