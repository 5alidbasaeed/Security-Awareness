"""
Program-level (GRC) metrics — what an auditor or a risk owner asks of a phishing
program that a risk score alone doesn't answer: who was actually tested and when
(coverage), how fast people fail or report (response time), whether reporting
outpaces failing (resilience), who keeps failing (repeat offenders), whether
training is finished on time, and how each unit compares with the organisation.

Everything here is a read over the immutable event log and the training tables;
nothing writes. The unit of analysis is one (employee, campaign) pair, so an
employee who clicks a link three times still counts as one failure on that
campaign — the same rule `analytics.campaign_summary` uses. `email_opened` is
never read (invariant #5) and no password field is ever touched (invariant #4).
"""

from collections import defaultdict
from datetime import timedelta
from statistics import median

from django.conf import settings
from django.db.models import Avg, Count, Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.events.models import Event
from apps.training.models import QuizAttempt, TrainingAssignment

from .analytics import HIGH_RISK_THRESHOLD, MEDIUM_RISK_THRESHOLD, meets_min_cohort, rate
from .models import RiskScoreSnapshot

E = Event.EventType
SENT = E.EMAIL_SENT.value
CLICK = E.LINK_CLICKED.value
SUBMIT = E.CREDENTIAL_ATTEMPT.value
REPORT = E.PHISHING_REPORTED.value
FAIL_TYPES = (CLICK, SUBMIT)
TRACKED = (SENT, CLICK, SUBMIT, REPORT)  # email_opened is deliberately absent

PERIODS = (
    ("30", "Last 30 days", 30),
    ("90", "Last 90 days", 90),
    ("180", "Last 6 months", 180),
    ("365", "Last 12 months", 365),
    ("all", "All time", None),
)
DEFAULT_PERIOD = "90"

# Buckets for "how quickly did people fail / report", in minutes after the email was sent.
RESPONSE_CHECKPOINTS = ((5, "5 min"), (15, "15 min"), (30, "30 min"), (60, "1 h"), (120, "2 h"), (240, "4 h"),
                        (480, "8 h"), (1440, "24 h"), (2880, "48 h"))

FOCUS_CHOICES = (
    ("", "Everyone"),
    ("repeat", "Repeat failures (2+ campaigns)"),
    ("high_risk_untrained", "High risk, no training completed"),
    ("overdue_training", "Training overdue"),
    ("never_tested", "Never tested"),
    ("exempt", "Exempt from simulations"),
    ("inactive", "Inactive (left the company)"),
)
LEVEL_CHOICES = (("", "All risk levels"), ("high", "High"), ("medium", "Medium"), ("low", "Low"), ("unscored", "Not scored"))


# --- periods ---------------------------------------------------------------


def resolve_period(value, now=None) -> dict:
    """`?period=` -> {key, label, since}. Unknown values fall back to the default rather than erroring."""
    now = now or timezone.now()
    table = {key: (label, days) for key, label, days in PERIODS}
    key = value if value in table else DEFAULT_PERIOD
    label, days = table[key]
    return {"key": key, "label": label, "since": now - timedelta(days=days) if days else None}


# --- the (employee, campaign) unit -----------------------------------------


def outcomes(events) -> dict:
    """{(employee_id, campaign_id): {event_type: earliest occurred_at}} for the tracked types only."""
    result: dict = {}
    for employee_id, campaign_id, event_type, at in events.filter(event_type__in=TRACKED).values_list(
        "employee_id", "campaign_id", "event_type", "occurred_at"
    ):
        record = result.setdefault((employee_id, campaign_id), {})
        if event_type not in record or at < record[event_type]:
            record[event_type] = at
    return result


def _fail_at(record):
    times = [record[t] for t in FAIL_TYPES if t in record]
    return min(times) if times else None


def _seconds(later, earlier):
    if later is None or earlier is None:
        return None
    delta = (later - earlier).total_seconds()
    return delta if delta >= 0 else None  # a clock-skewed event must not produce a negative "speed"


def _median(values):
    values = [v for v in values if v is not None]
    return median(values) if values else None


def tally(records) -> dict:
    """Rates and speeds over an iterable of per-(employee, campaign) records."""
    tested = failed = submitted = reported = 0
    fail_times, report_times = [], []
    for record in records:
        tested += 1
        fail_at = _fail_at(record)
        if fail_at is not None:
            failed += 1
            fail_times.append(_seconds(fail_at, record.get(SENT)))
        if SUBMIT in record:
            submitted += 1
        if REPORT in record:
            reported += 1
            report_times.append(_seconds(record[REPORT], record.get(SENT)))
    return {
        "tested": tested,
        "failed": failed,
        "submitted": submitted,
        "reported": reported,
        "failure_rate": rate(failed, tested),
        "submit_rate": rate(submitted, tested),
        "report_rate": rate(reported, tested),
        # Reports per failure (Cofense's "resilience" idea): above 1.0 means people report more than they fall for it.
        "resilience_ratio": round(reported / failed, 2) if failed else None,
        "median_seconds_to_fail": _median(fail_times),
        "median_seconds_to_report": _median(report_times),
    }


def _no_action(records) -> int:
    return sum(1 for r in records if _fail_at(r) is None and REPORT not in r)


def _scope_pairs(employees, campaigns, since):
    rows = list(employees.values_list("id", "department_id", "is_active", "is_exempt"))
    events = Event.objects.filter(employee__in=employees, campaign__in=campaigns)
    if since is not None:
        events = events.filter(campaign__launched_at__gte=since)
    return rows, outcomes(events)


def _unit_metrics(items, eligible_ids) -> dict:
    """items: [((employee_id, campaign_id), record)]; eligible_ids: who *should* be getting tested."""
    records = [record for _, record in items]
    result = tally(records)
    result["no_action"] = _no_action(records)
    tested_people = {employee_id for (employee_id, _), _ in items}
    covered = len(tested_people & eligible_ids)
    failed_campaigns: dict = defaultdict(set)
    for (employee_id, campaign_id), record in items:
        if _fail_at(record) is not None:
            failed_campaigns[employee_id].add(campaign_id)
    result.update(
        eligible=len(eligible_ids),
        covered=covered,
        coverage_percent=rate(covered, len(eligible_ids)),
        repeat_offenders=sum(1 for c in failed_campaigns.values() if len(c) >= 2),
        campaigns_run=len({campaign_id for (_, campaign_id), _ in items}),
        suppressed=False,
    )
    return result


def _suppressed() -> dict:
    return {"suppressed": True, "tested": 0, "failed": 0, "reported": 0, "submitted": 0, "no_action": 0,
            "failure_rate": None, "submit_rate": None, "report_rate": None, "resilience_ratio": None,
            "median_seconds_to_fail": None, "median_seconds_to_report": None, "eligible": 0, "covered": 0,
            "coverage_percent": None, "repeat_offenders": 0, "campaigns_run": 0}


def analyse(employees, campaigns, since=None) -> dict:
    """
    One pass over the events for `employees` x `campaigns` (both already row-scoped by the caller):
    {"org": metrics, "departments": {department_id or None: metrics}}. Units smaller than
    MIN_REPORTING_COHORT come back suppressed, exactly like the rest of the analytics (privacy).
    """
    rows, pairs = _scope_pairs(employees, campaigns, since)
    department_of = {employee_id: department_id for employee_id, department_id, _, _ in rows}
    eligible = {employee_id: department_id for employee_id, department_id, active, exempt in rows if active and not exempt}

    by_department: dict = defaultdict(list)
    for key, record in pairs.items():
        by_department[department_of.get(key[0])].append((key, record))
    people_per_department: dict = defaultdict(int)
    for _, department_id, _, _ in rows:
        people_per_department[department_id] += 1

    if not meets_min_cohort(len(rows)):
        org = _suppressed()
    else:
        org = _unit_metrics(list(pairs.items()), set(eligible))
    departments = {}
    for department_id, count in people_per_department.items():
        if not meets_min_cohort(count):
            departments[department_id] = _suppressed()
            continue
        eligible_ids = {e for e, d in eligible.items() if d == department_id}
        departments[department_id] = _unit_metrics(by_department.get(department_id, []), eligible_ids)
    return {"org": org, "departments": departments}


# --- targets ---------------------------------------------------------------


def targets() -> dict:
    return {
        "failure_rate": settings.TARGET_MAX_FAILURE_RATE,
        "report_rate": settings.TARGET_MIN_REPORT_RATE,
        "training_completion": settings.TARGET_MIN_TRAINING_COMPLETION,
        "coverage": settings.TARGET_MIN_COVERAGE,
    }


def grade(value, target, *, higher_is_better) -> str:
    """met / missed / no_data — always rendered with a word, never a colour alone."""
    if value is None:
        return "no_data"
    ok = value >= target if higher_is_better else value <= target
    return "met" if ok else "missed"


def scorecard(org: dict, training_completion) -> list[dict]:
    goal = targets()
    spec = (
        ("Failure rate", org["failure_rate"], goal["failure_rate"], False, "at most"),
        ("Report rate", org["report_rate"], goal["report_rate"], True, "at least"),
        ("Test coverage", org["coverage_percent"], goal["coverage"], True, "at least"),
        ("Training completion", training_completion, goal["training_completion"], True, "at least"),
    )
    return [
        {"label": label, "value": value, "target": target, "qualifier": word,
         "status": grade(value, target, higher_is_better=higher)}
        for label, value, target, higher, word in spec
    ]


# --- per-campaign views ----------------------------------------------------


def campaign_results(employees, campaigns, since=None, limit=None) -> list[dict]:
    """Per-campaign rates over `employees`' outcomes, newest first (`campaigns` and `employees` already row-scoped)."""
    picked = campaigns.filter(launched_at__isnull=False)
    if since is not None:
        picked = picked.filter(launched_at__gte=since)
    picked = picked.order_by("-launched_at", "-id")
    picked = list(picked[:limit] if limit else picked)
    grouped: dict = defaultdict(list)
    for (_, campaign_id), record in outcomes(Event.objects.filter(campaign__in=picked, employee__in=employees)).items():
        grouped[campaign_id].append(record)
    return [{"campaign": c, **tally(grouped.get(c.pk, [])), "no_action": _no_action(grouped.get(c.pk, []))} for c in picked]


def campaign_series(employees, campaigns, since=None, limit=12) -> list[dict]:
    """The most recent launched campaigns, oldest first — the 'are we improving' chart."""
    return list(reversed(campaign_results(employees, campaigns, since, limit)))


def totals(employees, campaigns) -> dict:
    """Rates across every campaign in `campaigns` for `employees` (the benchmark a single campaign is compared to)."""
    pairs = outcomes(Event.objects.filter(campaign__in=campaigns, employee__in=employees))
    return {**tally(pairs.values()), "no_action": _no_action(pairs.values()), "campaigns_run": len({c for _, c in pairs})}


def response_timeline(records) -> dict:
    """Cumulative % of tested employees who had failed / reported N minutes after the email was sent."""
    records = list(records)
    total = len(records)
    fail_gaps = [_seconds(_fail_at(r), r.get(SENT)) for r in records if _fail_at(r) is not None]
    report_gaps = [_seconds(r[REPORT], r.get(SENT)) for r in records if REPORT in r]
    fail_gaps, report_gaps = [g for g in fail_gaps if g is not None], [g for g in report_gaps if g is not None]

    def cumulative(gaps):
        return [rate(sum(1 for g in gaps if g <= minutes * 60), total) for minutes, _ in RESPONSE_CHECKPOINTS]

    return {"labels": [label for _, label in RESPONSE_CHECKPOINTS], "failed": cumulative(fail_gaps),
            "reported": cumulative(report_gaps), "has_data": bool(fail_gaps or report_gaps)}


def campaign_breakdown(campaign, employees) -> dict:
    """Everything the campaign page needs beyond the funnel. `employees` is the caller's row-scoped queryset."""
    pairs = outcomes(Event.objects.filter(campaign=campaign, employee__in=employees))
    people = {
        e.pk: e
        for e in employees.filter(pk__in={emp for emp, _ in pairs}).select_related("department")
    }
    items = [(key, record) for key, record in pairs.items() if key[0] in people]
    records = [record for _, record in items]
    unit = _unit_metrics(items, {key[0] for key in pairs})

    by_department: dict = defaultdict(list)
    for (employee_id, _), record in items:
        department = people[employee_id].department
        by_department[department.name if department else "No department"].append(record)
    departments = []
    for name, group in by_department.items():
        t = tally(group)
        departments.append({"name": name, **t, "no_action": _no_action(group)})
    departments.sort(key=lambda d: (d["failure_rate"] is None, -(d["failure_rate"] or 0), d["name"]))

    reporters = sorted(
        ((people[emp], record[REPORT], _seconds(record[REPORT], record.get(SENT)))
         for (emp, _), record in items if REPORT in record),
        key=lambda r: r[1],
    )

    repeat_ids = set()
    if campaign.launched_at:
        failed_here = {emp for (emp, _), record in items if _fail_at(record) is not None}
        repeat_ids = set(
            Event.objects.filter(employee_id__in=failed_here, event_type__in=FAIL_TYPES,
                                 campaign__launched_at__lt=campaign.launched_at)
            .exclude(campaign=campaign).values_list("employee_id", flat=True)
        )
    return {
        "unit": unit,
        "departments": departments,
        "reporters": reporters[:10],
        "repeat_failures": len(repeat_ids),
        "timeline": response_timeline(records),
    }


def follow_up_training(campaign, employees) -> dict:
    """Training that this campaign's failures triggered, and whether it got done."""
    assignments = TrainingAssignment.objects.filter(triggered_by_event__campaign=campaign, employee__in=employees)
    return _compliance(assignments)


def _compliance(assignments, now=None) -> dict:
    now = now or timezone.now()
    totals = assignments.aggregate(
        assigned=Count("id"),
        completed=Count("id", filter=Q(completed_at__isnull=False)),
        overdue=Count("id", filter=Q(completed_at__isnull=True, due_at__lt=now)),
    )
    totals["completion_rate_percent"] = rate(totals["completed"], totals["assigned"])
    return totals


# --- per-employee views ----------------------------------------------------


def employee_history(employee) -> list[dict]:
    """One row per campaign the person was tested on, newest first."""
    records: dict = {}
    campaigns: dict = {}
    for event in Event.objects.filter(employee=employee, event_type__in=TRACKED).select_related("campaign"):
        campaigns[event.campaign_id] = event.campaign
        record = records.setdefault(event.campaign_id, {})
        if event.event_type not in record or event.occurred_at < record[event.event_type]:
            record[event.event_type] = event.occurred_at
    assignments = {
        a.triggered_by_event.campaign_id: a
        for a in employee.training_assignments.select_related("triggered_by_event", "module").order_by("assigned_at")
        if a.triggered_by_event_id
    }
    rows = []
    for campaign_id, record in records.items():
        fail_at = _fail_at(record)
        if SUBMIT in record:
            outcome = "submitted"
        elif CLICK in record:
            outcome = "clicked"
        elif REPORT in record:
            outcome = "reported"
        else:
            outcome = "no_action"
        rows.append({
            "campaign": campaigns[campaign_id],
            "sent_at": record.get(SENT),
            "when": record.get(SENT) or fail_at or record.get(REPORT),
            "outcome": outcome,
            "also_reported": REPORT in record and fail_at is not None,
            "seconds_to_fail": _seconds(fail_at, record.get(SENT)),
            "seconds_to_report": _seconds(record.get(REPORT), record.get(SENT)),
            "assignment": assignments.get(campaign_id),
        })
    rows.sort(key=lambda r: r["when"] or timezone.now(), reverse=True)
    return rows


# --- employee list annotations & focus filters -----------------------------


def _distinct_campaigns(event_types):
    return Coalesce(
        Subquery(
            Event.objects.filter(employee=OuterRef("pk"), event_type__in=event_types)
            .order_by().values("employee").annotate(n=Count("campaign", distinct=True)).values("n")[:1]
        ),
        Value(0),
    )


def annotate_activity(employees):
    """Per-person columns for the employee list — all subqueries, so no GROUP BY and paginating stays cheap."""
    latest_score = RiskScoreSnapshot.objects.filter(employee=OuterRef("pk")).order_by("-computed_at", "-id")
    now = timezone.now()
    assignments = TrainingAssignment.objects.filter(employee=OuterRef("pk"))
    return employees.select_related("department").annotate(
        current_score=Subquery(latest_score.values("score")[:1]),
        tests=_distinct_campaigns([SENT]),
        failed=_distinct_campaigns(FAIL_TYPES),
        reported=_distinct_campaigns([REPORT]),
        last_tested=Subquery(
            Event.objects.filter(employee=OuterRef("pk"), event_type=SENT).order_by("-occurred_at").values("occurred_at")[:1]
        ),
        has_overdue=Exists(assignments.filter(completed_at__isnull=True, due_at__lt=now)),
        has_outstanding=Exists(assignments.filter(completed_at__isnull=True)),
        has_completed=Exists(assignments.filter(completed_at__isnull=False)),
    )


def apply_focus(annotated, focus: str):
    if focus == "repeat":
        return annotated.filter(is_active=True, failed__gte=2)
    if focus == "high_risk_untrained":
        return annotated.filter(is_active=True, current_score__gte=HIGH_RISK_THRESHOLD, has_completed=False)
    if focus == "overdue_training":
        return annotated.filter(is_active=True, has_overdue=True)
    if focus == "never_tested":
        return annotated.filter(is_active=True, is_exempt=False, tests=0)
    if focus == "exempt":
        return annotated.filter(is_exempt=True)
    if focus == "inactive":
        return annotated.filter(is_active=False)
    return annotated


def apply_level(annotated, level: str):
    if level == "high":
        return annotated.filter(current_score__gte=HIGH_RISK_THRESHOLD)
    if level == "medium":
        return annotated.filter(current_score__gte=MEDIUM_RISK_THRESHOLD, current_score__lt=HIGH_RISK_THRESHOLD)
    if level == "low":
        return annotated.filter(current_score__lt=MEDIUM_RISK_THRESHOLD)
    if level == "unscored":
        return annotated.filter(current_score__isnull=True)
    return annotated


def attention_counts(employees) -> dict:
    """The 'needs attention' list: how many people sit in each actionable bucket."""
    annotated = annotate_activity(employees)
    return {focus: apply_focus(annotated, focus).count()
            for focus in ("repeat", "high_risk_untrained", "overdue_training", "never_tested")}


def risk_distribution(employees) -> dict:
    """People per risk level, from each person's latest snapshot. Unscored = no snapshot yet."""
    scores = list(
        RiskScoreSnapshot.objects.filter(employee__in=employees)
        .order_by("employee_id", "-computed_at", "-id").distinct("employee_id").values_list("score", flat=True)
    )
    high = sum(1 for s in scores if s >= HIGH_RISK_THRESHOLD)
    medium = sum(1 for s in scores if MEDIUM_RISK_THRESHOLD <= s < HIGH_RISK_THRESHOLD)
    low = len(scores) - high - medium
    total = employees.count()
    return {"high": high, "medium": medium, "low": low, "unscored": max(total - len(scores), 0), "total": total}


# --- training --------------------------------------------------------------


def training_breakdown(assignments, now=None) -> dict:
    """Module-by-module, department-by-department and overdue-aging views of a set of assignments."""
    now = now or timezone.now()
    module_rows: dict = {}
    for row in assignments.values("module_id", "module__title").annotate(
        assigned=Count("id"),
        completed=Count("id", filter=Q(completed_at__isnull=False)),
        overdue=Count("id", filter=Q(completed_at__isnull=True, due_at__lt=now)),
    ):
        module_rows[row["module_id"]] = {**row, "completion_rate_percent": rate(row["completed"], row["assigned"]),
                                         "avg_score": None, "pass_rate": None, "median_days": None}
    for row in QuizAttempt.objects.filter(assignment__in=assignments).values("assignment__module_id").annotate(
        avg=Avg("score_percent"), attempts=Count("id"), passed=Count("id", filter=Q(passed=True))
    ):
        target = module_rows.get(row["assignment__module_id"])
        if target:
            target["avg_score"] = round(row["avg"], 1)
            target["pass_rate"] = rate(row["passed"], row["attempts"])

    durations: dict = defaultdict(list)
    on_time = late = 0
    for module_id, assigned_at, due_at, completed_at in assignments.filter(completed_at__isnull=False).values_list(
        "module_id", "assigned_at", "due_at", "completed_at"
    ):
        durations[module_id].append((completed_at - assigned_at).total_seconds() / 86400)
        if due_at:
            on_time, late = (on_time + 1, late) if completed_at <= due_at else (on_time, late + 1)
    for module_id, days in durations.items():
        if module_id in module_rows:
            module_rows[module_id]["median_days"] = round(median(days), 1)

    aging = {"1-7 days": 0, "8-30 days": 0, "31+ days": 0}
    for (due_at,) in assignments.filter(completed_at__isnull=True, due_at__lt=now).values_list("due_at"):
        days = (now - due_at).days
        aging["1-7 days" if days <= 7 else "8-30 days" if days <= 30 else "31+ days"] += 1

    departments: dict = {}
    for row in assignments.values("employee__department_id", "employee__department__name").annotate(
        assigned=Count("id"),
        completed=Count("id", filter=Q(completed_at__isnull=False)),
        overdue=Count("id", filter=Q(completed_at__isnull=True, due_at__lt=now)),
    ):
        departments[row["employee__department_id"]] = {
            "id": row["employee__department_id"], "name": row["employee__department__name"] or "No department",
            "assigned": row["assigned"], "completed": row["completed"], "overdue": row["overdue"],
            "completion_rate_percent": rate(row["completed"], row["assigned"]),
        }
    return {
        "modules": sorted(module_rows.values(), key=lambda m: (-m["overdue"], m["module__title"])),
        "departments": sorted(departments.values(), key=lambda d: (d["completion_rate_percent"] is None,
                                                                    d["completion_rate_percent"] or 0, d["name"])),
        "aging": aging,
        "on_time_rate_percent": rate(on_time, on_time + late),
        "completed_on_time": on_time,
        "completed_late": late,
        "median_days_to_complete": _median([d for days in durations.values() for d in days]),
    }


EMPTY = _unit_metrics([], set())  # a unit with nobody in it; read-only
