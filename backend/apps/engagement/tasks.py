"""
Event-driven engagement and integrations, run off the event log:
  * coaching — a short, supportive email to an employee right after they click
    or submit in a simulation, pointing at the teachable-moment page; and
  * SIEM export — forwarding every event to an external webhook (a SIEM, or a
    Slack/Teams incoming webhook), fire-and-forget.
Both are opt-in and safe to no-op: coaching needs an email backend, SIEM export
needs SIEM_WEBHOOK_URL. Neither ever includes a password (there are none in the
event log — invariant #4).
"""

import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event

logger = logging.getLogger(__name__)

COACHING_COOLDOWN = timedelta(hours=24)


@shared_task
def send_coaching_email(event_id: int):
    from apps.core.audit import log_action
    from apps.core.models import AuditLogEntry

    event = Event.objects.select_related("employee", "campaign").filter(pk=event_id).first()
    if event is None or not event.employee.is_active or not event.employee.email:
        return
    # One coaching email per employee per 24h, tracked in the audit log (durable, and a record).
    if AuditLogEntry.objects.filter(action="coaching_sent", metadata__employee_id=event.employee_id,
                                    occurred_at__gte=timezone.now() - COACHING_COOLDOWN).exists():
        return
    learn_url = settings.PORTAL_BASE_URL.rstrip("/") + reverse("portal:learn")
    try:
        send_mail(
            subject="A quick security heads-up",
            message=(
                f"Hi {event.employee.full_name},\n\n"
                "That email was a simulated phishing test from our security awareness programme — "
                "no harm done, and nothing you typed was stored. It's a good moment to review what to "
                f"look for:\n\n{learn_url}\n\n"
                "Spotting these takes practice, and reporting anything suspicious is always the right move."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[event.employee.email],
        )
    except Exception:  # noqa: BLE001 — coaching is best-effort; never break ingestion
        logger.exception("send_coaching_email: could not email employee %s", event.employee_id)
        return
    log_action(actor=None, action="coaching_sent", target_description=str(event.employee),
               employee_id=event.employee_id, campaign_id=event.campaign_id)


@shared_task
def export_event_to_siem(event_id: int):
    url = getattr(settings, "SIEM_WEBHOOK_URL", "")
    if not url:
        return
    event = Event.objects.select_related("employee", "campaign").filter(pk=event_id).first()
    if event is None:
        return
    payload = {
        "type": "security_awareness.event",
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "employee_email": event.employee.email,
        "department": event.employee.department.name if event.employee.department_id else None,
        "campaign": event.campaign.name,
        "source": event.source,
    }
    try:
        requests.post(url, json=payload, timeout=getattr(settings, "SIEM_WEBHOOK_TIMEOUT", 5))
    except Exception:  # noqa: BLE001 — a down SIEM must never affect event ingestion
        logger.exception("export_event_to_siem: POST to SIEM failed for event %s", event_id)
