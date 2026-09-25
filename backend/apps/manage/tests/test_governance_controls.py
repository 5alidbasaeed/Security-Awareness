"""Governance controls added from the 2026-09-25 review (opus_comments/phase3_admin_console.md, AG1)."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core import governance
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry

pytestmark = pytest.mark.django_db


def _user(django_user_model, name, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=name, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


def test_the_submitter_cannot_approve_their_own_campaign(client, django_user_model):
    author = _user(django_user_model, "author")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, submitted_by=author)
    client.force_login(author)

    client.post(reverse("manage:campaign-approve", args=[campaign.pk]))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.PENDING_APPROVAL
    assert not AuditLogEntry.objects.filter(action="campaign_approved").exists()


def test_a_different_security_admin_can_approve(client, django_user_model):
    author, approver = _user(django_user_model, "author"), _user(django_user_model, "approver")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, submitted_by=author)
    client.force_login(approver)

    client.post(reverse("manage:campaign-approve", args=[campaign.pk]))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED and campaign.approved_by == approver


def test_the_django_admin_action_enforces_the_same_rule(client, django_user_model):
    author = _user(django_user_model, "author")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, submitted_by=author)
    client.force_login(author)

    client.post(reverse("admin:campaigns_campaign_changelist"),
                {"action": "approve_campaign", "_selected_action": [campaign.pk]})

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.PENDING_APPROVAL


def test_break_glass_allows_self_approval_and_is_flagged(client, django_user_model, settings):
    settings.REQUIRE_SEPARATE_APPROVER = False
    author = _user(django_user_model, "author")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, submitted_by=author)
    client.force_login(author)

    client.post(reverse("manage:campaign-approve", args=[campaign.pk]))

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED
    check = next(c for c in governance.control_health() if c["label"] == "Approvals are independent")
    assert check["status"] == "attention"


# --- AG3: the approver reviews the actual content before deciding --------------------------------


def test_review_page_shows_what_is_being_approved(client, django_user_model):
    from apps.campaigns.models import EmailDraft

    author, approver = _user(django_user_model, "author"), _user(django_user_model, "approver")
    EmailDraft.objects.create(name="Payroll", subject="Your payslip", layout="minimal", body_html="<p>x</p>")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, submitted_by=author,
                               template_name="Payroll", landing_page_name="Login")
    client.force_login(approver)

    html = client.get(reverse("manage:campaign-review", args=[campaign.pk])).content.decode()

    assert reverse("manage:email-preview", args=["Payroll"]) in html
    assert reverse("manage:page-preview", args=["Login"]) in html
    assert "Your payslip" in html and reverse("manage:campaign-approve", args=[campaign.pk]) in html
    list_html = client.get(reverse("manage:campaigns")).content.decode()
    assert reverse("manage:campaign-review", args=[campaign.pk]) in list_html


def test_review_page_tells_the_submitter_they_cannot_approve(client, django_user_model):
    author = _user(django_user_model, "author")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, submitted_by=author)
    client.force_login(author)

    html = client.get(reverse("manage:campaign-review", args=[campaign.pk])).content.decode()

    assert "someone else has to approve it" in html
    assert reverse("manage:campaign-approve", args=[campaign.pk]) not in html


def test_only_approvers_can_open_the_review_page(client, django_user_model):
    manager = _user(django_user_model, "cm", group="Campaign Manager")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL)
    client.force_login(manager)
    assert client.get(reverse("manage:campaign-review", args=[campaign.pk])).status_code == 403
