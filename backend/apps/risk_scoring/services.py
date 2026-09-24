"""
Glue between the event log and the pure scoring function. Snapshots are only
ever inserted (CLAUDE.md invariant #6); "current score" is the latest row.
"""

from collections import defaultdict

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


class ScoreHistory:
    """
    Scores a set of employees as of ANY past moment, recomputed from the
    immutable event log — not read from snapshots — so a historical report is
    exactly reproducible (CLAUDE.md invariants #3 and #6). Events are loaded once;
    call `scores_at()` as many times as needed (e.g. once per week for a trend).
    """

    def __init__(self, employees, until):
        self._employees = list(employees.values_list("id", "created_at"))
        rows = (
            Event.objects.filter(employee__in=employees, occurred_at__lte=until)
            .order_by("occurred_at")
            .values_list("employee_id", "event_type", "campaign_id", "occurred_at")
        )
        self._events = defaultdict(list)
        for employee_id, event_type, campaign_id, occurred_at in rows:
            self._events[employee_id].append(ScoringEvent(event_type, campaign_id, occurred_at))

    def scores_at(self, moment) -> dict:
        """{employee_id: ScoreResult} for everyone who already existed at `moment`."""
        results = {}
        for employee_id, created_at in self._employees:
            if created_at > moment:
                continue
            visible = [e for e in self._events.get(employee_id, ()) if e.occurred_at <= moment]
            results[employee_id] = compute_risk_score(visible, moment)
        return results
