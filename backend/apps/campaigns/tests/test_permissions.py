import pytest
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.campaigns.admin import CampaignAdmin
from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand

pytestmark = pytest.mark.django_db


def _admin_site_campaign_admin():
    from django.contrib import admin

    return CampaignAdmin(Campaign, admin.site)


def test_viewer_cannot_launch_campaign(rf: RequestFactory, django_user_model):
    SetupGroupsCommand().handle()
    viewer_group = Group.objects.get(name="Viewer")
    user = django_user_model.objects.create_user(username="viewer", password="x")
    user.groups.add(viewer_group)
    # Django's permission checks need this refreshed off the DB-backed group cache.
    user = django_user_model.objects.get(pk=user.pk)

    campaign = CampaignFactory()
    request = rf.post("/admin/campaigns/campaign/")
    request.user = user
    request._messages = _DummyMessages()

    campaign_admin = _admin_site_campaign_admin()
    campaign_admin.launch_campaign(request, Campaign.objects.filter(pk=campaign.pk))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.DRAFT
    assert campaign.gophish_campaign_id is None


class _DummyMessages:
    """Minimal stand-in for Django's messages framework in a bare RequestFactory request."""

    def add(self, level, message, extra_tags=""):
        pass
