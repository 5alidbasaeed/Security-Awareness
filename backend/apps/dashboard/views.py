"""
Server-rendered dashboard (Django templates + HTMX + Chart.js — CLAUDE.md
invariant #11). Read-only: management actions stay in the admin. Every query
starts from scope.py so Department Managers only ever see their departments.
Numbers come from risk_scoring.analytics — the dashboard never recomputes them.
"""

from django.core.paginator import Paginator
from django.db.models import F, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.events.models import Event
from apps.risk_scoring import analytics
from apps.risk_scoring.models import RiskScoreSnapshot

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
    if field == "score":
        ordering = F("current_score").desc(nulls_last=True) if descending else F("current_score").asc(nulls_last=True)
        return queryset.order_by(ordering, "full_name")
    column = {"name": "full_name", "department": "department__name"}.get(field, "full_name")
    return queryset.order_by(f"-{column}" if descending else column, "full_name")


def _chart(labels, values, *, title):
    return {
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


# --- pages -----------------------------------------------------------------


@dashboard_access
def overview(request):
    user = request.user
    employees = visible_employees(user)
    summary = analytics.scope_summary(employees)
    series = analytics.trend_series(
        RiskScoreSnapshot.objects.filter(employee__in=employees), weeks=TREND_WEEKS
    )
    departments = [analytics.department_summary(d) for d in visible_departments(user).order_by("name")]
    departments.sort(key=lambda d: (d["average_score"] is None, -(d["average_score"] or 0)))
    highest_risk = _with_scores(employees).filter(current_score__isnull=False).order_by("-current_score", "full_name")[:5]
    recent = visible_campaigns(user).filter(gophish_campaign_id__isnull=False).order_by("-launched_at", "-id")[:5]

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
            "recent_campaigns": [_campaign_row(c) for c in recent],
        },
    )


@dashboard_access
def campaigns(request):
    queryset = visible_campaigns(request.user).select_related("target_department").order_by("-launched_at", "-id")
    status = request.GET.get("status", "")
    if status:
        queryset = queryset.filter(status=status)
    page = _page(request, queryset, per_page=15)
    rows = [_campaign_row(c) for c in page.object_list]
    template = "dashboard/_campaign_table.html" if _is_htmx(request) else "dashboard/campaigns.html"
    return render(
        request,
        template,
        {"active": "campaigns", "page": page, "rows": rows, "status": status, "statuses": Campaign.Status.choices},
    )


@dashboard_access
def campaign_detail(request, campaign_id):
    campaign = get_object_or_404(visible_campaigns(request.user).select_related("target_department"), pk=campaign_id)
    summary = analytics.campaign_summary(campaign)
    targeted = summary["targeted"]

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

    return render(
        request,
        "dashboard/campaign_detail.html",
        {
            "active": "campaigns",
            "campaign": campaign,
            "summary": summary,
            "steps": steps,
            "failures": sorted(failures.values(), key=lambda f: f["at"], reverse=True),
        },
    )


@dashboard_access
def employees(request):
    user = request.user
    query = request.GET.get("q", "").strip()
    department = request.GET.get("department", "")
    sort = request.GET.get("sort") or "-score"

    queryset = _with_scores(visible_employees(user))
    if query:
        queryset = queryset.filter(Q(full_name__icontains=query) | Q(email__icontains=query))
    if department.isdigit():
        queryset = queryset.filter(department_id=int(department))
    page = _page(request, _sorted_employees(queryset, sort))

    context = {
        "active": "employees",
        "page": page,
        "q": query,
        "department": department,
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
    events = Event.objects.filter(employee=employee).select_related("campaign").order_by("-occurred_at")[:15]
    latest = employee.risk_scores.first()  # Meta.ordering is newest first

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
            "assignments": assignments,
            "training": analytics.training_compliance(assignments),
            "events": events,
            "now": timezone.now(),
        },
    )


@dashboard_access
def departments(request):
    rows = [analytics.department_summary(d) for d in visible_departments(request.user).order_by("name")]
    return render(request, "dashboard/departments.html", {"active": "departments", "rows": rows})


@dashboard_access
def department_detail(request, department_id):
    department = get_object_or_404(visible_departments(request.user), pk=department_id)
    summary = analytics.department_summary(department)
    series = analytics.department_trend(department, weeks=TREND_WEEKS)
    members = _sorted_employees(_with_scores(department.employees.all()), "-score")[:10]
    return render(
        request,
        "dashboard/department_detail.html",
        {
            "active": "departments",
            "department": department,
            "summary": summary,
            "delta": (delta := analytics.series_delta(series)),
            "delta_direction": analytics.change_direction(delta),
            "chart": _trend_chart(series, f"{department.name} average risk score"),
            "members": members,
        },
    )


@dashboard_access
def training(request):
    user = request.user
    status = request.GET.get("status", "outstanding")
    query = request.GET.get("q", "").strip()
    now = timezone.now()

    base = visible_assignments(user)
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
    }
    template = "dashboard/_training_table.html" if _is_htmx(request) else "dashboard/training.html"
    return render(request, template, context)
