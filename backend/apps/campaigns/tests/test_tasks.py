from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.campaigns.tasks import launch_scheduled_campaigns
from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def test_due_approved_campaign_launches_through_the_same_path_as_the_admin_action():
    department = DepartmentFactory()
    EmployeeFactory(department=department)
    campaign = CampaignFactory(
        status=Campaign.Status.APPROVED,
        target_department=department,
        scheduled_at=timezone.now() - timedelta(minutes=1),
    )
    fake_client = FakePhishingEngineClient()

    with patch("apps.campaigns.tasks.get_client", return_value=fake_client):
        launch_scheduled_campaigns()

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.LAUNCHED
    assert campaign.gophish_campaign_id is not None
    assert len(fake_client.created_campaigns) == 1


def test_not_yet_due_campaign_is_not_launched():
    department = DepartmentFactory()
    campaign = CampaignFactory(
        status=Campaign.Status.APPROVED,
        target_department=department,
        scheduled_at=timezone.now() + timedelta(hours=1),
    )
    fake_client = FakePhishingEngineClient()

    with patch("apps.campaigns.tasks.get_client", return_value=fake_client):
        launch_scheduled_campaigns()

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED
    assert fake_client.created_campaigns == []


def test_draft_campaign_with_scheduled_at_is_not_launched():
    department = DepartmentFactory()
    campaign = CampaignFactory(
        status=Campaign.Status.DRAFT,
        target_department=department,
        scheduled_at=timezone.now() - timedelta(minutes=1),
    )
    fake_client = FakePhishingEngineClient()

    with patch("apps.campaigns.tasks.get_client", return_value=fake_client):
        launch_scheduled_campaigns()

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.DRAFT
    assert fake_client.created_campaigns == []
