import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory

pytestmark = pytest.mark.django_db


def _login(client, django_user_model, group_name, username="u"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group_name))
    client.force_login(user)
    return user


def test_anonymous_users_are_redirected_to_login(client):
    response = client.get(reverse("analytics:departments"))

    assert response.status_code == 302


def test_staff_without_analytics_permission_is_forbidden(client, django_user_model):
    _login(client, django_user_model, "Training Manager")

    assert client.get(reverse("analytics:departments")).status_code == 403


def test_non_staff_is_forbidden(client, django_user_model):
    user = _login(client, django_user_model, "Report Viewer")
    user.is_staff = False
    user.save()

    assert client.get(reverse("analytics:departments")).status_code == 403


def test_post_is_not_allowed(client, django_user_model):
    _login(client, django_user_model, "Report Viewer")

    assert client.post(reverse("analytics:departments")).status_code == 405


def test_report_viewer_sees_every_department(client, django_user_model):
    DepartmentFactory(), DepartmentFactory()
    _login(client, django_user_model, "Report Viewer")

    response = client.get(reverse("analytics:departments"))

    assert response.status_code == 200
    assert len(response.json()["departments"]) == 2


def test_department_manager_only_sees_their_own_department(client, django_user_model):
    mine, other = DepartmentFactory(), DepartmentFactory()
    user = _login(client, django_user_model, "Department Manager")
    mine.managers.add(user)
    my_employee, other_employee = EmployeeFactory(department=mine), EmployeeFactory(department=other)
    my_campaign, other_campaign = CampaignFactory(target_department=mine), CampaignFactory(target_department=other)

    listing = client.get(reverse("analytics:departments")).json()["departments"]
    assert [d["department_id"] for d in listing] == [mine.pk]

    assert client.get(reverse("analytics:department-trend", args=[mine.pk])).status_code == 200
    assert client.get(reverse("analytics:department-trend", args=[other.pk])).status_code == 404
    assert client.get(reverse("analytics:campaign", args=[my_campaign.pk])).status_code == 200
    assert client.get(reverse("analytics:campaign", args=[other_campaign.pk])).status_code == 404
    assert client.get(reverse("analytics:employee-history", args=[my_employee.pk])).status_code == 200
    assert client.get(reverse("analytics:employee-history", args=[other_employee.pk])).status_code == 404


def test_employee_history_payload_shape(client, django_user_model):
    department = DepartmentFactory()
    employee = EmployeeFactory(department=department)
    _login(client, django_user_model, "Report Viewer")

    data = client.get(reverse("analytics:employee-history", args=[employee.pk])).json()

    assert data["current_score"] is None
    assert data["trend"] == "insufficient_data"
    assert data["history"] == []
    assert data["training"]["assigned"] == 0


@pytest.mark.parametrize("weeks, expected", [("4", 4), ("0", 1), ("9999", 104), ("abc", 12), ("-3", 12)])
def test_trend_weeks_parameter_is_clamped(client, django_user_model, weeks, expected):
    department = DepartmentFactory()
    _login(client, django_user_model, "Report Viewer")

    data = client.get(reverse("analytics:department-trend", args=[department.pk]), {"weeks": weeks}).json()

    assert len(data["trend"]) == expected
