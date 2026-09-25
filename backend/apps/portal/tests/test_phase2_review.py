"""Regressions from the 2026-09-25 end-to-end portal review (opus_comments/phase2_portal_defects.md)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.employees.tests.factories import EmployeeFactory
from apps.portal.tests.test_course import deck
from apps.portal.tests.test_portal import quiz_for, sign_in
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db


def test_home_lists_outstanding_training_before_completed(client):
    employee = EmployeeFactory()
    done = TrainingAssignmentFactory(employee=employee, completed_at=timezone.now() - timedelta(days=1))
    later = TrainingAssignmentFactory(employee=employee, due_at=timezone.now() + timedelta(days=9))
    sooner = TrainingAssignmentFactory(employee=employee, due_at=timezone.now() + timedelta(days=2))
    sign_in(client, employee)

    html = client.get(reverse("portal:home")).content.decode()
    positions = [html.index(reverse("portal:assignment", args=[a.pk])) for a in (sooner, later, done)]
    assert positions == sorted(positions)


def test_quiz_cannot_be_posted_before_the_course_is_opened(client):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee)
    deck(item.module)
    q, right, _ = quiz_for(item.module)
    sign_in(client, employee)

    response = client.post(reverse("portal:submit-quiz", args=[item.pk]), {f"q{q.pk}": right.pk})

    item.refresh_from_db()
    assert response.url == reverse("portal:course", args=[item.pk])
    assert item.completed_at is None and not item.quiz_attempts.exists()


def test_slides_only_module_needs_the_course_opened_before_self_completion(client):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee)
    deck(item.module, 2)
    sign_in(client, employee)
    url = reverse("portal:assignment", args=[item.pk])

    assert b"I've completed this training" not in client.get(url).content
    client.post(url, {"action": "complete"})
    item.refresh_from_db()
    assert item.completed_at is None

    client.get(reverse("portal:course", args=[item.pk]))  # opening the course marks it started
    client.post(url, {"action": "complete"})
    item.refresh_from_db()
    assert item.completed_at is not None


def test_signed_in_portal_pages_are_never_cached(client):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee)
    sign_in(client, employee)
    for url in (reverse("portal:home"), reverse("portal:assignment", args=[item.pk]), reverse("portal:report")):
        assert "no-store" in client.get(url)["Cache-Control"], url


def test_a_module_with_no_content_cannot_be_self_completed(client):
    employee = EmployeeFactory()
    item = TrainingAssignmentFactory(employee=employee, module__content_url="")
    sign_in(client, employee)
    url = reverse("portal:assignment", args=[item.pk])

    assert b"I've completed this training" not in client.get(url).content
    client.post(url, {"action": "complete"})
    item.refresh_from_db()
    assert item.completed_at is None


def test_reminders_skip_modules_with_nothing_to_take_and_state_the_due_date(mailoutbox):
    from apps.training.tasks import send_training_reminders

    long_ago = timezone.now() - timedelta(days=5)
    empty = TrainingAssignmentFactory(module__content_url="", due_at=long_ago)
    real = TrainingAssignmentFactory(due_at=timezone.now() - timedelta(days=1))
    type(real).objects.filter(pk__in=[empty.pk, real.pk]).update(assigned_at=long_ago)

    send_training_reminders()

    recipients = [m.to[0] for m in mailoutbox]
    assert real.employee.email in recipients and empty.employee.email not in recipients
    assert "is now overdue" in mailoutbox[0].body
