"""
Celery Beat entry point — see CELERY_BEAT_SCHEDULE in settings/base.py.
EMAIL_BACKEND defaults to the console backend (dev/test); a real deployment
overrides it via env var to point at real SMTP — see CLAUDE.md/README for
what's deferred.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import TrainingAssignment


def magic_link(employee):
    # Imported lazily: the portal app depends on training, not the other way round.
    from apps.portal.views import magic_link as portal_link

    return portal_link(employee)

logger = logging.getLogger(__name__)

REMINDER_AFTER = timedelta(days=2)  # remind once an assignment has been outstanding this long
REMINDER_COOLDOWN = timedelta(hours=24)  # ...and don't remind again more often than this


@shared_task
def send_training_reminders():
    now = timezone.now()
    due_for_reminder = (
        TrainingAssignment.objects.filter(
            completed_at__isnull=True, module__is_active=True, employee__is_active=True,
            assigned_at__lte=now - REMINDER_AFTER
        )
        .exclude(last_reminded_at__gte=now - REMINDER_COOLDOWN)
        .select_related("employee", "module")
    )

    sent = 0
    for assignment in due_for_reminder:
        try:
            send_mail(
                subject=f"Reminder: complete your security training — {assignment.module.title}",
                message=(
                    f"Hi {assignment.employee.full_name},\n\n"
                    f'You have an outstanding security awareness training assignment: "{assignment.module.title}". '
                    "Please complete it at your earliest convenience.\n\n"
                    f"Open your training (no password needed): {magic_link(assignment.employee)}"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[assignment.employee.email],
            )
        except Exception:  # noqa: BLE001 — one bad mailbox must not block everyone after it, every hour
            logger.exception("send_training_reminders: could not email %s — will retry next run", assignment.employee)
            continue
        assignment.last_reminded_at = now
        assignment.save(update_fields=["last_reminded_at"])
        sent += 1

    logger.info("send_training_reminders: sent %d reminder(s)", sent)
    return sent
