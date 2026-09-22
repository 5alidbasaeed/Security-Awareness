from datetime import timedelta

import pytest
from django.utils import timezone

from apps.training.tasks import send_training_reminders
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db


def test_overdue_incomplete_assignment_gets_reminded(mailoutbox):
    assignment = TrainingAssignmentFactory()
    assignment.assigned_at = timezone.now() - timedelta(days=3)
    assignment.save(update_fields=["assigned_at"])

    sent = send_training_reminders()

    assert sent == 1
    assert len(mailoutbox) == 1
    assert assignment.employee.email in mailoutbox[0].to
    assignment.refresh_from_db()
    assert assignment.last_reminded_at is not None


def test_recently_assigned_is_not_reminded_yet(mailoutbox):
    TrainingAssignmentFactory()  # assigned_at defaults to now — inside the 2-day grace period

    sent = send_training_reminders()

    assert sent == 0
    assert len(mailoutbox) == 0


def test_completed_assignment_is_never_reminded(mailoutbox):
    assignment = TrainingAssignmentFactory(completed_at=timezone.now())
    assignment.assigned_at = timezone.now() - timedelta(days=5)
    assignment.save(update_fields=["assigned_at"])

    sent = send_training_reminders()

    assert sent == 0
    assert len(mailoutbox) == 0


def test_reminded_recently_is_not_reminded_again(mailoutbox):
    assignment = TrainingAssignmentFactory()
    assignment.assigned_at = timezone.now() - timedelta(days=3)
    assignment.last_reminded_at = timezone.now() - timedelta(hours=1)
    assignment.save(update_fields=["assigned_at", "last_reminded_at"])

    sent = send_training_reminders()

    assert sent == 0
    assert len(mailoutbox) == 0
