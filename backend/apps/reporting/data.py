"""
Everything a report says, computed for a scope and a moment in time. Nothing
here reads risk snapshots: scores are recomputed from the immutable event log
"as of" the report date (risk_scoring.services.ScoreHistory), so any past
report can be regenerated identically. Renderers (CSV/PDF/ZIP) only format
what this module returns.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from functools import cached_property

from django.utils import timezone

from apps.risk_scoring import analytics
from apps.risk_scoring.scoring import ALGORITHM_VERSION
from apps.risk_scoring.services import ScoreHistory

MAX_TREND_POINTS = 26


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=dt_timezone.utc)


@dataclass
class ReportScope:
    """What a report covers. Built from a user's visible_* querysets, so scoping is inherited."""

    employees: object
    campaigns: object
    assignments: object
    departments: object
    label: str
    is_scoped: bool


class ReportData:
    def __init__(self, scope: ReportScope, period_start: date, period_end: date, now=None):
        self.scope = scope
        self.period_start = period_start
        self.period_end = period_end
        now = now or timezone.now()
        self.as_of = min(end_of_day(period_end), now)
        self.period_start_dt = datetime.combine(period_start, time.min, tzinfo=dt_timezone.utc)
        self.algorithm_version = ALGORITHM_VERSION

    @cached_property
    def history(self) -> ScoreHistory:
        return ScoreHistory(self.scope.employees, until=self.as_of)

    @cached_property
    def scores(self) -> dict:
        return self.history.scores_at(self.as_of)

    # -- employees -----------------------------------------------------------

    @cached_property
    def risk_rows(self) -> list[dict]:
        rows = []
        for employee in self.scope.employees.select_related("department").order_by("full_name"):
            result = self.scores.get(employee.pk)
            if result is None:
                continue  # did not exist yet at the report date
            rows.append(
                {
                    "employee": employee.full_name,
                    "email": employee.email,
                    "department": employee.department.name if employee.department else "",
                    "exempt": employee.is_exempt,
                    "score": result.score,
                    "level": analytics.risk_level(result.score),
                    "campaigns_failed": result.metrics["campaigns_failed"],
                    "repeat_failures": result.metrics["repeat_failures"],
                    "campaigns_reported": result.metrics["campaigns_reported"],
                }
            )
        return rows

    # -- campaigns -----------------------------------------------------------

    @cached_property
    def campaign_rows(self) -> list[dict]:
        launched = self.scope.campaigns.filter(
            launched_at__gte=self.period_start_dt, launched_at__lte=self.as_of
        ).select_related("target_department").order_by("launched_at")
        rows = []
        for campaign in launched:
            summary = analytics.campaign_summary(campaign, until=self.as_of)
            rows.append(
                {
                    "campaign": campaign.name,
                    "department": campaign.target_department.name if campaign.target_department else "",
                    "launched": campaign.launched_at.date().isoformat(),
                    "targeted": summary["targeted"],
                    "failed": summary["failed"],
                    "clicked": summary["clicked"],
                    "submitted_data": summary["submitted_data"],
                    "reported": summary["reported"],
                    "failure_rate_percent": summary["failure_rate_percent"],
                    "submit_rate_percent": summary["submit_rate_percent"],
                    "report_rate_percent": summary["report_rate_percent"],
                }
            )
        return rows

    # -- training (as of the report date) -----------------------------------

    def _due_as_of(self, assignment):
        """The due date that applied at the report date: extensions made later do not rewrite the past."""
        due = assignment.due_at
        extensions = list(assignment.extensions.all())  # prefetched, oldest first
        if extensions:
            due = extensions[0].previous_due_at
            for extension in extensions:
                if extension.extended_at <= self.as_of:
                    due = extension.new_due_at
        return due

    def _assignment_status(self, assignment) -> str:
        if assignment.waived_at is not None and assignment.waived_at <= self.as_of:
            return "Waived"
        completed = assignment.completed_at is not None and assignment.completed_at <= self.as_of
        if completed:
            return "Completed"
        due = self._due_as_of(assignment)
        if due is not None and due < self.as_of:
            return "Overdue"
        if assignment.started_at is not None and assignment.started_at <= self.as_of:
            return "In progress"
        return "Assigned"

    @cached_property
    def training_rows(self) -> list[dict]:
        assignments = (
            self.scope.assignments.filter(assigned_at__lte=self.as_of)
            .select_related("employee", "employee__department", "module")
            .prefetch_related("extensions")
            .order_by("assigned_at")
        )
        rows = []
        for a in assignments:
            due = self._due_as_of(a)
            rows.append(
                {
                    "employee": a.employee.full_name,
                    "email": a.employee.email,
                    "department": a.employee.department.name if a.employee.department else "",
                    "module": a.module.title,
                    "assigned": a.assigned_at.date().isoformat(),
                    "due": due.date().isoformat() if due else "",
                    "completed": a.completed_at.date().isoformat() if a.completed_at and a.completed_at <= self.as_of else "",
                    "status": self._assignment_status(a),
                    "department_id": a.employee.department_id,
                }
            )
        return rows

    @staticmethod
    def _training_totals(rows) -> dict:
        waived = sum(1 for r in rows if r["status"] == "Waived")
        assigned = len(rows) - waived  # a documented exception is neither owed nor done
        completed = sum(1 for r in rows if r["status"] == "Completed")
        overdue = sum(1 for r in rows if r["status"] == "Overdue")
        return {
            "waived": waived,
            "assigned": assigned,
            "completed": completed,
            "overdue": overdue,
            "outstanding": assigned - completed,
            "completion_rate_percent": analytics.rate(completed, assigned),
        }

    @cached_property
    def training_totals(self) -> dict:
        return self._training_totals(self.training_rows)

    # -- departments ---------------------------------------------------------

    @cached_property
    def department_rows(self) -> list[dict]:
        by_department: dict[str, list] = {}
        for row in self.risk_rows:
            by_department.setdefault(row["department"] or "No department", []).append(row)
        training_by_department: dict = {}
        for row in self.training_rows:
            training_by_department.setdefault(row["department"] or "No department", []).append(row)

        rows = []
        for name in sorted(by_department):
            people = by_department[name]
            scores = [p["score"] for p in people]
            training = self._training_totals(training_by_department.get(name, []))
            rows.append(
                {
                    "department": name,
                    "employees": len(people),
                    "average_score": round(sum(scores) / len(scores), 2),
                    "high_risk_employees": sum(1 for p in people if p["level"] == "high"),
                    "medium_risk_employees": sum(1 for p in people if p["level"] == "medium"),
                    "training_assigned": training["assigned"],
                    "training_completed": training["completed"],
                    "training_overdue": training["overdue"],
                    "training_completion_percent": training["completion_rate_percent"],
                }
            )
        return rows

    # -- headline + trend ----------------------------------------------------

    def average_score_at(self, moment):
        scores = [r.score for r in self.history.scores_at(moment).values()]
        return round(sum(scores) / len(scores), 2) if scores else None

    @cached_property
    def trend(self) -> list[dict]:
        """Weekly average score across the period, ending at the report date."""
        weeks = min(max((self.as_of - self.period_start_dt).days // 7 + 1, 1), MAX_TREND_POINTS)
        points = []
        for weeks_ago in range(weeks - 1, -1, -1):
            moment = self.as_of - timedelta(weeks=weeks_ago)
            points.append({"week_ending": moment.date().isoformat(), "average_score": self.average_score_at(moment)})
        return points

    @cached_property
    def headline(self) -> dict:
        scores = [r["score"] for r in self.risk_rows]
        opening = self.average_score_at(self.period_start_dt)
        closing = round(sum(scores) / len(scores), 2) if scores else None
        change = round(closing - opening, 2) if opening is not None and closing is not None else None
        campaigns = self.campaign_rows
        return {
            "employees": len(self.risk_rows),
            "average_score_start": opening,
            "average_score_end": closing,
            "score_change": change,
            "direction": None if change is None else analytics.change_direction(change),
            "high_risk_employees": sum(1 for r in self.risk_rows if r["level"] == "high"),
            "medium_risk_employees": sum(1 for r in self.risk_rows if r["level"] == "medium"),
            "campaigns_run": len(campaigns),
            "employees_targeted": sum(c["targeted"] for c in campaigns),
            "failure_rate_percent": analytics.rate(sum(c["failed"] for c in campaigns), sum(c["targeted"] for c in campaigns)),
            "submit_rate_percent": analytics.rate(sum(c["submitted_data"] for c in campaigns), sum(c["targeted"] for c in campaigns)),
            "report_rate_percent": analytics.rate(sum(c["reported"] for c in campaigns), sum(c["targeted"] for c in campaigns)),
            "training": self.training_totals,
        }
