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


class _DummyMessages:
    """Minimal stand-in for Django's messages framework in a bare RequestFactory request."""

    def add(self, level, message, extra_tags=""):
        pass


def _user_in_group(django_user_model, username, group_name):
    SetupGroupsCommand().handle()
    group = Group.objects.get(name=group_name)
    user = django_user_model.objects.create_user(username=username, password="x")
    user.groups.add(group)
    return django_user_model.objects.get(pk=user.pk)  # fresh instance, no stale perm cache


def test_report_viewer_cannot_launch_campaign(rf: RequestFactory, django_user_model):
    user = _user_in_group(django_user_model, "viewer", "Report Viewer")
    campaign = CampaignFactory(status=Campaign.Status.APPROVED)
    request = rf.post("/admin/campaigns/campaign/")
    request.user = user
    request._messages = _DummyMessages()

    campaign_admin = _admin_site_campaign_admin()
    campaign_admin.launch_campaign_action(request, Campaign.objects.filter(pk=campaign.pk))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED
    assert campaign.gophish_campaign_id is None


def test_campaign_manager_cannot_approve_own_submission(rf: RequestFactory, django_user_model):
    user = _user_in_group(django_user_model, "campaign_mgr", "Campaign Manager")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL)
    request = rf.post("/admin/campaigns/campaign/")
    request.user = user
    request._messages = _DummyMessages()

    campaign_admin = _admin_site_campaign_admin()
    campaign_admin.approve_campaign(request, Campaign.objects.filter(pk=campaign.pk))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.PENDING_APPROVAL
    assert campaign.approved_by is None


def test_security_admin_can_approve_pending_campaign(rf: RequestFactory, django_user_model):
    user = _user_in_group(django_user_model, "sec_admin", "Security Admin")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL)
    request = rf.post("/admin/campaigns/campaign/")
    request.user = user
    request._messages = _DummyMessages()

    campaign_admin = _admin_site_campaign_admin()
    campaign_admin.approve_campaign(request, Campaign.objects.filter(pk=campaign.pk))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED
    assert campaign.approved_by == user
