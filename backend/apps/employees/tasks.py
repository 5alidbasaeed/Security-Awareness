from celery import shared_task
from django.utils import timezone

from apps.core.audit import log_action

from .models import Employee


@shared_task
def lapse_expired_exemptions() -> int:
    """End exemptions whose date has passed, and record each one so the register shows it happened."""
    expired = Employee.objects.filter(is_exempt=True, exempt_until__lt=timezone.localdate())
    count = 0
    for employee in expired:
        reason, until = employee.exempt_reason, employee.exempt_until
        employee.is_exempt = False  # save() clears the reason/date/owner
        employee.save()
        log_action(actor=None, action="exemption_lapsed", target_description=str(employee),
                   previous_reason=reason, exempt_until=str(until))
        count += 1
    return count
