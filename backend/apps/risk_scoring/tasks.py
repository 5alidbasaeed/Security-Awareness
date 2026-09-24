import logging

from celery import shared_task

from apps.employees.models import Employee

from .scoring import ALGORITHM_VERSION
from .services import compute_and_store_snapshot

logger = logging.getLogger(__name__)


@shared_task
def recompute_score(employee_id: int, algorithm_version: str = ALGORITHM_VERSION):
    """Inserts a new snapshot for one employee — never updates an existing one."""
    employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        logger.warning("recompute_score: no Employee with id=%s — skipped", employee_id)
        return
    compute_and_store_snapshot(employee, algorithm_version=algorithm_version)


@shared_task
def recompute_all_scores(algorithm_version: str = ALGORITHM_VERSION):
    """
    Daily (time decay moves scores even with no new events) and after an
    algorithm change. Fans out one task per employee so one bad row can't
    block the rest.
    """
    count = 0
    for employee_id in Employee.objects.values_list("id", flat=True):
        recompute_score.delay(employee_id, algorithm_version)
        count += 1
    logger.info("recompute_all_scores: queued %d employee(s) for %s", count, algorithm_version)
