"""
Resolving who a campaign targets. A campaign targets either a fixed department
or a dynamic SmartGroup; either way the exempt/inactive filter is applied last,
so no path can ever reach an exempt or deactivated employee (CLAUDE.md /
invariant on exemptions). Pure queries — no engine calls.
"""

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from apps.employees.exemptions import not_exempt_q
from apps.employees.models import Employee
from apps.events.models import Event
from apps.risk_scoring.analytics import HIGH_RISK_THRESHOLD
from apps.risk_scoring.services import latest_snapshots

from .models import SmartGroup


def _base(department):
    qs = Employee.objects.filter(not_exempt_q(), is_active=True)
    return qs.filter(department=department) if department is not None else qs


def resolve_smart_group(group: SmartGroup):
    people = _base(group.department)
    if group.rule == SmartGroup.Rule.ALL:
        return people
    if group.rule == SmartGroup.Rule.NEW_HIRES:
        cutoff = timezone.now() - timedelta(days=group.new_hire_days)
        return people.filter(created_at__gte=cutoff)
    if group.rule == SmartGroup.Rule.HIGH_RISK:
        high = latest_snapshots().filter(score__gte=HIGH_RISK_THRESHOLD).values("employee_id")
        return people.filter(pk__in=high)
    if group.rule == SmartGroup.Rule.REPEAT_CLICKERS:
        failed = (
            Event.objects.filter(event_type__in=[Event.EventType.LINK_CLICKED, Event.EventType.CREDENTIAL_ATTEMPT])
            .values("employee_id").annotate(n=Count("campaign", distinct=True))
            .filter(n__gte=2).values("employee_id")
        )
        return people.filter(pk__in=failed)
    return people.none()


def campaign_audience(campaign):
    """(queryset of employees, engine group name). Raises ValueError if no audience is set."""
    if campaign.target_smart_group_id:
        group = campaign.target_smart_group
        return resolve_smart_group(group), f"smart:{group.pk}:{group.name}"[:255]
    if campaign.target_department_id:
        return _base(campaign.target_department), campaign.target_department.name
    raise ValueError("Campaign has neither a target department nor a smart group.")
