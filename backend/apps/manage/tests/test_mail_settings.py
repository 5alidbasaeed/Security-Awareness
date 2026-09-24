"""Mail settings: the relay the simulation emails go through. Security Admin only, password write-only."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db

VALID = {
    "action": "save", "host": "10.12.180.10", "port": "25", "username": "svc@corp.example", "password": "s3cret-pw",
    "from_address": "IT Service Desk <helpdesk@corp.example>", "allow_self_signed": "on",
}


@pytest.fixture
def engine(monkeypatch):
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    return fake


def login(client, django_user_model, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)


def test_saving_updates_the_default_sending_profile(client, django_user_model, engine, settings):
    login(client, django_user_model)

    client.post(reverse("manage:mail-settings"), VALID)

    saved = engine.profile_settings[settings.GOPHISH_DEFAULT_SEND_PROFILE]
    assert (saved["host"], saved["port"], saved["username"], saved["ignore_cert_errors"]) == ("10.12.180.10", 25, "svc@corp.example", True)
    assert saved["password"] == "s3cret-pw"


def test_the_password_is_never_shown_or_logged(client, django_user_model, engine):
    login(client, django_user_model)
    client.post(reverse("manage:mail-settings"), VALID)

    page = client.get(reverse("manage:mail-settings")).content.decode()

    assert "s3cret-pw" not in page and "A password is set" in page
    assert not any("s3cret-pw" in str(e.metadata) + e.target_description for e in AuditLogEntry.objects.all())
    entry = AuditLogEntry.objects.get(action="mail_settings_updated")
    assert entry.metadata["password_changed"] is True


def test_a_blank_password_keeps_the_stored_one(client, django_user_model, engine, settings):
    login(client, django_user_model)
    client.post(reverse("manage:mail-settings"), VALID)

    client.post(reverse("manage:mail-settings"), {**VALID, "password": "", "port": "587"})

    saved = engine.profile_settings[settings.GOPHISH_DEFAULT_SEND_PROFILE]
    assert saved["password"] == "s3cret-pw" and saved["port"] == 587


@pytest.mark.parametrize("field,value", [
    ("host", "http://relay.example"), ("host", "relay:25"), ("host", ""), ("port", "0"), ("port", "70000"),
    ("port", "abc"), ("from_address", "not-an-address"),
])
def test_bad_values_are_rejected_and_nothing_is_saved(client, django_user_model, engine, field, value):
    login(client, django_user_model)

    response = client.post(reverse("manage:mail-settings"), {**VALID, field: value})

    assert response.status_code == 200 and not engine.profile_settings


def test_a_test_message_goes_to_the_chosen_address_and_is_audited(client, django_user_model, engine, settings):
    login(client, django_user_model)
    client.post(reverse("manage:mail-settings"), VALID)

    client.post(reverse("manage:mail-settings"), {"action": "test", "to": "me@corp.example"})

    assert engine.test_emails == [(settings.GOPHISH_DEFAULT_SEND_PROFILE, "me@corp.example")]
    assert AuditLogEntry.objects.filter(action="mail_test_sent").exists()


def test_a_failing_relay_shows_its_own_message(client, django_user_model, engine):
    login(client, django_user_model)
    client.post(reverse("manage:mail-settings"), VALID)
    engine.test_email_error = "535 authentication failed"

    response = client.post(reverse("manage:mail-settings"), {"action": "test", "to": "me@corp.example"}, follow=True)

    assert b"535 authentication failed" in response.content and not engine.test_emails


@pytest.mark.parametrize("group", ["Campaign Manager", "Training Manager", "Report Viewer", "Department Manager"])
def test_only_security_admins_can_change_where_email_is_relayed(client, django_user_model, engine, group):
    login(client, django_user_model, group)

    assert client.get(reverse("manage:mail-settings")).status_code == 403
    assert client.post(reverse("manage:mail-settings"), VALID).status_code == 403 and not engine.profile_settings


@pytest.mark.parametrize("field", ["from_address", "username"])
def test_line_breaks_cannot_be_smuggled_into_the_sender_or_login(client, django_user_model, engine, field):
    login(client, django_user_model)
    value = "a@corp.example\nBcc: victim@evil.example" if field == "from_address" else "svc\nX: y"

    response = client.post(reverse("manage:mail-settings"), {**VALID, field: value})

    assert response.status_code == 200 and not engine.profile_settings
