"""
Read-side analytics over the event log and the snapshot table. Pure queries —
nothing here writes. Training completion is reported as its own compliance
metric and is deliberately NOT part of the risk score (CLAUDE.md invariant #6
and the plan's Risk Scoring section).
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.employees.models import Employee
from apps.events.models import Event
from apps.training.models import TrainingAssignment

from .models import RiskScoreSnapshot
from .services import latest_snapshots

HIGH_RISK_THRESHOLD = 60
TREND_LOOKBACK_DAYS = 30
TREND_DELTA = 5  # points of movement before we call it improving/worsening


def _rate(part: int, whole: int):
    return round(100 * part / whole, 1) if whole else None


def campaign_summary(campaign) -> dict:
    """
    Counts are distinct employees per event type. Rates use employees who were
    actually sent the email as the denominator. `opened` is a delivery
    diagnostic only — it never feeds a rate or a score (invariant #5).
    """
    rows = Event.objects.filter(campaign=campaign).values("event_type").annotate(n=Count("employee", distinct=True))
    counts = {row["event_type"]: row["n"] for row in rows}
    failed = (
        Event.objects.filter(
            campaign=campaign, event_type__in=[Event.EventType.LINK_CLICKED, Event.EventType.CREDENTIAL_ATTEMPT]
        )
        .values("employee")
        .distinct()
        .count()
    )
    targeted = counts.get(Event.EventType.EMAIL_SENT, 0)
    submitted = counts.get(Event.EventType.CREDENTIAL_ATTEMPT, 0)
    reported = counts.get(Event.EventType.PHISHING_REPORTED, 0)
    return {
        "campaign_id": campaign.pk,
        "name": campaign.name,
        "status": campaign.status,
        "targeted": targeted,
        "delivered": counts.get(Event.EventType.EMAIL_DELIVERED, 0),
        "opened_diagnostic_only": counts.get(Event.EventType.EMAIL_OPENED, 0),
        "clicked": counts.get(Event.EventType.LINK_CLICKED, 0),
        "submitted_data": submitted,
        "reported": reported,
        "failed": failed,
        "failure_rate_percent": _rate(failed, targeted),
        "submit_rate_percent": _rate(submitted, targeted),
        "report_rate_percent": _rate(reported, targeted),
    }


def training_compliance(assignments) -> dict:
    now = timezone.now()
    totals = assignments.aggregate(
        assigned=Count("id"),
        completed=Count("id", filter=Q(completed_at__isnull=False)),
        overdue=Count("id", filter=Q(completed_at__isnull=True, due_at__lt=now)),
    )
    return {
        **totals,
        "outstanding": totals["assigned"] - totals["completed"],
        "completion_rate_percent": _rate(totals["completed"], totals["assigned"]),
    }


def department_summary(department) -> dict:
    employees = Employee.objects.filter(department=department)
    scores = latest_snapshots().filter(employee__in=employees)
    stats = scores.aggregate(average=Avg("score"), scored=Count("id"), high=Count("id", filter=Q(score__gte=HIGH_RISK_THRESHOLD)))
    return {
        "department_id": department.pk,
        "name": department.name,
        "employees": employees.count(),
        "employees_scored": stats["scored"],
        "average_score": float(stats["average"]) if stats["average"] is not None else None,
        "high_risk_employees": stats["high"],
        "high_risk_threshold": HIGH_RISK_THRESHOLD,
        "training": training_compliance(TrainingAssignment.objects.filter(employee__in=employees)),
    }


def score_history(employee) -> list[dict]:
    snapshots = RiskScoreSnapshot.objects.filter(employee=employee).order_by("computed_at", "id")
    return [
        {"computed_at": s.computed_at, "score": float(s.score), "algorithm_version": s.algorithm_version}
        for s in snapshots
    ]


def trend_direction(history: list[dict], now=None) -> str:
    """Lower score is better. Compares the latest snapshot to one from ~30 days earlier."""
    now = now or timezone.now()
    if not history:
        return "insufficient_data"
    baseline_cutoff = now - timedelta(days=TREND_LOOKBACK_DAYS)
    baseline = [h for h in history if h["computed_at"] <= baseline_cutoff]
    if not baseline:
        return "insufficient_data"
    delta = history[-1]["score"] - baseline[-1]["score"]
    if delta <= -TREND_DELTA:
        return "improving"
    if delta >= TREND_DELTA:
        return "worsening"
    return "stagnant"


def department_trend(department, weeks: int = 12, now=None) -> list[dict]:
    """
    Average of each employee's latest-known score as of the end of each week,
    so an employee with no new snapshot that week still counts at their last value.
    """
    now = now or timezone.now()
    snapshots = list(
        RiskScoreSnapshot.objects.filter(employee__department=department)
        .order_by("computed_at", "id")
        .values("employee_id", "score", "computed_at")
    )
    latest: dict[int, float] = {}
    index = 0
    series = []
    for weeks_ago in range(weeks - 1, -1, -1):
        week_end = now - timedelta(weeks=weeks_ago)
        while index < len(snapshots) and snapshots[index]["computed_at"] <= week_end:
            latest[snapshots[index]["employee_id"]] = float(snapshots[index]["score"])
            index += 1
        series.append(
            {
                "week_ending": week_end.date().isoformat(),
                "average_score": round(sum(latest.values()) / len(latest), 2) if latest else None,
                "employees_scored": len(latest),
            }
        )
    return series
