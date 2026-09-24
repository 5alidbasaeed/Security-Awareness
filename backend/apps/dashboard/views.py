"""
Server-rendered dashboard (Django templates + HTMX + Chart.js — CLAUDE.md
invariant #11). Read-only: management actions stay in the admin. Every query
starts from scope.py so Department Managers only ever see their departments.
Numbers come from risk_scoring.analytics / risk_scoring.program_metrics — the
dashboard never recomputes them.
"""

from django.core.paginator import Paginator
from django.db.models import Count, F, Max, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignTemplate
from apps.events.models import Event
from apps.intake.models import ReportedEmail
from apps.risk_scoring import analytics, program_metrics
from apps.risk_scoring.models import RiskScoreSnapshot
from apps.training.models import TrainingAssignment, TrainingModule

from .access import dashboard_access
from .scope import visible_assignments, visible_campaigns, visible_departments, visible_employees

E = Event.EventType
TREND_WEEKS = 12


# --- helpers ---------------------------------------------------------------


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _page(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def _with_scores(employees):
    latest = RiskScoreSnapshot.objects.filter(employee=OuterRef("pk")).order_by("-computed_at", "-id")
    return employees.select_related("department").annotate(current_score=Subquery(latest.values("score")[:1]))


def _sorted_employees(queryset, sort: str):
    field = sort.lstrip("-")
    descending = sort.startswith("-")
    if field in ("score", "failed", "reported", "last_tested"):
        ordering = F(field if field != "score" else "current_score")
        ordering = ordering.desc(nulls_last=True) if descending else ordering.asc(nulls_last=True)
        return queryset.order_by(ordering, "full_name")
    column = {"name": "full_name", "department": "department__name"}.get(field, "full_name")
    return queryset.order_by(f"-{column}" if descending else column, "full_name")


def _lines_chart(labels, datasets, *, unit="%", y_max=100):
    """Multi-series line chart (rates per campaign, response speed). Series alternate solid/dashed so colour never stands alone."""
    return {
        "kind": "lines",
        "labels": labels,
        "datasets": datasets,
        "unit": unit,
        "y_max": y_max,
        "has_data": any(v is not None for d in datasets for v in d["values"]),
    }


def _chart(labels, values, *, title):
    return {
        "kind": "risk",
        "labels": labels,
        "values": values,
        "title": title,
        "medium": analytics.MEDIUM_RISK_THRESHOLD,
        "high": analytics.HIGH_RISK_THRESHOLD,
        "has_data": any(v is not None for v in values),
    }


def _trend_chart(series, title):
    labels = [point["week_ending"] for point in series]
    return _chart(labels, [point["average_score"] for point in series], title=title)


def _campaign_row(campaign):
    return {"campaign": campaign, "summary": analytics.campaign_summary(campaign)}


def _period(request):
    return program_metrics.resolve_period(request.GET.get("period"))


def _all_campaigns():
    # Outcome analysis is always scoped by *employee* (visible_employees), so a Department Manager
    # still only ever sees their own people — but an org-wide campaign they were part of still counts.
    return Campaign.objects.all()


def _vs(value, benchmark):
    """Percentage-point gap to a benchmark, or None when either side has no data."""
    return None if value is None or benchmark is None else round(value - benchmark, 1)


def _rates_chart(series):
    return _lines_chart(
        [c["campaign"].name[:28] for c in series],
        [
            {"label": "Failure rate", "values": [c["failure_rate"] for c in series], "color": "--risk-high", "dashed": False},
            {"label": "Report rate", "values": [c["report_rate"] for c in series], "color": "--risk-low", "dashed": True},
        ],
    )


# --- pages -----------------------------------------------------------------


@dashboard_access
def overview(request):
    user = request.user
    employees = visible_employees(user)
    period = _period(request)
    summary = analytics.scope_summary(employees)
    series = analytics.trend_series(
        RiskScoreSnapshot.objects.filter(employee__in=employees), weeks=TREND_WEEKS
    )
    departments = [analytics.department_summary(d) for d in visible_departments(user).order_by("name")]
    departments.sort(key=lambda d: (d["average_score"] is None, -(d["average_score"] or 0)))
    highest_risk = _with_scores(employees).filter(current_score__isnull=False).order_by("-current_score", "full_name")[:5]
    from apps.engagement.points import leaderboard
    top_reporters = leaderboard(employees, limit=5)
    improvement = analytics.program_improvement(RiskScoreSnapshot.objects.filter(employee__in=employees))
    recent = visible_campaigns(user).filter(gophish_campaign_id__isnull=False).order_by("-launched_at", "-id")[:5]

    program = program_metrics.analyse(employees, _all_campaigns(), period["since"])["org"]
    suppressed = program["suppressed"]
    campaign_rates = [] if suppressed else program_metrics.campaign_series(
        employees, visible_campaigns(user), period["since"]
    )
    triage_open = None
    if user.has_perm("intake.view_reportedemail"):
        triage_open = ReportedEmail.objects.filter(verdict=ReportedEmail.Verdict.NEW, reporter__in=employees).count()

    return render(
        request,
        "dashboard/overview.html",
        {
            "active": "overview",
            "summary": summary,
            "delta": (delta := analytics.series_delta(series)),
            "delta_direction": analytics.change_direction(delta),
            "chart": _trend_chart(series, "Average risk score"),
            "departments": departments,
            "highest_risk": highest_risk,
            "top_reporters": top_reporters,
            "improvement": improvement,
            "recent_campaigns": [_campaign_row(c) for c in recent],
            "period": period,
            "periods": program_metrics.PERIODS,
            "program": program,
            "scorecard": [] if suppressed else program_metrics.scorecard(
                program, summary["training"]["completion_rate_percent"]
            ),
            "rates_chart": _rates_chart(campaign_rates),
            "distribution": program_metrics.risk_distribution(employees),
            "attention": {} if suppressed else program_metrics.attention_counts(employees),
            "triage_open": triage_open,
        },
    )


@dashboard_access
def campaigns(request):
    user = request.user
    period = _period(request)
    queryset = visible_campaigns(user).select_related("target_department").order_by("-launched_at", "-id")
    status = request.GET.get("status", "")
    department = request.GET.get("department", "")
    query = request.GET.get("q", "").strip()
    if status:
        queryset = queryset.filter(status=status)
    if department.isdigit():
        queryset = queryset.filter(target_department_id=int(department))
    if query:
        queryset = queryset.filter(name__icontains=query)
    if period["since"] is not None and request.GET.get("period"):
        # An explicit period narrows the list; the bare page still lists everything, as it always has.
        queryset = queryset.filter(launched_at__gte=period["since"])
    page = _page(request, queryset, per_page=15)
    difficulty = dict(
        CampaignTemplate.objects.filter(template_name__in=[c.template_name for c in page.object_list])
        .values_list("template_name", "difficulty")
    )
    difficulty_labels = dict(CampaignTemplate.Difficulty.choices)
    rows = []
    for campaign in page.object_list:
        row = _campaign_row(campaign)
        summary = row["summary"]
        row["difficulty"] = difficulty_labels.get(difficulty.get(campaign.template_name), "")
        row["resilience"] = round(summary["reported"] / summary["failed"], 2) if summary["failed"] else None
        rows.append(row)
    template = "dashboard/_campaign_table.html" if _is_htmx(request) else "dashboard/campaigns.html"
    return render(
        request,
        template,
        {
            "active": "campaigns", "page": page, "rows": rows, "status": status, "statuses": Campaign.Status.choices,
            "department": department, "departments": visible_departments(user).order_by("name"), "q": query,
            "period": period, "periods": program_metrics.PERIODS, "period_chosen": bool(request.GET.get("period")),
            "totals": program_metrics.totals(visible_employees(user), queryset),
        },
    )


@dashboard_access
def campaign_detail(request, campaign_id):
    user = request.user
    campaign = get_object_or_404(visible_campaigns(user).select_related("target_department"), pk=campaign_id)
    summary = analytics.campaign_summary(campaign)
    targeted = summary["targeted"]
    employees = visible_employees(user)

    funnel = [
        ("Sent", targeted),
        ("Delivered", summary["delivered"]),
        ("Clicked the link", summary["clicked"]),
        ("Submitted data", summary["submitted_data"]),
        ("Reported the email", summary["reported"]),
    ]
    steps = [
        {"label": label, "value": value, "percent": analytics.rate(value, targeted), "max": targeted}
        for label, value in funnel
    ]

    failures: dict[int, dict] = {}
    failing_events = (
        Event.objects.filter(campaign=campaign, event_type__in=[E.LINK_CLICKED, E.CREDENTIAL_ATTEMPT])
        .select_related("employee", "employee__department")
        .order_by("occurred_at")
    )
    for event in failing_events:
        outcome = "Submitted data" if event.event_type == E.CREDENTIAL_ATTEMPT else "Clicked the link"
        current = failures.get(event.employee_id)
        if current is None or outcome == "Submitted data":
            failures[event.employee_id] = {"employee": event.employee, "outcome": outcome, "at": event.occurred_at}

    breakdown = program_metrics.campaign_breakdown(campaign, employees)
    benchmark = program_metrics.totals(employees, visible_campaigns(user))
    timeline = breakdown["timeline"]

    return render(
        request,
        "dashboard/campaign_detail.html",
        {
            "active": "campaigns",
            "campaign": campaign,
            "summary": summary,
            "steps": steps,
            "failures": sorted(failures.values(), key=lambda f: f["at"], reverse=True),
            "unit": breakdown["unit"],
            "benchmark": benchmark,
            "failure_vs": _vs(summary["failure_rate_percent"], benchmark["failure_rate"]),
            "report_vs": _vs(summary["report_rate_percent"], benchmark["report_rate"]),
            "departments": breakdown["departments"],
            "reporters": breakdown["reporters"],
            "repeat_failures": breakdown["repeat_failures"],
            "timeline": timeline,
            "timeline_chart": _lines_chart(
                timeline["labels"],
                [
                    {"label": "Clicked or submitted", "values": timeline["failed"], "color": "--risk-high", "dashed": False},
                    {"label": "Reported", "values": timeline["reported"], "color": "--risk-low", "dashed": True},
                ],
            ),
            "template_info": CampaignTemplate.objects.filter(template_name=campaign.template_name).first(),
            "follow_up": program_metrics.follow_up_training(campaign, employees),
        },
    )


@dashboard_access
def employees(request):
    user = request.user
    query = request.GET.get("q", "").strip()
    department = request.GET.get("department", "")
    sort = request.GET.get("sort") or "-score"
    focus = request.GET.get("focus", "")
    level = request.GET.get("level", "")
    if focus not in dict(program_metrics.FOCUS_CHOICES):
        focus = ""
    if level not in dict(program_metrics.LEVEL_CHOICES):
        level = ""

    queryset = program_metrics.annotate_activity(visible_employees(user))
    if query:
        queryset = queryset.filter(Q(full_name__icontains=query) | Q(email__icontains=query))
    if department.isdigit():
        queryset = queryset.filter(department_id=int(department))
    queryset = program_metrics.apply_level(program_metrics.apply_focus(queryset, focus), level)
    page = _page(request, _sorted_employees(queryset, sort))

    context = {
        "active": "employees",
        "page": page,
        "q": query,
        "department": department,
        "focus": focus,
        "level": level,
        "focus_choices": program_metrics.FOCUS_CHOICES,
        "level_choices": program_metrics.LEVEL_CHOICES,
        "default_sort": "-score",
        "departments": visible_departments(user).order_by("name"),
    }
    template = "dashboard/_employee_table.html" if _is_htmx(request) else "dashboard/employees.html"
    return render(request, template, context)


@dashboard_access
def employee_detail(request, employee_id):
    employee = get_object_or_404(_with_scores(visible_employees(request.user)), pk=employee_id)
    history = analytics.score_history(employee)
    labels = [h["computed_at"].strftime("%b %d") for h in history]
    assignments = employee.training_assignments.select_related("module").order_by("-assigned_at")
    assignment_rows = assignments.annotate(best_score=Max("quiz_attempts__score_percent"), attempts=Count("quiz_attempts"))
    events = Event.objects.filter(employee=employee).select_related("campaign").order_by("-occurred_at")[:15]
    latest = employee.risk_scores.first()  # Meta.ordering is newest first
    tests = program_metrics.employee_history(employee)
    failed = [t for t in tests if t["outcome"] in ("submitted", "clicked")]
    fail_speeds = [t["seconds_to_fail"] for t in failed if t["seconds_to_fail"] is not None]

    return render(
        request,
        "dashboard/employee_detail.html",
        {
            "active": "employees",
            "employee": employee,
            "trend": analytics.trend_direction(history),
            "metrics": latest.contributing_metrics if latest else None,
            "algorithm_version": latest.algorithm_version if latest else None,
            "chart": _chart(labels, [h["score"] for h in history], title="Risk score"),
            "assignments": assignment_rows,
            "training": analytics.training_compliance(assignments),
            "events": events,
            "now": timezone.now(),
            "tests": tests,
            "tests_failed": len(failed),
            "tests_reported": sum(1 for t in tests if t["outcome"] == "reported" or t["also_reported"]),
            "last_tested": max((t["sent_at"] for t in tests if t["sent_at"]), default=None),
            "fastest_fail_seconds": min(fail_speeds) if fail_speeds else None,
            "real_reports": ReportedEmail.objects.filter(reporter=employee).count(),
            "is_repeat": len(failed) >= 2,
        },
    )


DEPARTMENT_SORTS = {
    "name": lambda r: r["name"].lower(),
    "risk": lambda r: r["average_score"],
    "failure": lambda r: r["metrics"]["failure_rate"],
    "report": lambda r: r["metrics"]["report_rate"],
    "coverage": lambda r: r["metrics"]["coverage_percent"],
    "training": lambda r: r["training"]["completion_rate_percent"],
}


@dashboard_access
def departments(request):
    user = request.user
    period = _period(request)
    org = program_metrics.analyse(visible_employees(user), _all_campaigns(), period["since"])
    rows = []
    for department in visible_departments(user).order_by("name"):
        metrics = org["departments"].get(department.pk, program_metrics.EMPTY)
        delta = analytics.series_delta(analytics.department_trend(department, weeks=TREND_WEEKS))
        rows.append({
            **analytics.department_summary(department),
            "metrics": metrics,
            "failure_vs_org": _vs(metrics["failure_rate"], org["org"]["failure_rate"]),
            "delta": delta,
            "delta_direction": analytics.change_direction(delta),
        })

    sort = request.GET.get("sort") or "name"
    key = DEPARTMENT_SORTS.get(sort.lstrip("-"), DEPARTMENT_SORTS["name"])
    present = sorted((r for r in rows if key(r) is not None), key=key, reverse=sort.startswith("-"))
    rows = present + [r for r in rows if key(r) is None]  # departments with no data always sink to the bottom
    return render(
        request,
        "dashboard/departments.html",
        {"active": "departments", "rows": rows, "org": org["org"], "period": period, "periods": program_metrics.PERIODS,
         "default_sort": "name", "targets": program_metrics.targets()},
    )


@dashboard_access
def department_detail(request, department_id):
    user = request.user
    department = get_object_or_404(visible_departments(user), pk=department_id)
    period = _period(request)
    summary = analytics.department_summary(department)
    series = analytics.department_trend(department, weeks=TREND_WEEKS)
    members = _sorted_employees(_with_scores(department.employees.all()), "-score")[:10]
    analysis = program_metrics.analyse(visible_employees(user), _all_campaigns(), period["since"])
    org = analysis["org"]
    metrics = analysis["departments"].get(department.pk, program_metrics.EMPTY)
    people = department.employees.all()
    campaign_rows = program_metrics.campaign_results(
        people, Campaign.objects.filter(events__employee__in=people).distinct(), period["since"], limit=10
    )
    repeat = program_metrics.apply_focus(program_metrics.annotate_activity(people), "repeat").order_by("-failed", "full_name")[:10]
    breakdown = program_metrics.training_breakdown(TrainingAssignment.objects.filter(employee__in=people))
    delta = analytics.series_delta(series)
    return render(
        request,
        "dashboard/department_detail.html",
        {
            "active": "departments",
            "department": department,
            "summary": summary,
            "delta": delta,
            "delta_direction": analytics.change_direction(delta),
            "chart": _trend_chart(series, f"{department.name} average risk score"),
            "members": members,
            "period": period,
            "periods": program_metrics.PERIODS,
            "metrics": metrics,
            "org": org,
            "failure_vs_org": _vs(metrics["failure_rate"], org["failure_rate"]),
            "report_vs_org": _vs(metrics["report_rate"], org["report_rate"]),
            "campaign_rows": campaign_rows,
            "repeat_offenders": repeat,
            "modules": breakdown["modules"],
            "distribution": program_metrics.risk_distribution(people),
        },
    )


@dashboard_access
def training(request):
    user = request.user
    status = request.GET.get("status", "outstanding")
    query = request.GET.get("q", "").strip()
    department = request.GET.get("department", "")
    module = request.GET.get("module", "")
    now = timezone.now()

    base = visible_assignments(user)
    if department.isdigit():
        base = base.filter(employee__department_id=int(department))
    if module.isdigit():
        base = base.filter(module_id=int(module))
    compliance = analytics.training_compliance(base)

    queryset = base.select_related("employee", "employee__department", "module")
    if status == "completed":
        queryset = queryset.filter(completed_at__isnull=False)
    elif status == "overdue":
        queryset = queryset.filter(completed_at__isnull=True, due_at__lt=now)
    elif status == "outstanding":
        queryset = queryset.filter(completed_at__isnull=True)
    if query:
        queryset = queryset.filter(Q(employee__full_name__icontains=query) | Q(employee__email__icontains=query))
    page = _page(request, queryset.order_by("due_at", "-assigned_at"))

    context = {
        "active": "training",
        "page": page,
        "status": status,
        "q": query,
        "compliance": compliance,
        "now": now,
        "department": department,
        "module": module,
        "departments": visible_departments(user).order_by("name"),
        "modules": TrainingModule.objects.order_by("title"),
        "target": program_metrics.targets()["training_completion"],
    }
    if not _is_htmx(request):
        context["breakdown"] = program_metrics.training_breakdown(base, now=now)
    return render(request, "dashboard/_training_table.html" if _is_htmx(request) else "dashboard/training.html", context)
