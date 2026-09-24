"""
Every registered admin page, for every role: it may 200 or be refused (403/302),
but it must never crash (5xx). Catches broken readonly_fields, missing
permissions wiring, and template errors across all phases at once.
"""

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.urls import NoReverseMatch, reverse

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.tests.factories import make_sample_data

pytestmark = pytest.mark.django_db

ROLES = ["superuser", "Security Admin", "Campaign Manager", "Training Manager", "Report Viewer", "Department Manager"]


def _pages():
    for model in admin.site._registry:
        meta = model._meta
        for suffix in ("changelist", "add"):
            yield f"admin:{meta.app_label}_{meta.model_name}_{suffix}"


@pytest.fixture
def sample_data():
    return make_sample_data()


@pytest.mark.parametrize("role", ROLES)
def test_no_admin_page_crashes_for_any_role(client, django_user_model, sample_data, role):
    SetupGroupsCommand().handle()
    if role == "superuser":
        user = django_user_model.objects.create_superuser("root", "r@example.com", "x")
    else:
        user = django_user_model.objects.create_user("u", password="x", is_staff=True)
        user.groups.add(Group.objects.get(name=role))
        sample_data["department"].managers.add(user)
    client.force_login(user)

    failures = []
    for name in _pages():
        response = client.get(reverse(name))
        if response.status_code >= 500:
            failures.append((name, response.status_code))
    assert not failures


@pytest.mark.parametrize("role", ["superuser", "Security Admin", "Report Viewer", "Department Manager"])
def test_no_admin_change_page_crashes(client, django_user_model, sample_data, role):
    SetupGroupsCommand().handle()
    if role == "superuser":
        user = django_user_model.objects.create_superuser("root", "r@example.com", "x")
    else:
        user = django_user_model.objects.create_user("u", password="x", is_staff=True)
        user.groups.add(Group.objects.get(name=role))
        sample_data["department"].managers.add(user)
    client.force_login(user)

    failures = []
    for obj in sample_data["objects"]:
        meta = obj._meta
        try:
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_change", args=[obj.pk])
        except NoReverseMatch:
            continue
        status = client.get(url).status_code
        if status >= 500:
            failures.append((meta.label, status))
    assert not failures
