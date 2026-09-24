from unittest.mock import patch

import pytest

from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.events.tasks import reconcile_all_active_campaigns

pytestmark = pytest.mark.django_db


def test_only_launched_campaigns_are_queued_for_reconciliation():
    launched = CampaignFactory(status=Campaign.Status.LAUNCHED, gophish_campaign_id="1")
    CampaignFactory(status=Campaign.Status.DRAFT)  # never launched — must not be queued

    with patch("apps.events.tasks.reconcile_campaign.delay") as delay:
        reconcile_all_active_campaigns()

    delay.assert_called_once_with(launched.pk)


def test_one_malformed_timeline_entry_does_not_abort_reconciliation():
    from apps.employees.tests.factories import EmployeeFactory
    from apps.engine.base import EngineEvent
    from apps.engine.tests.fakes import FakePhishingEngineClient
    from apps.events.models import Event
    from apps.events.tasks import reconcile_campaign

    campaign = CampaignFactory(status=Campaign.Status.LAUNCHED, gophish_campaign_id="7")
    employee = EmployeeFactory()
    fake = FakePhishingEngineClient()
    fake.results_by_campaign_id["7"] = [
        EngineEvent("bad", "link_clicked", "2026-13-45T10:00:00Z", {"email": employee.email}),
        EngineEvent(
            "good", "credential_attempt", "2026-09-24T10:00:00Z",
            {"email": employee.email, "details": '{"payload": {"password": ["hunter2"]}}'},
        ),
    ]

    with patch("apps.events.tasks.get_client", return_value=fake):
        reconcile_campaign(campaign.pk)

    event = Event.objects.get()
    assert event.external_id == "good"
    assert "hunter2" not in str(event.metadata)
