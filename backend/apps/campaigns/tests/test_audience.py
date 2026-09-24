from datetime import timedelta

import pytest
from django.utils import timezone

from apps.campaigns.audience import campaign_audience, resolve_smart_group
from apps.campaigns.models import SmartGroup
from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.tests.factories import EventFactory
from apps.risk_scoring.services import compute_and_store_snapshot

pytestmark = pytest.mark.django_db


def _fail(employee, campaign):
    EventFactory(employee=employee, campaign=campaign, event_type="credential_attempt")


def test_repeat_clickers_are_people_who_failed_two_or_more_campaigns():
    once = EmployeeFactory()
    twice = EmployeeFactory()
    _fail(once, CampaignFactory())
    _fail(twice, CampaignFactory())
    _fail(twice, CampaignFactory())
    group = SmartGroup.objects.create(name="Repeat", rule=SmartGroup.Rule.REPEAT_CLICKERS)

    assert list(resolve_smart_group(group)) == [twice]


def test_high_risk_group_uses_the_latest_snapshot():
    risky = EmployeeFactory()
    safe = EmployeeFactory()
    _fail(risky, CampaignFactory())
    _fail(risky, CampaignFactory())
    compute_and_store_snapshot(risky)
    compute_and_store_snapshot(safe)
    group = SmartGroup.objects.create(name="High", rule=SmartGroup.Rule.HIGH_RISK)

    assert list(resolve_smart_group(group)) == [risky]


def test_new_hires_group_respects_the_window():
    old = EmployeeFactory()
    new = EmployeeFactory()
    from apps.employees.models import Employee
    Employee.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=90))
    group = SmartGroup.objects.create(name="New", rule=SmartGroup.Rule.NEW_HIRES, new_hire_days=30)

    assert list(resolve_smart_group(group)) == [new]


def test_a_smart_group_never_includes_exempt_or_inactive_people():
    EmployeeFactory(is_exempt=True)
    EmployeeFactory(is_active=False)
    active = EmployeeFactory()
    group = SmartGroup.objects.create(name="All", rule=SmartGroup.Rule.ALL)

    assert list(resolve_smart_group(group)) == [active]


def test_campaign_audience_prefers_a_smart_group_over_the_department():
    department = DepartmentFactory()
    group = SmartGroup.objects.create(name="All", rule=SmartGroup.Rule.ALL)
    campaign = CampaignFactory(target_department=department, target_smart_group=group)

    _, group_name = campaign_audience(campaign)

    assert group_name.startswith("smart:")


def test_campaign_with_no_audience_raises():
    campaign = CampaignFactory(target_department=None)
    with pytest.raises(ValueError):
        campaign_audience(campaign)
