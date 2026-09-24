import pytest

from apps.campaigns.models import Campaign
from apps.campaigns.services import CampaignLaunchError, launch_campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.audit import log_action
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def test_cannot_launch_a_draft_campaign():
    campaign = CampaignFactory(status=Campaign.Status.DRAFT)
    client = FakePhishingEngineClient()

    with pytest.raises(CampaignLaunchError, match="must be Approved"):
        launch_campaign(campaign, actor=None, client=client)

    assert client.created_campaigns == []


def test_cannot_launch_an_already_launched_campaign():
    campaign = CampaignFactory(status=Campaign.Status.LAUNCHED)
    client = FakePhishingEngineClient()

    with pytest.raises(CampaignLaunchError, match="already launched"):
        launch_campaign(campaign, actor=None, client=client)


def test_exempt_employees_are_excluded_from_the_synced_group():
    department = DepartmentFactory()
    included = EmployeeFactory(department=department, is_exempt=False)
    EmployeeFactory(department=department, is_exempt=True)  # excluded
    campaign = CampaignFactory(status=Campaign.Status.APPROVED, target_department=department)
    client = FakePhishingEngineClient()

    launch_campaign(campaign, actor=None, client=client)

    synced = client.synced_groups[department.name]
    assert [c.email for c in synced] == [included.email]


def test_rate_limit_blocks_launch_beyond_the_configured_threshold(settings):
    settings.CAMPAIGN_LAUNCH_RATE_LIMIT = 1
    department = DepartmentFactory()
    EmployeeFactory(department=department)
    client = FakePhishingEngineClient()

    first = CampaignFactory(status=Campaign.Status.APPROVED, target_department=department)
    launch_campaign(first, actor=None, client=client)

    second = CampaignFactory(status=Campaign.Status.APPROVED, target_department=department)
    with pytest.raises(CampaignLaunchError, match="rate limit"):
        launch_campaign(second, actor=None, client=client)

    second.refresh_from_db()
    assert second.status == Campaign.Status.APPROVED  # unchanged — never launched


def test_rate_limit_only_counts_recent_launches(settings):
    settings.CAMPAIGN_LAUNCH_RATE_LIMIT = 1
    department = DepartmentFactory()
    EmployeeFactory(department=department)
    client = FakePhishingEngineClient()

    # A campaign_launched entry outside the 24h window shouldn't count.
    from datetime import timedelta

    from django.utils import timezone

    old_entry = log_action(actor=None, action="campaign_launched", target_description="old campaign")
    old_entry.occurred_at = timezone.now() - timedelta(days=2)
    old_entry.save(update_fields=["occurred_at"])

    campaign = CampaignFactory(status=Campaign.Status.APPROVED, target_department=department)
    launch_campaign(campaign, actor=None, client=client)  # should not raise

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.LAUNCHED
