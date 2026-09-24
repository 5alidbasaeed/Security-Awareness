"""
JSON analytics endpoints — the data layer for the Phase 4.1 dashboard
(Django templates + HTMX + Chart.js). Same access rules as the admin: staff
with `view_riskscoresnapshot`, and a Department Manager only ever sees their
own departments (via core.scoping, shared with the admin mixin).
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from apps.core.scoping import visible_campaigns, visible_departments, visible_employees

from . import analytics


def analytics_access(view):
    @wraps(view)
    @login_required
    @require_GET
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_staff and request.user.has_perm("risk_scoring.view_riskscoresnapshot")):
            return HttpResponseForbidden("analytics access required")
        return view(request, *args, **kwargs)

    return wrapper


@analytics_access
def departments(request):
    summaries = [analytics.department_summary(d) for d in visible_departments(request.user).order_by("name")]
    return JsonResponse({"departments": summaries})


@analytics_access
def department_trend(request, department_id):
    department = get_object_or_404(visible_departments(request.user), pk=department_id)
    raw_weeks = request.GET.get("weeks", "12")
    weeks = min(max(int(raw_weeks), 1), 104) if raw_weeks.isdigit() else 12
    return JsonResponse({"department": department.name, "trend": analytics.department_trend(department, weeks=weeks)})


@analytics_access
def campaign(request, campaign_id):
    return JsonResponse(analytics.campaign_summary(get_object_or_404(visible_campaigns(request.user), pk=campaign_id)))


@analytics_access
def employee_history(request, employee_id):
    employee = get_object_or_404(visible_employees(request.user), pk=employee_id)
    history = analytics.score_history(employee)
    return JsonResponse(
        {
            "employee_id": employee.pk,
            "current_score": history[-1]["score"] if history else None,
            "trend": analytics.trend_direction(history),
            "history": history,
            "training": analytics.training_compliance(employee.training_assignments.all()),
        }
    )
