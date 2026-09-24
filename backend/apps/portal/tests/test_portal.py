import re

import pytest
from django.core import mail, signing
from django.urls import reverse

from apps.employees.tests.factories import EmployeeFactory
from apps.portal.tokens import employee_from_token, sign_employee
from apps.training.models import Quiz, QuizAttempt, QuizChoice, QuizQuestion
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db


def sign_in(client, employee):
    return client.get(reverse("portal:enter", args=[sign_employee(employee)]))


def quiz_for(module, pass_mark=100):
    quiz = Quiz.objects.create(module=module, passing_score_percent=pass_mark)
    q = QuizQuestion.objects.create(quiz=quiz, text="Hover before you click?")
    right = QuizChoice.objects.create(question=q, text="Yes", is_correct=True)
    wrong = QuizChoice.objects.create(question=q, text="No", is_correct=False)
    return q, right, wrong


# --- sign-in -----------------------------------------------------------------------------


def test_portal_requires_sign_in(client):
    assert client.get(reverse("portal:home")).url == reverse("portal:login")


def test_login_form_does_not_reveal_whether_an_email_exists(client):
    EmployeeFactory(email="real@corp.example")

    known = client.post(reverse("portal:login"), {"email": "real@corp.example"})
    unknown = client.post(reverse("portal:login"), {"email": "nobody@corp.example"})

    assert known.content == unknown.content
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["real@corp.example"]


def test_emailed_link_signs_the_employee_in(client):
    employee = EmployeeFactory()
    client.post(reverse("portal:login"), {"email": employee.email})
    link = re.search(r"(/portal/enter/\S+)", mail.outbox[0].body).group(1)

    client.get(link)

    assert client.get(reverse("portal:home")).status_code == 200


def test_tampered_expired_or_inactive_links_are_rejected(client, settings):
    employee = EmployeeFactory()
    assert employee_from_token(sign_employee(employee) + "x") is None

    settings.PORTAL_LINK_MAX_AGE_SECONDS = -1
    assert employee_from_token(sign_employee(employee)) is None
    settings.PORTAL_LINK_MAX_AGE_SECONDS = 3600

    employee.is_active = False
    employee.save()
    assert employee_from_token(sign_employee(employee)) is None
    assert client.get(reverse("portal:enter", args=["garbage"])).status_code == 400


def test_a_token_from_another_salt_does_not_work():
    employee = EmployeeFactory()
    assert employee_from_token(signing.dumps({"eid": employee.pk})) is None


# --- isolation ---------------------------------------------------------------------------


def test_an_employee_never_sees_another_employees_assignment(client):
    mine = TrainingAssignmentFactory()
    theirs = TrainingAssignmentFactory()
    sign_in(client, mine.employee)

    home = client.get(reverse("portal:home")).content.decode()
    assert mine.module.title in home
    assert client.get(reverse("portal:assignment", args=[theirs.pk])).status_code == 404
    assert client.post(reverse("portal:submit-quiz", args=[theirs.pk])).status_code == 404
    assert client.get(reverse("portal:certificate", args=[theirs.pk])).status_code == 404


def test_portal_session_gives_no_staff_access(client):
    sign_in(client, EmployeeFactory())

    assert client.get(reverse("dashboard:overview")).status_code == 302  # still sent to staff login
    assert client.get(reverse("manage:index")).status_code == 302


# --- quiz and completion -----------------------------------------------------------------


def test_passing_the_quiz_completes_the_assignment_and_issues_a_certificate(client):
    item = TrainingAssignmentFactory()
    q, right, _ = quiz_for(item.module)
    sign_in(client, item.employee)

    response = client.post(reverse("portal:submit-quiz", args=[item.pk]), {f"q{q.pk}": right.pk})

    item.refresh_from_db()
    assert response.url == reverse("portal:certificate", args=[item.pk])
    assert item.completed_at is not None and QuizAttempt.objects.get().passed
    assert item.employee.full_name in client.get(response.url).content.decode()


def test_failing_the_quiz_records_the_attempt_but_does_not_complete(client):
    item = TrainingAssignmentFactory()
    q, _, wrong = quiz_for(item.module)
    sign_in(client, item.employee)

    client.post(reverse("portal:submit-quiz", args=[item.pk]), {f"q{q.pk}": wrong.pk})

    item.refresh_from_db()
    assert item.completed_at is None
    attempt = QuizAttempt.objects.get()
    assert attempt.score_percent == 0 and not attempt.passed


def test_a_module_without_a_quiz_is_completed_by_confirming(client):
    item = TrainingAssignmentFactory()
    sign_in(client, item.employee)

    client.post(reverse("portal:assignment", args=[item.pk]), {"action": "complete"})

    item.refresh_from_db()
    assert item.completed_at is not None


def test_a_module_with_a_quiz_cannot_be_completed_by_just_confirming(client):
    item = TrainingAssignmentFactory()
    quiz_for(item.module)
    sign_in(client, item.employee)

    client.post(reverse("portal:assignment", args=[item.pk]), {"action": "complete"})

    item.refresh_from_db()
    assert item.completed_at is None


def test_reminder_emails_carry_a_portal_sign_in_link():
    from datetime import timedelta

    from django.utils import timezone

    from apps.training.models import TrainingAssignment
    from apps.training.tasks import send_training_reminders

    item = TrainingAssignmentFactory()
    TrainingAssignment.objects.filter(pk=item.pk).update(assigned_at=timezone.now() - timedelta(days=3))

    send_training_reminders()

    assert "/portal/enter/" in mail.outbox[0].body


def test_a_quiz_with_no_questions_does_not_lock_the_employee_out(client):
    # score_quiz() returns 0% for an empty quiz, so treating it as a real quiz meant it could never be passed.
    item = TrainingAssignmentFactory()
    Quiz.objects.create(module=item.module, passing_score_percent=80)
    sign_in(client, item.employee)

    client.post(reverse("portal:assignment", args=[item.pk]), {"action": "complete"})

    item.refresh_from_db()
    assert item.completed_at is not None


# --- review fixes -------------------------------------------------------------------------


def test_sign_in_link_is_queued_for_every_address_so_the_page_answers_the_same(client, monkeypatch):
    queued = []
    monkeypatch.setattr("apps.portal.views.send_portal_link.delay", lambda email: queued.append(email))
    EmployeeFactory(email="real@corp.example")

    known = client.post(reverse("portal:login"), {"email": "real@corp.example"})
    unknown = client.post(reverse("portal:login"), {"email": "ghost@corp.example"})

    assert queued == ["real@corp.example", "ghost@corp.example"] and known.content == unknown.content


def test_a_broker_outage_does_not_change_the_answer_or_leak_anything(client, monkeypatch):
    def boom(email):
        raise ConnectionError("redis down")

    monkeypatch.setattr("apps.portal.views.send_portal_link.delay", boom)

    response = client.post(reverse("portal:login"), {"email": "real@corp.example"})

    assert response.status_code == 200 and b"Check your email" in response.content


def test_inactive_employees_get_no_link(client):
    from django.core import mail

    EmployeeFactory(email="gone@corp.example", is_active=False)

    client.post(reverse("portal:login"), {"email": "gone@corp.example"})

    assert mail.outbox == []


def test_a_named_proxy_header_is_used_for_throttling_only_when_configured(client, settings):
    settings.PORTAL_LINK_IP_LIMIT = 1
    settings.PORTAL_CLIENT_IP_HEADER = "HTTP_X_REAL_IP"
    from django.core import mail

    EmployeeFactory(email="a@corp.example"), EmployeeFactory(email="b@corp.example")
    client.post(reverse("portal:login"), {"email": "a@corp.example"}, REMOTE_ADDR="10.0.0.1", HTTP_X_REAL_IP="203.0.113.1")
    client.post(reverse("portal:login"), {"email": "b@corp.example"}, REMOTE_ADDR="10.0.0.1", HTTP_X_REAL_IP="203.0.113.2")

    assert len(mail.outbox) == 2  # same proxy address, different real clients: not one shared bucket
