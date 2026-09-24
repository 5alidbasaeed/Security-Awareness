import logging

from celery import shared_task
from django.utils import timezone

from .models import GeneratedReport
from .services import generate_report, previous_month

logger = logging.getLogger(__name__)


@shared_task
def generate_monthly_executive_summary():
    """
    Runs daily (CELERY_BEAT_SCHEDULE) and is idempotent: once last month's org-wide
    executive summary exists it does nothing. Keeps a standing archive of monthly
    reports without anyone having to remember to run one.
    """
    start, end = previous_month(timezone.now().date())
    exists = GeneratedReport.objects.filter(
        kind=GeneratedReport.Kind.EXECUTIVE_SUMMARY, generated_by__isnull=True, period_start=start, period_end=end
    ).exists()
    if exists:
        return None
    report = generate_report(kind=GeneratedReport.Kind.EXECUTIVE_SUMMARY, user=None, period_start=start, period_end=end)
    logger.info("generate_monthly_executive_summary: created %s", report.filename)
    return report.pk
