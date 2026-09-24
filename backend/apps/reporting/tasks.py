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


def _due_period(schedule, today):
    """(start, end) of the period to send today, or None if nothing is due."""
    from datetime import timedelta

    from .models import ReportSchedule

    if schedule.frequency == ReportSchedule.Frequency.MONTHLY:
        start, end = previous_month(today)
    else:
        end = today - timedelta(days=today.weekday() + 1)  # last Sunday
        start = end - timedelta(days=6)
    if schedule.last_period_end is not None and schedule.last_period_end >= end:
        return None
    return start, end


@shared_task
def send_scheduled_reports():
    """Daily: generate and email every schedule whose period has closed and wasn't sent yet."""
    from django.conf import settings
    from django.core.mail import EmailMessage

    from apps.core.audit import log_action

    from .models import ReportSchedule
    from .services import KINDS

    today = timezone.now().date()
    sent = 0
    for schedule in ReportSchedule.objects.filter(is_active=True):
        period = _due_period(schedule, today)
        recipients = schedule.recipient_list()
        if period is None or not recipients:
            continue
        spec = KINDS.get(schedule.kind)
        if spec is None or spec.employee_level:
            logger.warning("send_scheduled_reports: %s has a kind that can't be scheduled", schedule)
            continue
        try:
            report = generate_report(kind=schedule.kind, user=None, period_start=period[0], period_end=period[1])
            email = EmailMessage(
                subject=f"{spec.label}: {period[0]:%Y-%m-%d} to {period[1]:%Y-%m-%d}",
                body=(f"Attached: {spec.label} for {period[0]:%Y-%m-%d} to {period[1]:%Y-%m-%d}.\n"
                      f"SHA-256 {report.sha256}\n\nSent by the security awareness platform ({schedule.name})."),
                from_email=settings.DEFAULT_FROM_EMAIL, to=recipients,
            )
            email.attach(report.filename, bytes(report.content), report.content_type)
            email.send()
        except Exception:  # noqa: BLE001 — one failing schedule mustn't block the rest
            logger.exception("send_scheduled_reports: %s failed", schedule)
            continue
        schedule.last_period_end = period[1]
        schedule.save(update_fields=["last_period_end"])
        log_action(actor=None, action="scheduled_report_sent", target_description=schedule.name,
                   report_id=report.pk, recipients=len(recipients))
        sent += 1
    return sent
