"""Sending a test of a composed email: limited to the sender/approved domains, rate-limited, audited."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.models import EmailDraft
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def engine(monkeypatch):
    fake = FakePhishingEngineClient()
    fake.upsert_sending_profile(name="default", host="relay.corp.example", port=25, username="", password="pw",
                                from_address="IT <it@corp.example>", ignore_cert_errors=False)
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    return fake


def login(client, django_user_model, group="Campaign Manager", email="staffer@corp.example"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True, email=email)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


def make_draft(**overrides):
    fields = {"name": "Test draft", "subject": "Password expiry", "layout": "corporate", "body_html": "<p>Hi {{.FirstName}}</p>"}
    fields.update(overrides)
    return EmailDraft.objects.create(**fields)


def test_a_test_message_is_sent_to_the_senders_own_address(client, django_user_model, engine):
    login(client, django_user_model, email="staffer@corp.example")
    draft = make_draft()

    response = client.post(reverse("manage:template-edit", args=[draft.pk]), {"action": "test", "to": "staffer@corp.example"})

    assert response.status_code == 302
    assert engine.test_emails == [("default", "staffer@corp.example")]
    assert engine.last_test["template"]["subject"] == "Password expiry"
    assert AuditLogEntry.objects.filter(action="email_test_sent").exists()


def test_an_approved_company_domain_is_allowed(client, django_user_model, engine, settings):
    settings.TEST_EMAIL_ALLOWED_DOMAINS = ["corp.example"]
    login(client, django_user_model, email="staffer@corp.example")
    draft = make_draft()

    client.post(reverse("manage:template-edit", args=[draft.pk]), {"action": "test", "to": "colleague@corp.example"})

    assert engine.test_emails == [("default", "colleague@corp.example")]


def test_an_outside_address_is_refused(client, django_user_model, engine, settings):
    settings.TEST_EMAIL_ALLOWED_DOMAINS = []
    login(client, django_user_model, email="staffer@corp.example")
    draft = make_draft()

    response = client.post(reverse("manage:template-edit", args=[draft.pk]), {"action": "test", "to": "victim@outside.example"}, follow=True)

    assert b"only go to your own address" in response.content
    assert engine.test_emails == []


def test_sends_are_capped_per_hour(client, django_user_model, engine, settings):
    settings.TEST_EMAIL_LIMIT_PER_HOUR = 2
    login(client, django_user_model, email="staffer@corp.example")
    draft = make_draft()

    for _ in range(3):
        client.post(reverse("manage:template-edit", args=[draft.pk]), {"action": "test", "to": "staffer@corp.example"})

    assert len(engine.test_emails) == 2


def test_a_relay_failure_shows_its_own_message_and_does_not_count_against_the_cap(client, django_user_model, engine, settings):
    settings.TEST_EMAIL_LIMIT_PER_HOUR = 1
    login(client, django_user_model, email="staffer@corp.example")
    draft = make_draft()
    engine.test_email_error = "550 relay refused"

    response = client.post(reverse("manage:template-edit", args=[draft.pk]), {"action": "test", "to": "staffer@corp.example"}, follow=True)

    assert b"550 relay refused" in response.content and not engine.test_emails


def test_report_viewers_cannot_send_a_test(client, django_user_model, engine):
    login(client, django_user_model, group="Report Viewer")
    draft = make_draft()

    response = client.post(reverse("manage:template-edit", args=[draft.pk]), {"action": "test", "to": "x@corp.example"})

    assert response.status_code == 403 and not engine.test_emails


def test_the_checks_panel_flags_an_email_with_no_link_and_shows_for_an_existing_draft(client, django_user_model, engine):
    login(client, django_user_model)
    draft = make_draft(body_html="<p>Hi</p>")  # no {{.URL}} anywhere

    page = client.get(reverse("manage:template-edit", args=[draft.pk])).content.decode()

    assert "no link or button" in page and "Send yourself a test" in page


def test_the_checks_panel_is_absent_when_creating_a_new_email(client, django_user_model, engine):
    login(client, django_user_model)

    page = client.get(reverse("manage:template-new")).content.decode()

    assert "Send yourself a test" not in page
