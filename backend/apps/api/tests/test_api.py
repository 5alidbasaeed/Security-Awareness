import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.urls import get_resolver, reverse
from django.utils import timezone

from apps.api.models import ApiKey, Scope, hash_key
from apps.campaigns.models import Campaign
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.employees.tests.factories import DepartmentFactory
from apps.engine.tests.fakes import FakePhishingEngineClient
from apps.training.models import TrainingModule

pytestmark = pytest.mark.django_db
ALL_SCOPES = [s.value for s in Scope]


@pytest.fixture
def fake_engine():
    fake = FakePhishingEngineClient()
    with patch("apps.api.views.get_client", return_value=fake):
        yield fake


def make_key(django_user_model, group="Security Admin", scopes=ALL_SCOPES, **kwargs):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=f"u-{group}", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return ApiKey.generate(name="bot", owner=user, scopes=scopes, **kwargs)


def call(client, name, raw_key=None, method="get", body=None):
    headers = {"HTTP_AUTHORIZATION": f"Api-Key {raw_key}"} if raw_key else {}
    url = reverse(f"api:{name}")
    if method == "post":
        return client.post(url, data=json.dumps(body or {}), content_type="application/json", **headers)
    return client.get(url, **headers)


# --- keys ----------------------------------------------------------------------------------


def test_only_a_hash_of_the_key_is_stored(django_user_model):
    key, raw = make_key(django_user_model)

    assert raw.startswith("sk_sim_")
    assert key.hashed_key == hash_key(raw) and raw not in key.hashed_key
    assert not ApiKey.objects.filter(hashed_key=raw).exists()


@pytest.mark.parametrize("header", [None, "Api-Key wrong", "Bearer sk_sim_x", "Api-Key "])
def test_missing_or_wrong_key_is_401(client, django_user_model, header):
    make_key(django_user_model)
    headers = {"HTTP_AUTHORIZATION": header} if header else {}

    assert client.get(reverse("api:whoami"), **headers).status_code == 401


def test_revoked_and_expired_keys_stop_working(client, django_user_model):
    key, raw = make_key(django_user_model)
    assert call(client, "whoami", raw).status_code == 200

    key.revoked_at = timezone.now()
    key.save()
    assert call(client, "whoami", raw).status_code == 401

    _, raw2 = ApiKey.generate(name="old", owner=key.owner, scopes=ALL_SCOPES, expires_at=timezone.now() - timedelta(minutes=1))
    assert call(client, "whoami", raw2).status_code == 401


def test_a_deactivated_owner_disables_their_keys(client, django_user_model):
    key, raw = make_key(django_user_model)
    key.owner.is_active = False
    key.owner.save()

    assert call(client, "whoami", raw).status_code == 401


def test_whoami_reports_the_key_and_records_use(client, django_user_model):
    key, raw = make_key(django_user_model)

    data = call(client, "whoami", raw).json()

    assert data["prefix"] == key.prefix and data["owner"] == key.owner.username
    key.refresh_from_db()
    assert key.last_used_at is not None


# --- scope ∩ owner permission --------------------------------------------------------------


def test_a_key_without_the_scope_is_forbidden(client, django_user_model, fake_engine):
    _, raw = make_key(django_user_model, scopes=[Scope.READ])

    assert call(client, "email-templates", raw).status_code == 403


def test_a_key_is_never_more_powerful_than_its_owner(client, django_user_model, fake_engine):
    # Report Viewers can't create campaigns, so even a key claiming that scope can't.
    _, raw = make_key(django_user_model, group="Report Viewer", scopes=ALL_SCOPES)

    response = call(client, "campaigns", raw, "post", {"name": "x", "template_name": "t", "landing_page_name": "p", "landing_page_url": "https://x.example"})

    assert response.status_code == 403
    assert not Campaign.objects.exists()


# --- drafting content ----------------------------------------------------------------------


def test_creates_a_training_module_with_a_quiz(client, django_user_model):
    _, raw = make_key(django_user_model)
    body = {
        "title": "Spotting invoice fraud",
        "content_url": "https://training.example/invoice",
        "quiz": {"passing_score_percent": 75, "questions": [
            {"text": "What should you check first?", "choices": [
                {"text": "The sender's real address", "is_correct": True}, {"text": "The logo", "is_correct": False}]},
        ]},
    }

    response = call(client, "training-modules", raw, "post", body)

    assert response.status_code == 201
    module = TrainingModule.objects.get()
    assert module.quiz.passing_score_percent == 75
    assert module.quiz.questions.get().choices.filter(is_correct=True).count() == 1
    assert AuditLogEntry.objects.filter(action="training_module_created", metadata__via="api").exists()


def test_an_invalid_quiz_rolls_back_the_whole_module(client, django_user_model):
    _, raw = make_key(django_user_model)
    body = {"title": "T", "content_url": "https://t.example",
            "quiz": {"questions": [{"text": "Q", "choices": [{"text": "A", "is_correct": False}]}]}}

    response = call(client, "training-modules", raw, "post", body)

    assert response.status_code == 400 and "correct choice" in response.json()["error"]
    assert not TrainingModule.objects.exists()


def test_drafts_an_email_template_and_landing_page_in_the_engine(client, django_user_model, fake_engine):
    _, raw = make_key(django_user_model)

    t = call(client, "email-templates", raw, "post", {"name": "Invoice", "subject": "Your invoice", "html": "<p>Hi</p>"})
    p = call(client, "landing-pages", raw, "post", {"name": "Invoice page", "html": "<form></form>"})

    assert t.status_code == 201 and p.status_code == 201
    assert fake_engine.email_templates["Invoice"]["subject"] == "Your invoice"
    assert call(client, "email-templates", raw).json()["email_templates"][0]["name"] == "Invoice"


def test_landing_pages_can_never_capture_passwords(client, django_user_model, fake_engine):
    _, raw = make_key(django_user_model)

    response = call(client, "landing-pages", raw, "post", {"name": "P", "html": "<form></form>", "capture_passwords": True})

    assert response.json()["capture_passwords"] is False
    assert fake_engine.pages["P"]["capture_passwords"] is False


def test_campaigns_are_created_as_drafts_and_nothing_is_sent(client, django_user_model, fake_engine):
    department = DepartmentFactory(name="Finance")
    _, raw = make_key(django_user_model)
    body = {"name": "Q4 invoice test", "template_name": "Invoice", "landing_page_name": "Invoice page",
            "landing_page_url": "https://phish.example", "target_department": "Finance", "status": "approved"}

    response = call(client, "campaigns", raw, "post", body)

    assert response.status_code == 201
    campaign = Campaign.objects.get()
    assert campaign.status == Campaign.Status.DRAFT  # the "status": "approved" in the body is ignored
    assert campaign.target_department == department and campaign.gophish_campaign_id is None
    assert fake_engine.created_campaigns == [] and fake_engine.synced_groups == {}


def test_the_api_has_no_launch_approve_or_send_endpoint():
    # The safety boundary is structural: an API key simply has nothing to call that sends email.
    api_routes = [str(p.pattern) for p in get_resolver().url_patterns if str(p.pattern) == "api/"]
    assert api_routes
    from apps.api import urls

    names = {p.name for p in urls.urlpatterns}
    assert names == {"whoami", "training-modules", "email-templates", "landing-pages", "campaigns"}
    for word in ("launch", "approve", "send", "submit", "delete"):
        assert not any(word in (p.name or "") or word in str(p.pattern) for p in urls.urlpatterns)
