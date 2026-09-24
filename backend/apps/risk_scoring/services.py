"""
Glue between the event log and the pure scoring function. Snapshots are only
ever inserted (CLAUDE.md invariant #6); "current score" is the latest row.
"""

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.employees.models import Employee
from apps.events.models import Event

from .models import RiskScoreSnapshot
from .scoring import ALGORITHM_VERSION, ScoringEvent, compute_risk_score


def compute_and_store_snapshot(employee: Employee, *, now=None, algorithm_version: str = ALGORITHM_VERSION):
    if algorithm_version != ALGORITHM_VERSION:
        raise ValueError(f"Unknown risk-scoring algorithm version {algorithm_version!r}.")
    now = now or timezone.now()
    events = [
        ScoringEvent(event_type=e.event_type, campaign_id=e.campaign_id, occurred_at=e.occurred_at)
        for e in Event.objects.filter(employee=employee, occurred_at__lte=now)
    ]
    result = compute_risk_score(events, now)
    return RiskScoreSnapshot.objects.create(
        employee=employee,
        score=result.score,
        algorithm_version=algorithm_version,
        contributing_metrics=result.metrics,
    )


def latest_snapshots():
    """One row per employee: their most recent snapshot."""
    newest_id = (
        RiskScoreSnapshot.objects.filter(employee=OuterRef("employee"))
        .order_by("-computed_at", "-id")
        .values("id")[:1]
    )
    return RiskScoreSnapshot.objects.filter(id=Subquery(newest_id))
