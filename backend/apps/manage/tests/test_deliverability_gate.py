import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand

pytestmark = pytest.mark.django_db


def login(client, django_user_model, group):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)


def test_view_only_roles_cannot_trigger_outbound_dns_lookups(client, django_user_model, monkeypatch):
    """The checker resolves whatever domain it is given from the internal network; that is a
    campaign manager's tool, not something a view-only account should be able to drive."""
    calls = []
    monkeypatch.setattr("apps.engagement.deliverability.check_domain", lambda *a, **k: calls.append(a) or {})
    login(client, django_user_model, "Report Viewer")

    response = client.post(reverse("manage:deliverability"), {"domain": "attacker.example"})

    assert response.status_code == 403 and calls == []


def test_campaign_managers_can_still_use_it(client, django_user_model):
    login(client, django_user_model, "Campaign Manager")

    assert client.get(reverse("manage:deliverability")).status_code == 200
