"""Duplicate/delete campaigns, deactivate employees, delete empty departments, employee filter."""
import pytest
from django.urls import reverse

from apps.campaigns.models import Campaign
from apps.employees.models import Department, Employee

from .test_deliverability_gate import login

pytestmark = pytest.mark.django_db


def make_campaign(**kw):
    return Campaign.objects.create(name="C", template_name="t", landing_page_name="l", landing_page_url="http://x.test", **kw)


def test_duplicate_makes_a_fresh_draft(client, django_user_model):
    login(client, django_user_model, "Campaign Manager")
    source = make_campaign(status=Campaign.Status.LAUNCHED, gophish_campaign_id="9")

    client.post(reverse("manage:campaign-duplicate", args=[source.pk]))

    copy = Campaign.objects.exclude(pk=source.pk).get()
    assert copy.name == "Copy of C" and copy.status == Campaign.Status.DRAFT and copy.gophish_campaign_id is None


def test_only_security_admin_can_delete_and_never_a_launched_one(client, django_user_model):
    login(client, django_user_model, "Campaign Manager")
    draft = make_campaign()
    assert client.post(reverse("manage:campaign-delete", args=[draft.pk])).status_code == 403
    assert Campaign.objects.filter(pk=draft.pk).exists()

    client.logout()
    django_user_model.objects.all().delete()
    login(client, django_user_model, "Security Admin")
    launched = make_campaign(status=Campaign.Status.LAUNCHED, gophish_campaign_id="1")
    client.post(reverse("manage:campaign-delete", args=[launched.pk]))
    assert Campaign.objects.filter(pk=launched.pk).exists()
    client.post(reverse("manage:campaign-delete", args=[draft.pk]))
    assert not Campaign.objects.filter(pk=draft.pk).exists()


def test_deactivate_toggles_and_department_filter(client, django_user_model):
    login(client, django_user_model, "Security Admin")
    dept, other = Department.objects.create(name="A"), Department.objects.create(name="B")
    e = Employee.objects.create(full_name="Ann", email="ann@x.test", department=dept)
    Employee.objects.create(full_name="Bob", email="bob@x.test", department=other)

    client.post(reverse("manage:employee-toggle-active", args=[e.pk]))
    e.refresh_from_db()
    assert e.is_active is False and e.deactivated_at is not None

    body = client.get(reverse("manage:employees"), {"department": dept.pk}).content.decode()
    assert "Ann" in body and "Bob" not in body and "Inactive" in body


def test_department_delete_refused_while_it_has_people(client, django_user_model):
    login(client, django_user_model, "Security Admin")
    dept = Department.objects.create(name="A")
    Employee.objects.create(full_name="Ann", email="ann@x.test", department=dept)
    client.post(reverse("manage:department-delete", args=[dept.pk]))
    assert Department.objects.filter(pk=dept.pk).exists()
    Employee.objects.all().delete()
    client.post(reverse("manage:department-delete", args=[dept.pk]))
    assert not Department.objects.filter(pk=dept.pk).exists()
