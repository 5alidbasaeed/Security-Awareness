"""Background work for the employee portal."""

import logging

from celery import shared_task

from apps.employees.models import Employee

logger = logging.getLogger(__name__)


@shared_task
def send_portal_link(email: str) -> None:
    """
    Emails a sign-in link if `email` belongs to an active employee. Run in the background, and
    enqueued for EVERY submitted address, so the sign-in page answers at the same speed whether or
    not the address exists (no timing oracle) and a slow mail relay never holds up a web worker.
    """
    from .views import send_link

    employee = Employee.objects.filter(email__iexact=email, is_active=True).first()
    if employee is None:
        return
    try:
        send_link(employee)
    except Exception:  # noqa: BLE001 — a delivery failure must not be retried into a mail storm
        logger.exception("portal: could not email sign-in link")
