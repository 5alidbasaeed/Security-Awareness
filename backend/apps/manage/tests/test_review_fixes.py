"""Regressions from the 2026-09-25 source review (fixes for the findings not already covered elsewhere)."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group, User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign, EmailDraft, LandingDraft
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.gophish import EXTERNAL_ID_MAX, build_gophish_external_id
from apps.engine.tests.fakes import FakePhishingEngineClient
from apps.events.sanitize import strip_sensitive_fields
from apps.manage.forms import CampaignForm
from apps.manage.views_ux import NAME_MAX, _unique_name, module_readiness
from apps.portal.tests.test_course import deck
from apps.portal.tests.test_portal import sign_in
from apps.risk_scoring import analytics
from apps.training.models import TrainingAssignment, TrainingPolicy
from apps.training.tasks import enforce_training_policies, escalate_overdue_training
from apps.training.tests.factories import TrainingAssignmentFactory, TrainingModuleFactory

pytestmark = pytest.mark.django_db


def login(client, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = User.objects.create_user(username=f"u-{group}", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


# --- a retired training module must not be dropped from a campaign ------------------------------------------


def _approved_campaign_with_retired_module():
    dept = DepartmentFactory()
    EmailDraft.objects.create(name="Invoice", subject="Invoice due")
    LandingDraft.objects.create(name="Sign in")
    module = TrainingModuleFactory(is_active=False)
    campaign = CampaignFactory(
        template_name="Invoice", landing_page_name="Sign in", target_department=dept,
        training_module=module, status=Campaign.Status.APPROVED,
    )
    return campaign, module


def test_campaign_form_keeps_a_module_that_was_retired_after_it_was_chosen(client):
    user = login(client)
    campaign, module = _approved_campaign_with_retired_module()

    form = CampaignForm(instance=campaign, user=user)

    assert module in form.fields["training_module"].queryset
    assert not CampaignForm(user=user).fields["training_module"].queryset.filter(pk=module.pk).exists()  # not for new ones


def test_changing_a_campaigns_training_after_approval_sends_it_back_to_draft(client):
    login(client)
    campaign, _ = _approved_campaign_with_retired_module()

    client.post(reverse("manage:campaign-edit", args=[campaign.pk]), {
        "name": campaign.name, "email": "Invoice", "landing_page": "Sign in",
        "audience": f"dept:{campaign.target_department_id}", "training_module": "", "scheduled_at": "",
    })

    campaign.refresh_from_db()
    assert campaign.training_module is None and campaign.status == Campaign.Status.DRAFT


def test_summary_panel_names_the_campaigns_retired_module(client):
    login(client)
    module = TrainingModuleFactory(title="Old course", is_active=False)

    html = client.get(reverse("manage:campaign-summary"), {"training_module": module.pk}).content.decode()

    assert "Old course" in html


def test_summary_panel_request_leaves_the_csrf_token_out_of_the_url():
    from pathlib import Path

    template = Path(__file__).resolve().parents[1] / "templates" / "manage" / "campaign_form.html"
    assert 'hx-params="not csrfmiddlewaretoken"' in template.read_text(encoding="utf-8")


# --- retiring a module: reminders and escalations agree, and the message says so ----------------------------


def test_retired_modules_are_not_escalated_to_managers():
    department = DepartmentFactory()
    manager = User.objects.create_user("mgr", email="mgr@corp.example")
    department.managers.add(manager)
    late = TrainingAssignmentFactory(employee=EmployeeFactory(department=department), module=TrainingModuleFactory(is_active=False))
    TrainingAssignment.objects.filter(pk=late.pk).update(due_at=timezone.now() - timedelta(days=10))

    assert escalate_overdue_training() == 0
    assert not mail.outbox


def test_retire_message_says_reminders_stop(client):
    login(client)
    module = TrainingModuleFactory()

    response = client.post(reverse("manage:module-toggle", args=[module.pk]), follow=True)

    assert "no more reminders" in response.content.decode()


def test_readiness_says_retired_even_without_content():
    module = TrainingModuleFactory(is_active=False, content_url="")
    assert module_readiness(module, 0, 0)[0] == "Retired"


# --- portal: a slide course can't be marked started without opening it ---------------------------------------


def test_start_action_cannot_bypass_opening_a_slide_course(client):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee)
    deck(item.module, 2)
    sign_in(client, employee)
    url = reverse("portal:assignment", args=[item.pk])

    client.post(url, {"action": "start"})
    client.post(url, {"action": "complete"})

    item.refresh_from_db()
    assert item.started_at is None and item.completed_at is None


# --- policy enforcement: one audit entry per policy, with that policy's count --------------------------------


def test_policy_audit_entries_carry_their_own_count():
    EmployeeFactory(), EmployeeFactory()
    TrainingPolicy.objects.create(name="A", module=TrainingModuleFactory())
    TrainingPolicy.objects.create(name="B", module=TrainingModuleFactory())
    TrainingPolicy.objects.create(name="C (nobody new)", module=TrainingModuleFactory(), is_active=False)

    assert enforce_training_policies() == 4
    entries = {e.target_description: e.metadata["enrolled"] for e in AuditLogEntry.objects.filter(action="training_policy_enforced")}
    assert entries == {"A": 2, "B": 2}


def test_a_policy_that_enrols_nobody_writes_no_audit_entry():
    employee = EmployeeFactory()
    first, second = TrainingModuleFactory(), TrainingModuleFactory()
    TrainingPolicy.objects.create(name="A", module=first)
    TrainingPolicy.objects.create(name="B", module=second)
    TrainingAssignmentFactory(employee=employee, module=second)  # already has B

    enforce_training_policies()

    assert list(AuditLogEntry.objects.filter(action="training_policy_enforced").values_list("target_description", flat=True)) == ["A"]


# --- ingestion ------------------------------------------------------------------------------------------------


def test_external_id_always_fits_the_column_and_stays_deterministic():
    long_email = "a" * 240 + "@corp.example"
    args = {"campaign_id": "42", "email": long_email, "message": "Clicked Link", "time": "2026-09-25T10:00:00.123456789Z"}

    first, second = build_gophish_external_id(**args), build_gophish_external_id(**args)

    assert len(first) <= EXTERNAL_ID_MAX and first == second
    assert build_gophish_external_id(**{**args, "email": "short@corp.example"}) == \
        "42:short@corp.example:Clicked Link:2026-09-25T10:00:00.123456789Z"  # short keys unchanged


def test_webhook_accepts_an_event_for_a_very_long_address(client, settings):
    import hashlib
    import hmac
    import json

    settings.GOPHISH_WEBHOOK_SECRET = "s3cret"
    employee = EmployeeFactory(email="a" * 230 + "@corp.example")
    campaign = CampaignFactory(gophish_campaign_id="77")
    body = json.dumps({"campaign_id": 77, "email": employee.email, "time": "2026-09-25T10:00:00Z",
                       "message": "Clicked Link", "details": ""}).encode()
    signature = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()

    response = client.post(reverse("events:gophish-webhook"), body, content_type="application/json",
                           HTTP_X_GOPHISH_SIGNATURE=f"sha256={signature}")

    assert response.status_code == 200
    assert campaign.events.count() == 1


@pytest.mark.parametrize("key", ["pwd", "pw", "user_pin", "login[otp]", "passcode", "Passwd", "mfa_code"])
def test_sanitizer_strips_common_secret_field_names(key):
    assert strip_sensitive_fields({"payload": {key: ["x"], "username": ["u"]}}) == {"payload": {"username": ["u"]}}


@pytest.mark.parametrize("key", ["username", "spinner", "shipping", "email", "rid"])
def test_sanitizer_keeps_ordinary_field_names(key):
    assert strip_sensitive_fields({key: "v"}) == {key: "v"}


# --- analytics -------------------------------------------------------------------------------------------------


def test_suppressed_summary_has_the_same_training_keys(settings):
    settings.MIN_REPORTING_COHORT = 5
    EmployeeFactory()
    from apps.employees.models import Employee

    suppressed = analytics.scope_summary(Employee.objects.all())["training"]
    settings.MIN_REPORTING_COHORT = 1
    full = analytics.scope_summary(Employee.objects.all())["training"]

    assert set(suppressed) == set(full)


def test_overview_trend_leaves_out_offboarded_people(client, monkeypatch):
    from apps.dashboard import views as dashboard_views
    from apps.risk_scoring.models import RiskScoreSnapshot

    login(client)
    active, gone = EmployeeFactory(), EmployeeFactory(is_active=False)
    for employee, score in ((active, 10), (gone, 90)):
        RiskScoreSnapshot.objects.create(employee=employee, score=score, algorithm_version="v1", contributing_metrics={})
    seen = {}
    for name in ("trend_series", "program_improvement"):
        real = getattr(dashboard_views.analytics, name)
        monkeypatch.setattr(dashboard_views.analytics, name,
                            lambda snapshots, *a, _n=name, _r=real, **k: (seen.setdefault(_n, set(snapshots.values_list("employee_id", flat=True))), _r(snapshots, *a, **k))[1])

    assert client.get(reverse("dashboard:overview")).status_code == 200

    assert seen == {"trend_series": {active.pk}, "program_improvement": {active.pk}}


# --- duplicating: names always fit and collide safely ----------------------------------------------------------


def test_copy_names_stay_within_the_length_limit():
    base = "x" * NAME_MAX
    EmailDraft.objects.create(name=f"Copy of {base}"[:NAME_MAX], subject="s")
    for n in range(2, 12):
        suffix = f" ({n})"
        EmailDraft.objects.create(name=f"Copy of {base}"[: NAME_MAX - len(suffix)] + suffix, subject="s")

    name = _unique_name(EmailDraft, base)

    assert len(name) <= NAME_MAX and name.endswith(" (12)")


def test_duplicate_survives_a_name_taken_at_the_same_moment(client, monkeypatch):
    login(client)
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views_ux.get_client", lambda: fake)
    source = EmailDraft.objects.create(name="Invoice", subject="s")
    real = _unique_name
    calls = []

    def racing(model, base, field="name"):
        name = real(model, base, field)
        if not calls:  # someone else takes the first free name between the check and the save
            EmailDraft.objects.create(name=name, subject="s")
        calls.append(name)
        return name

    monkeypatch.setattr("apps.manage.views_ux._unique_name", racing)
    response = client.post(reverse("manage:template-duplicate", args=[source.pk]))

    assert response.status_code == 302
    assert EmailDraft.objects.filter(name="Copy of Invoice (2)").exists()
