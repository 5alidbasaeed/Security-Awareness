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
MEDIUM_RISK_THRESHOLD = 30
TREND_LOOKBACK_DAYS = 30
TREND_DELTA = 5  # points of movement before we call it improving/worsening


def risk_level(score) -> str:
    """The one place a score becomes low/medium/high — the UI reads this, never its own numbers."""
    if score is None:
        return "unscored"
    if score >= HIGH_RISK_THRESHOLD:
        return "high"
    if score >= MEDIUM_RISK_THRESHOLD:
        return "medium"
    return "low"


def rate(part: int, whole: int):
    return round(100 * part / whole, 1) if whole else None


def campaign_summary(campaign, until=None) -> dict:
    """
    Counts are distinct employees per event type. Rates use employees who were
    actually sent the email as the denominator. `opened` is a delivery
    diagnostic only — it never feeds a rate or a score (invariant #5).
    """
    events = Event.objects.filter(campaign=campaign)
    if until is not None:
        events = events.filter(occurred_at__lte=until)
    rows = events.values("event_type").annotate(n=Count("employee", distinct=True))
    counts = {row["event_type"]: row["n"] for row in rows}
    failed = (
        events.filter(event_type__in=[Event.EventType.LINK_CLICKED, Event.EventType.CREDENTIAL_ATTEMPT])
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
        "failure_rate_percent": rate(failed, targeted),
        "submit_rate_percent": rate(submitted, targeted),
        "report_rate_percent": rate(reported, targeted),
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
        "completion_rate_percent": rate(totals["completed"], totals["assigned"]),
    }


def scope_summary(employees) -> dict:
    """Summary for any set of employees: a department, or everyone a user may see."""
    scores = latest_snapshots().filter(employee__in=employees)
    stats = scores.aggregate(
        average=Avg("score"), scored=Count("id"), high=Count("id", filter=Q(score__gte=HIGH_RISK_THRESHOLD))
    )
    return {
        "employees": employees.count(),
        "employees_scored": stats["scored"],
        "average_score": float(stats["average"]) if stats["average"] is not None else None,
        "high_risk_employees": stats["high"],
        "high_risk_threshold": HIGH_RISK_THRESHOLD,
        "training": training_compliance(TrainingAssignment.objects.filter(employee__in=employees)),
    }


def department_summary(department) -> dict:
    summary = scope_summary(Employee.objects.filter(department=department))
    return {"department_id": department.pk, "name": department.name, **summary}


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


def trend_series(snapshots, weeks: int = 12, now=None) -> list[dict]:
    """
    Average of each employee's latest-known score as of the end of each week,
    so an employee with no new snapshot that week still counts at their last value.
    `snapshots` is any RiskScoreSnapshot queryset (one department, everyone visible, ...).
    """
    now = now or timezone.now()
    rows = list(snapshots.order_by("computed_at", "id").values("employee_id", "score", "computed_at"))
    latest: dict[int, float] = {}
    index = 0
    series = []
    for weeks_ago in range(weeks - 1, -1, -1):
        week_end = now - timedelta(weeks=weeks_ago)
        while index < len(rows) and rows[index]["computed_at"] <= week_end:
            latest[rows[index]["employee_id"]] = float(rows[index]["score"])
            index += 1
        series.append(
            {
                "week_ending": week_end.date().isoformat(),
                "average_score": round(sum(latest.values()) / len(latest), 2) if latest else None,
                "employees_scored": len(latest),
            }
        )
    return series


def department_trend(department, weeks: int = 12, now=None) -> list[dict]:
    return trend_series(RiskScoreSnapshot.objects.filter(employee__department=department), weeks=weeks, now=now)


def series_delta(series: list[dict]):
    """Change between the last two weeks that have data (lower score is better), or None."""
    points = [point["average_score"] for point in series if point["average_score"] is not None]
    if len(points) < 2:
        return None
    return round(points[-1] - points[-2], 1)
