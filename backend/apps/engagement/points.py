"""
Recognition points — deliberately reward *reporting*, not shame failure (the
plan is explicit about this). Points are a positive-only read model computed
from the immutable event log, never stored, so changing the formula is free.

  * Reporting a phishing email: +10 (the behaviour we most want).
  * Completing assigned training: +5.
Failures cost nothing here — the risk score already reflects them, and a
leaderboard that punishes people discourages reporting.
"""

from django.db.models import Count, Q

from apps.events.models import Event
from apps.training.models import TrainingAssignment

REPORT_POINTS = 10
TRAINING_POINTS = 5


def points_for(employee) -> dict:
    reports = Event.objects.filter(employee=employee, event_type=Event.EventType.PHISHING_REPORTED).count()
    completed = TrainingAssignment.objects.filter(employee=employee, completed_at__isnull=False).count()
    return {
        "reports": reports,
        "training_completed": completed,
        "points": reports * REPORT_POINTS + completed * TRAINING_POINTS,
    }


def leaderboard(employees, limit=10) -> list[dict]:
    """Top reporters/completers among `employees` (already row-scoped by the caller)."""
    rows = (
        employees.annotate(
            reports=Count("events", filter=Q(events__event_type=Event.EventType.PHISHING_REPORTED), distinct=True),
            completed=Count("training_assignments", filter=Q(training_assignments__completed_at__isnull=False), distinct=True),
        )
    )
    scored = [
        {"employee": e, "reports": e.reports, "training_completed": e.completed,
         "points": e.reports * REPORT_POINTS + e.completed * TRAINING_POINTS}
        for e in rows
    ]
    scored = [s for s in scored if s["points"] > 0]
    scored.sort(key=lambda s: (-s["points"], s["employee"].full_name))
    return scored[:limit]
