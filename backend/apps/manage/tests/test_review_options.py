"""Options added to the existing console pages after the senior review."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from apps.api.models import ApiKey, Scope
from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.intake.models import ReportedEmail
from apps.reporting.models import GeneratedReport, ReportSchedule
from apps.training.models import TrainingPolicy
from apps.training.tests.factories import TrainingAssignmentFactory, TrainingModuleFactory

pytestmark = pytest.mark.django_db


def login(client, django_user_model, group="Security Admin", username=None):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=username or f"u-{group}", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


def actions():
    return list(AuditLogEntry.objects.values_list("action", flat=True))


# --- campaigns ----------------------------------------------------------------------------


def test_rejecting_a_campaign_requires_a_reason(client, django_user_model):
    login(client, django_user_model)
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL)
    client.post(reverse("manage:campaign-reject", args=[campaign.pk]), {"reason": "  "})
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.PENDING_APPROVAL and "campaign_rejected" not in actions()
    client.post(reverse("manage:campaign-reject", args=[campaign.pk]), {"reason": "Wrong audience"})
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.DRAFT
    assert AuditLogEntry.objects.get(action="campaign_rejected").metadata["reason"] == "Wrong audience"


def test_campaign_list_searches_filters_and_tells_the_approver_how_many_people(client, django_user_model):
    admin = login(client, django_user_model)
    dept = DepartmentFactory(name="Finance")
    EmployeeFactory.create_batch(3, department=dept)
    EmployeeFactory(department=dept, is_exempt=True, exempt_reason="Leave")
    EmployeeFactory(department=dept, is_active=False)
    CampaignFactory(name="Invoice scam", target_department=dept, status=Campaign.Status.PENDING_APPROVAL, submitted_by=admin)
    CampaignFactory(name="Other run", target_department=DepartmentFactory())

    page = client.get(reverse("manage:campaigns")).content.decode()
    assert "3 recipients" in page and f"Submitted by {admin.username}" in page
    only = client.get(reverse("manage:campaigns"), {"q": "invoice"}).content.decode()
    assert "Invoice scam" in only and "Other run" not in only
    by_dept = client.get(reverse("manage:campaigns"), {"department": dept.pk}).content.decode()
    assert "Invoice scam" in by_dept and "Other run" not in by_dept


# --- employees ----------------------------------------------------------------------------


def test_employee_state_filters(client, django_user_model):
    login(client, django_user_model)
    EmployeeFactory(full_name="Active Person")
    EmployeeFactory(full_name="Gone Person", is_active=False)
    EmployeeFactory(full_name="Exempt Person", is_exempt=True, exempt_reason="Legal hold")
    get = lambda **p: client.get(reverse("manage:employees"), p).content.decode()  # noqa: E731
    assert "Gone Person" not in get(state="active") and "Active Person" in get(state="active")
    assert "Gone Person" in get(state="inactive") and "Active Person" not in get(state="inactive")
    exempt = get(state="exempt")
    assert "Exempt Person" in exempt and "Legal hold" in exempt and "Active Person" not in exempt


def test_employee_export_matches_the_filter_is_audited_and_is_security_admin_only(client, django_user_model):
    login(client, django_user_model)
    EmployeeFactory(full_name="=cmd|calc", email="a@example.com")
    EmployeeFactory(full_name="Gone", email="g@example.com", is_active=False)
    text = client.post(reverse("manage:employee-export"), {"state": "active"}).content.decode("utf-8-sig")
    assert "'=cmd|calc" in text and "Gone" not in text
    assert "employees_exported" in actions()

    login(client, django_user_model, "Campaign Manager")
    assert client.post(reverse("manage:employee-export")).status_code == 403


# --- departments --------------------------------------------------------------------------


def test_a_department_head_can_be_set_and_is_shown(client, django_user_model):
    login(client, django_user_model)
    head = EmployeeFactory(full_name="Hana Head")
    dept = DepartmentFactory(name="Legal")
    response = client.post(reverse("manage:department-edit", args=[dept.pk]), {"name": "Legal", "manager": head.pk})
    assert response.status_code == 302
    dept.refresh_from_db()
    assert dept.manager == head
    assert "head: Hana Head" in client.get(reverse("manage:departments")).content.decode()


# --- training -----------------------------------------------------------------------------


def test_module_list_shows_usage_and_waived_are_not_counted(client, django_user_model):
    from apps.training.models import TrainingAssignment

    login(client, django_user_model)
    module = TrainingModuleFactory(title="Phishing 101")
    a, b, waived = (TrainingAssignmentFactory(module=module) for _ in range(3))
    TrainingAssignment.objects.filter(pk=a.pk).update(completed_at=timezone.now())
    TrainingAssignment.objects.filter(pk=waived.pk).update(waived_at=timezone.now())
    row = client.get(reverse("manage:training")).context["page"].object_list[0]
    assert (row.assigned_total, row.completed_total) == (2, 1) and b


def test_a_training_policy_can_be_paused_and_resumed(client, django_user_model):
    login(client, django_user_model)
    policy = TrainingPolicy.objects.create(name="Annual", module=TrainingModuleFactory())
    client.post(reverse("manage:policy-toggle", args=[policy.pk]))
    policy.refresh_from_db()
    assert not policy.is_active and "training_policy_paused" in actions()
    client.post(reverse("manage:policy-toggle", args=[policy.pk]))
    policy.refresh_from_db()
    assert policy.is_active and "training_policy_resumed" in actions()


def test_report_viewer_cannot_pause_a_policy(client, django_user_model):
    login(client, django_user_model, "Report Viewer")
    policy = TrainingPolicy.objects.create(name="Annual", module=TrainingModuleFactory())
    assert client.post(reverse("manage:policy-toggle", args=[policy.pk])).status_code == 403


# --- scheduled reports --------------------------------------------------------------------


def test_schedules_can_be_paused_and_deleted_with_an_audit_trail(client, django_user_model):
    login(client, django_user_model)
    schedule = ReportSchedule.objects.create(
        name="Monthly CISO", kind=GeneratedReport.Kind.EXECUTIVE_SUMMARY, recipients="ciso@example.com")
    client.post(reverse("manage:schedule-toggle", args=[schedule.pk]))
    schedule.refresh_from_db()
    assert not schedule.is_active and "report_schedule_paused" in actions()
    client.post(reverse("manage:schedule-delete", args=[schedule.pk]))
    assert not ReportSchedule.objects.exists()
    entry = AuditLogEntry.objects.get(action="report_schedule_deleted")
    assert entry.metadata["recipients"] == "ciso@example.com"


def test_report_viewer_cannot_delete_a_schedule(client, django_user_model):
    login(client, django_user_model, "Report Viewer")
    schedule = ReportSchedule.objects.create(
        name="M", kind=GeneratedReport.Kind.EXECUTIVE_SUMMARY, recipients="a@example.com")
    assert client.post(reverse("manage:schedule-delete", args=[schedule.pk])).status_code == 403
    assert ReportSchedule.objects.exists()


# --- reported emails ----------------------------------------------------------------------


def test_reported_emails_can_be_searched(client, django_user_model):
    login(client, django_user_model)
    reporter = EmployeeFactory(full_name="Rita Reporter")
    ReportedEmail.objects.create(reporter=reporter, subject="Gift card request", sender="x@evil.example", source="portal")
    ReportedEmail.objects.create(reporter=reporter, subject="Lunch menu", sender="cafe@example.com", source="portal")
    page = client.get(reverse("manage:reported"), {"q": "gift"}).content.decode()
    assert "Gift card request" in page and "Lunch menu" not in page


# --- API keys -----------------------------------------------------------------------------


def _key(owner, name):
    return ApiKey.generate(name=name, owner=owner, scopes=[Scope.TRAINING_WRITE.value])[0]


def test_security_admin_sees_and_revokes_everyones_keys_but_others_only_their_own(client, django_user_model):
    trainer = django_user_model.objects.create_user(username="trainer", password="x", is_staff=True)
    key = _key(trainer, "trainer bot")
    admin = login(client, django_user_model)
    page = client.get(reverse("manage:api-keys")).content.decode()
    assert "All keys" in page and "trainer bot" in page and "Owner trainer" in page and "never used" in page
    client.post(reverse("manage:api-key-revoke", args=[key.pk]))
    key.refresh_from_db()
    assert key.revoked_at is not None
    assert AuditLogEntry.objects.get(action="api_key_revoked").metadata["by_owner"] is False and admin

    mine = _key(trainer, "second")
    login(client, django_user_model, "Report Viewer")
    assert "second" not in client.get(reverse("manage:api-keys")).content.decode()
    assert client.post(reverse("manage:api-key-revoke", args=[mine.pk])).status_code == 404


def test_a_campaign_with_nobody_to_send_to_cannot_be_submitted(client, django_user_model):
    login(client, django_user_model)
    no_audience = CampaignFactory(target_department=None)
    empty_dept = CampaignFactory(target_department=DepartmentFactory())
    everyone_exempt = DepartmentFactory()
    EmployeeFactory(department=everyone_exempt, is_exempt=True, exempt_reason="Leave")
    all_exempt = CampaignFactory(target_department=everyone_exempt)
    for c in (no_audience, empty_dept, all_exempt):
        client.post(reverse("manage:campaign-submit", args=[c.pk]))
        c.refresh_from_db()
        assert c.status == Campaign.Status.DRAFT
    ok_dept = DepartmentFactory()
    EmployeeFactory(department=ok_dept)
    ok = CampaignFactory(target_department=ok_dept)
    client.post(reverse("manage:campaign-submit", args=[ok.pk]))
    ok.refresh_from_db()
    assert ok.status == Campaign.Status.PENDING_APPROVAL
