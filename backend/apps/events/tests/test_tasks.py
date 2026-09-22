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
