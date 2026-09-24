"""
Regression tests from the Phase 0-3 review: ways the approval workflow and
RBAC could be bypassed even though each individual action was permission-checked.
"""

from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import RequestFactory
from django.urls import reverse

from apps.campaigns.admin import CampaignAdmin
from apps.campaigns.models import Campaign
from apps.campaigns.services import CampaignLaunchError, launch_campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.admin import EmployeeAdmin
from apps.employees.models import Employee
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def _user(django_user_model, group_name, username="u", **extra):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=username, password="x", is_staff=True, **extra)
    user.groups.add(Group.objects.get(name=group_name))
    return django_user_model.objects.get(pk=user.pk)


class _FormStub:
    def __init__(self, changed_data):
        self.changed_data = changed_data


def _request(rf: RequestFactory, user):
    request = rf.get("/")
    request.user = user
    request.session = {}
    request._messages = type("M", (), {"add": lambda *a, **k: None})()
    return request


def test_status_is_not_editable_in_the_change_form(rf, django_user_model):
    user = _user(django_user_model, "Campaign Manager")
    campaign = CampaignFactory()

    form_class = CampaignAdmin(Campaign, admin.site).get_form(_request(rf, user), campaign, change=True)

    for field in ("status", "approved_by", "approved_at", "gophish_campaign_id"):
        assert field not in form_class.base_fields


def test_report_viewer_cannot_submit_campaign_for_approval(client, django_user_model):
    user = _user(django_user_model, "Report Viewer")
    campaign = CampaignFactory()
    client.force_login(user)

    client.post(
        reverse("admin:campaigns_campaign_changelist"),
        {"action": "submit_for_approval", "_selected_action": [campaign.pk]},
    )

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.DRAFT


@pytest.mark.parametrize("status", [Campaign.Status.PENDING_APPROVAL, Campaign.Status.APPROVED])
def test_editing_content_after_submission_resets_to_draft(rf, django_user_model, status):
    user = _user(django_user_model, "Campaign Manager")
    campaign = CampaignFactory(status=status, approved_by=user)
    campaign.landing_page_url = "https://evil.example.com/"

    CampaignAdmin(Campaign, admin.site).save_model(
        _request(rf, user), campaign, _FormStub(["landing_page_url"]), change=True
    )

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.DRAFT
    assert campaign.approved_by is None


def test_editing_schedule_only_keeps_approval(rf, django_user_model):
    user = _user(django_user_model, "Campaign Manager")
    campaign = CampaignFactory(status=Campaign.Status.APPROVED)

    CampaignAdmin(Campaign, admin.site).save_model(_request(rf, user), campaign, _FormStub(["scheduled_at"]), change=True)

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED


def test_new_campaign_records_its_creator(rf, django_user_model):
    user = _user(django_user_model, "Campaign Manager")
    campaign = CampaignFactory.build(target_department=DepartmentFactory())

    CampaignAdmin(Campaign, admin.site).save_model(_request(rf, user), campaign, _FormStub([]), change=False)

    assert Campaign.objects.get(pk=campaign.pk).created_by == user


def test_department_manager_cannot_edit_departments(django_user_model):
    user = _user(django_user_model, "Department Manager")

    assert not user.has_perm("employees.change_department")
    assert not user.has_perm("employees.add_department")


def test_department_manager_can_only_pick_their_own_department(rf, django_user_model):
    mine, other = DepartmentFactory(), DepartmentFactory()
    user = _user(django_user_model, "Department Manager")
    mine.managers.add(user)
    request = _request(rf, user)

    for model_admin, model, field in (
        (CampaignAdmin(Campaign, admin.site), Campaign, "target_department"),
        (EmployeeAdmin(Employee, admin.site), Employee, "department"),
    ):
        formfield = model_admin.formfield_for_foreignkey(model._meta.get_field(field), request)
        assert list(formfield.queryset) == [mine]
        assert other not in formfield.queryset


def test_preview_response_is_sandboxed(rf, django_user_model):
    department = DepartmentFactory()
    user = _user(django_user_model, "Department Manager")
    department.managers.add(user)
    campaign = CampaignFactory(target_department=department, landing_page_name="Page")
    fake = FakePhishingEngineClient()
    fake.landing_pages["Page"] = "<script>fetch('/admin/')</script>"

    with patch("apps.campaigns.admin.get_client", return_value=fake):
        response = CampaignAdmin(Campaign, admin.site).preview_landing_page_view(_request(rf, user), campaign.pk)

    assert "sandbox" in response["Content-Security-Policy"]


def test_stale_copy_of_a_launched_campaign_cannot_launch_twice():
    campaign = CampaignFactory(status=Campaign.Status.APPROVED)
    EmployeeFactory(department=campaign.target_department)
    stale_copy = Campaign.objects.get(pk=campaign.pk)  # e.g. the scheduler's copy
    client = FakePhishingEngineClient()

    launch_campaign(campaign, actor=None, client=client)
    with pytest.raises(CampaignLaunchError, match="already launched"):
        launch_campaign(stale_copy, actor=None, client=client)

    assert len(client.created_campaigns) == 1


def test_rate_limit_block_is_still_audit_logged(settings):
    from apps.core.models import AuditLogEntry

    settings.CAMPAIGN_LAUNCH_RATE_LIMIT = 0
    campaign = CampaignFactory(status=Campaign.Status.APPROVED)

    with pytest.raises(CampaignLaunchError, match="rate limit"):
        launch_campaign(campaign, actor=None, client=FakePhishingEngineClient())

    assert AuditLogEntry.objects.filter(action="campaign_launch_rate_limited").exists()
