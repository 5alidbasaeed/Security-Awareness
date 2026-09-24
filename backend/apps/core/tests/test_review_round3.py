"""Regression tests from the full Phase 0-4 review."""

import csv
import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.core import mail
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.campaigns.services import CampaignLaunchError, launch_campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.admin import EmployeeAdmin
from apps.employees.models import Employee
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.tests.fakes import FakePhishingEngineClient
from apps.training.admin import QuizAttemptAdmin
from apps.training.models import QuizAttempt
from apps.training.tasks import send_training_reminders
from apps.training.tests.factories import QuizFactory, TrainingAssignmentFactory

pytestmark = pytest.mark.django_db


def test_new_assignments_get_a_due_date(settings):
    settings.TRAINING_DUE_DAYS = 14

    assignment = TrainingAssignmentFactory()

    assert assignment.due_at is not None
    assert abs(assignment.due_at - (timezone.now() + timedelta(days=14))) < timedelta(minutes=1)


def test_an_explicit_due_date_is_kept():
    due = timezone.now() + timedelta(days=3)

    assert TrainingAssignmentFactory(due_at=due).due_at == due


def test_one_undeliverable_reminder_does_not_block_the_rest():
    old = timezone.now() - timedelta(days=5)
    bad = TrainingAssignmentFactory(employee=EmployeeFactory(email="bad@example.com"))
    good = TrainingAssignmentFactory(employee=EmployeeFactory(email="good@example.com"))
    type(bad).objects.filter(pk__in=[bad.pk, good.pk]).update(assigned_at=old)

    real_send = mail.send_mail

    def flaky(*args, **kwargs):
        if "bad@example.com" in kwargs["recipient_list"]:
            raise OSError("mailbox unavailable")
        return real_send(*args, **kwargs)

    with patch("apps.training.tasks.send_mail", side_effect=flaky):
        sent = send_training_reminders()

    good.refresh_from_db()
    bad.refresh_from_db()
    assert sent == 1
    assert good.last_reminded_at is not None
    assert bad.last_reminded_at is None  # will be retried next run


def test_quiz_attempt_passed_is_derived_from_the_score(rf, django_user_model):
    quiz = QuizFactory(passing_score_percent=80)
    assignment = TrainingAssignmentFactory(module=quiz.module)
    user = django_user_model.objects.create_superuser("root", "r@example.com", "x")
    request = rf.post("/")
    request.user = user
    request.session = {}
    request._messages = type("M", (), {"add": lambda *a, **k: None})()

    attempt = QuizAttempt(assignment=assignment, score_percent=10, passed=True)  # staff claims a pass
    QuizAttemptAdmin(QuizAttempt, admin.site).save_model(request, attempt, None, change=False)

    attempt.refresh_from_db()
    assignment.refresh_from_db()
    assert attempt.passed is False
    assert assignment.completed_at is None


def test_launch_with_no_eligible_employees_is_a_clear_error():
    department = DepartmentFactory()
    EmployeeFactory(department=department, is_exempt=True)
    campaign = CampaignFactory(status=Campaign.Status.APPROVED, target_department=department)
    client = FakePhishingEngineClient()

    with pytest.raises(CampaignLaunchError, match="no eligible employees"):
        launch_campaign(campaign, actor=None, client=client)

    assert client.created_campaigns == []


def test_anonymous_analytics_request_redirects_to_the_admin_login(client):
    response = client.get(reverse("analytics:departments"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("admin:login"))


def test_csv_export_neutralises_spreadsheet_formulas(rf, django_user_model):
    user = django_user_model.objects.create_superuser("root", "r@example.com", "x")
    EmployeeFactory(full_name='=HYPERLINK("http://evil.example","click")', email="f@example.com")
    request = rf.post("/")
    request.user = user
    request.session = {}
    request._messages = type("M", (), {"add": lambda *a, **k: None})()

    response = EmployeeAdmin(Employee, admin.site).export_as_csv(request, Employee.objects.all())

    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert not rows[1][0].startswith("=")
