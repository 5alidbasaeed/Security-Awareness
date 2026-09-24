import json

import pytest
from django.contrib.auth.models import Group, Permission
from django.urls import reverse

from apps.api.models import ApiKey, Scope
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.intake.models import ReportedEmail
from apps.portal.tokens import sign_employee

pytestmark = pytest.mark.django_db


def staff(client, django_user_model, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=f"s-{group}", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


def test_employee_reports_from_the_portal(client):
    employee = EmployeeFactory()
    client.get(reverse("portal:enter", args=[sign_employee(employee)]))

    client.post(reverse("portal:report"), {"subject": "Urgent invoice", "sender": "billing@odd.example", "notes": "Weird link"})

    report = ReportedEmail.objects.get()
    assert report.reporter == employee and report.source == "portal" and report.verdict == "new"


def test_an_empty_portal_report_is_rejected(client):
    employee = EmployeeFactory()
    client.get(reverse("portal:enter", args=[sign_employee(employee)]))

    client.post(reverse("portal:report"), {})

    assert not ReportedEmail.objects.exists()


def test_mail_integration_reports_through_the_api(client, django_user_model):
    user = staff(client, django_user_model)
    reporter = EmployeeFactory(email="pat@corp.example")
    _, raw = ApiKey.generate(name="mailbox", owner=user, scopes=[Scope.REPORTS_WRITE])

    response = client.post(reverse("api:reported-emails"), data=json.dumps(
        {"reporter_email": "PAT@corp.example", "subject": "Gift card request", "sender": "ceo@lookalike.example"}),
        content_type="application/json", HTTP_AUTHORIZATION=f"Api-Key {raw}")

    assert response.status_code == 201
    assert ReportedEmail.objects.get().reporter == reporter


def test_api_rejects_an_unknown_reporter(client, django_user_model):
    user = staff(client, django_user_model)
    _, raw = ApiKey.generate(name="mailbox", owner=user, scopes=[Scope.REPORTS_WRITE])

    response = client.post(reverse("api:reported-emails"), data=json.dumps({"reporter_email": "who@x.example"}),
                           content_type="application/json", HTTP_AUTHORIZATION=f"Api-Key {raw}")

    assert response.status_code == 400 and not ReportedEmail.objects.exists()


def test_security_admin_triages_a_report(client, django_user_model):
    user = staff(client, django_user_model)
    report = ReportedEmail.objects.create(reporter=EmployeeFactory(), subject="S", source="portal")

    client.post(reverse("manage:reported-triage", args=[report.pk]), {"verdict": "malicious", "triage_notes": "Blocked sender"})

    report.refresh_from_db()
    assert report.verdict == "malicious" and report.triaged_by == user and report.triaged_at


def test_report_viewer_can_see_but_not_triage(client, django_user_model):
    staff(client, django_user_model, "Report Viewer")
    report = ReportedEmail.objects.create(reporter=EmployeeFactory(), subject="S", source="portal")

    assert client.get(reverse("manage:reported")).status_code == 200
    assert client.post(reverse("manage:reported-triage", args=[report.pk]), {"verdict": "safe"}).status_code == 403


def test_department_manager_only_sees_reports_from_their_people(client, django_user_model):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="dm", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name="Department Manager"))
    user.user_permissions.add(*Permission.objects.filter(codename="view_reportedemail"))
    client.force_login(user)
    mine, other = DepartmentFactory(), DepartmentFactory()
    mine.managers.add(user)
    ReportedEmail.objects.create(reporter=EmployeeFactory(department=mine), subject="Mine", source="portal")
    ReportedEmail.objects.create(reporter=EmployeeFactory(department=other), subject="Theirs", source="portal")

    body = client.get(reverse("manage:reported")).content.decode()

    assert "Mine" in body and "Theirs" not in body


def test_teachable_moment_page_is_public_and_anonymous(client):
    response = client.get(reverse("portal:learn"))

    assert response.status_code == 200
    assert b"simulated phishing email" in response.content
