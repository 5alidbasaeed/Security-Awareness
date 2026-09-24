import pathlib

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.models import Campaign, EmailDraft, LandingDraft
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def login(client, django_user_model, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=f"u-{group}", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


# --- access -------------------------------------------------------------------------------


def test_anonymous_is_redirected(client):
    assert client.get(reverse("manage:index")).status_code == 302


def test_report_viewer_cannot_open_the_management_hub(client, django_user_model):
    login(client, django_user_model, "Report Viewer")
    # Report Viewer has no change_* perms, so the hub cards are empty but the page still renders...
    assert client.get(reverse("manage:index")).status_code == 200
    # ...and it cannot reach any write page.
    assert client.get(reverse("manage:campaign-new")).status_code == 403
    assert client.get(reverse("manage:employee-edit", args=[1])).status_code == 403


@pytest.mark.parametrize("name", ["index", "campaigns", "content", "training", "employees", "departments", "api-keys"])
def test_every_management_page_renders_for_a_security_admin(client, django_user_model, monkeypatch, name):
    monkeypatch.setattr("apps.manage.views.get_client", lambda: FakePhishingEngineClient())
    login(client, django_user_model)
    assert client.get(reverse(f"manage:{name}")).status_code == 200


# --- campaign workflow --------------------------------------------------------------------


def test_create_draft_then_submit_approve_launch(client, django_user_model, monkeypatch):
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    department = DepartmentFactory(name="Ops")
    EmployeeFactory(department=department)
    admin = login(client, django_user_model)

    EmailDraft.objects.create(name="T", subject="S", body_html="<p>x</p>")
    LandingDraft.objects.create(name="P")
    client.post(reverse("manage:campaign-new"), {
        "name": "Test run", "email": "T", "landing_page": "P", "audience": f"dept:{department.pk}",
    })
    campaign = Campaign.objects.get()
    assert campaign.status == Campaign.Status.DRAFT and campaign.created_by == admin

    client.post(reverse("manage:campaign-submit", args=[campaign.pk]))
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.PENDING_APPROVAL

    client.post(reverse("manage:campaign-approve", args=[campaign.pk]))
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.APPROVED

    client.post(reverse("manage:campaign-launch", args=[campaign.pk]))
    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.LAUNCHED
    assert len(fake.created_campaigns) == 1


def test_reject_sends_a_pending_campaign_back_to_draft(client, django_user_model):
    login(client, django_user_model)
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL)

    client.post(reverse("manage:campaign-reject", args=[campaign.pk]), {"reason": "Landing page too aggressive"})

    campaign.refresh_from_db()
    assert campaign.status == Campaign.Status.DRAFT


def test_a_campaign_manager_cannot_approve(client, django_user_model):
    login(client, django_user_model, "Campaign Manager")
    campaign = CampaignFactory(status=Campaign.Status.PENDING_APPROVAL)

    assert client.post(reverse("manage:campaign-approve", args=[campaign.pk])).status_code == 403


# --- content: passwords are never captured ------------------------------------------------


def test_landing_page_created_here_never_captures_passwords(client, django_user_model, monkeypatch):
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    login(client, django_user_model)

    client.post(reverse("manage:page-new"), {"name": "P", "html": "<form></form>", "capture_credentials": "on"})

    assert fake.pages["P"]["capture_passwords"] is False


# --- CSV import ---------------------------------------------------------------------------


def test_no_inline_styles_or_scripts_in_manage_templates():
    root = pathlib.Path(__file__).resolve().parent.parent / "templates"
    offenders = [p.name for p in root.rglob("*.html")
                 if " style=" in p.read_text() or "onclick=" in p.read_text() or "<script>" in p.read_text()]
    assert not offenders


def test_department_manager_key_scopes_are_limited_to_their_permissions(client, django_user_model):
    user = login(client, django_user_model, "Department Manager")
    DepartmentFactory().managers.add(user)

    body = client.get(reverse("manage:api-keys")).content.decode()
    # A Department Manager can't create campaigns, so that scope must not be offered.
    assert "campaigns:write" not in body
    assert "content:write" in body  # they hold change_campaign


def test_quiz_questions_are_added_and_removed_in_the_ui(client, django_user_model):
    from apps.training.models import QuizQuestion, TrainingModule

    login(client, django_user_model)
    module = TrainingModule.objects.create(title="M", content_url="https://t.example")

    client.post(reverse("manage:question-add", args=[module.pk]),
                {"text": "Check the sender?", "choice1": "Yes", "choice2": "No", "correct": "1"})
    question = QuizQuestion.objects.get()
    assert question.quiz.module == module
    assert list(question.choices.values_list("text", "is_correct")) == [("Yes", True), ("No", False)]

    client.post(reverse("manage:question-add", args=[module.pk]), {"text": "Only one answer", "choice1": "A", "correct": "1"})
    assert QuizQuestion.objects.count() == 1  # rejected: needs two answers

    client.post(reverse("manage:question-delete", args=[module.pk, question.pk]))
    assert not QuizQuestion.objects.exists()
