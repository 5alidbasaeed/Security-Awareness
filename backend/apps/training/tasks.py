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
            completed_at__isnull=True, waived_at__isnull=True, module__is_active=True, employee__is_active=True,
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


@shared_task
def enforce_training_policies():
    """Daily: enrol everyone in scope of an active policy who is due for it. Idempotent."""
    from apps.core.audit import log_action
    from apps.employees.models import Employee

    from .models import TrainingPolicy

    now = timezone.now()
    enrolled = 0
    for policy in TrainingPolicy.objects.filter(is_active=True, module__is_active=True).select_related("module"):
        people = Employee.objects.filter(is_active=True)
        if policy.department_id:
            people = people.filter(department_id=policy.department_id)
        recent_cutoff = now - timedelta(days=policy.repeat_every_days)
        already = TrainingAssignment.objects.filter(module=policy.module, assigned_at__gte=recent_cutoff).values("employee_id")
        open_ = TrainingAssignment.objects.filter(module=policy.module, completed_at__isnull=True, waived_at__isnull=True).values("employee_id")
        for employee in people.exclude(pk__in=already).exclude(pk__in=open_):
            TrainingAssignment.objects.create(employee=employee, module=policy.module,
                                              due_at=now + timedelta(days=policy.due_days))
            enrolled += 1
        if enrolled:
            log_action(actor=None, action="training_policy_enforced", target_description=policy.name, enrolled=enrolled)
    logger.info("enforce_training_policies: enrolled %d", enrolled)
    return enrolled


ESCALATE_AFTER_OVERDUE = timedelta(days=7)
ESCALATION_COOLDOWN = timedelta(days=7)


@shared_task
def escalate_overdue_training():
    """
    Weekly-ish (runs daily, one email per manager per 7 days): tell each department's managers
    which of their people are more than a week overdue. Managers are the department's
    `manager` (an Employee) and its Department Manager users.
    """
    from apps.core.audit import log_action
    from apps.core.models import AuditLogEntry
    from apps.employees.models import Department

    now = timezone.now()
    sent = 0
    for department in Department.objects.prefetch_related("managers").select_related("manager"):
        overdue = (
            TrainingAssignment.objects.filter(
                employee__department=department, employee__is_active=True, completed_at__isnull=True, waived_at__isnull=True,
                due_at__lt=now - ESCALATE_AFTER_OVERDUE,
            ).select_related("employee", "module").order_by("due_at")
        )
        if not overdue.exists():
            continue
        recipients = {u.email for u in department.managers.all() if u.email}
        if department.manager and department.manager.is_active:
            recipients.add(department.manager.email)
        recipients.discard("")
        if not recipients:
            continue
        # Cooldown lives in the audit log (durable across worker restarts, and a record that we escalated).
        if AuditLogEntry.objects.filter(action="training_escalation_sent", metadata__department_id=department.pk,
                                        occurred_at__gte=now - ESCALATION_COOLDOWN).exists():
            continue
        lines = [f"- {a.employee.full_name}: {a.module.title} (due {a.due_at:%Y-%m-%d})" for a in overdue[:50]]
        try:
            send_mail(
                subject=f"Overdue security training in {department.name}",
                message=("These people in your team are more than a week overdue on security training:\n\n"
                         + "\n".join(lines) + "\n\nA quick nudge from you makes a real difference."),
                from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=sorted(recipients),
            )
        except Exception:  # noqa: BLE001 — one bad address mustn't stop the other departments
            logger.exception("escalate_overdue_training: could not email %s", department)
            continue
        log_action(actor=None, action="training_escalation_sent", target_description=department.name,
                   department_id=department.pk, overdue=overdue.count())
        sent += 1
    return sent
