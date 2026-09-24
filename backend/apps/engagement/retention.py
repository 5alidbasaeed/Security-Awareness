"""
Privacy retention (Phase 6.4). The event log is immutable (invariant #3), so we
never delete history — instead we ANONYMISE an employee who was deactivated more than PII_RETENTION_DAYS ago (counted from `deactivated_at`, never from when the record was created):
personal data in place: name and email become placeholders, so the aggregate
history survives but the person is no longer identifiable. Reversible only by
re-importing them.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def anonymize_employee(employee):
    employee.full_name = "Former employee"
    employee.email = f"anonymised+{employee.pk}@example.invalid"
    employee.save(update_fields=["full_name", "email"])


@shared_task
def anonymize_stale_employees():
    """Anonymise employees deactivated longer than PII_RETENTION_DAYS. 0 = disabled."""
    from apps.core.audit import log_action
    from apps.employees.models import Employee

    days = getattr(settings, "PII_RETENTION_DAYS", 0)
    if not days:
        return 0
    now = timezone.now()
    # Rows deactivated before deactivated_at existed have no timestamp. Guessing would either anonymise
    # them at once or never, so start their clock now; they become eligible a full retention period later.
    Employee.objects.filter(is_active=False, deactivated_at__isnull=True).update(deactivated_at=now)
    cutoff = now - timedelta(days=days)
    stale = Employee.objects.filter(is_active=False, deactivated_at__lt=cutoff).exclude(email__endswith="@example.invalid")
    count = 0
    for employee in stale:
        anonymize_employee(employee)
        count += 1
    if count:
        log_action(actor=None, action="employees_anonymised", target_description=f"{count} former employee(s)")
    return count
