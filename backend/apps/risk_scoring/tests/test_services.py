from datetime import timedelta

import pytest
from django.utils import timezone

from apps.employees.tests.factories import EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring import tasks
from apps.risk_scoring.models import RiskScoreSnapshot
from apps.risk_scoring.services import compute_and_store_snapshot, latest_snapshots

pytestmark = pytest.mark.django_db


def test_snapshot_records_score_version_and_metrics():
    employee = EmployeeFactory()
    EventFactory(employee=employee, event_type=Event.EventType.CREDENTIAL_ATTEMPT)

    snapshot = compute_and_store_snapshot(employee)

    assert float(snapshot.score) == 50
    assert snapshot.algorithm_version == "v1"
    assert snapshot.contributing_metrics["campaigns_failed"] == 1


def test_recomputing_inserts_a_new_row_and_never_updates():
    employee = EmployeeFactory()
    compute_and_store_snapshot(employee)
    EventFactory(employee=employee, event_type=Event.EventType.LINK_CLICKED)
    compute_and_store_snapshot(employee)

    assert RiskScoreSnapshot.objects.filter(employee=employee).count() == 2
    assert float(latest_snapshots().get(employee=employee).score) == 25


def test_snapshots_are_immutable():
    snapshot = compute_and_store_snapshot(EmployeeFactory())

    with pytest.raises(TypeError):
        snapshot.save()
    with pytest.raises(TypeError):
        RiskScoreSnapshot.objects.all().update(score=0)
    with pytest.raises(TypeError):
        RiskScoreSnapshot.objects.all().delete()


def test_unknown_algorithm_version_is_rejected():
    with pytest.raises(ValueError):
        compute_and_store_snapshot(EmployeeFactory(), algorithm_version="v99")


def test_events_after_the_scoring_time_are_ignored():
    employee = EmployeeFactory()
    EventFactory(employee=employee, occurred_at=timezone.now() + timedelta(days=1))

    assert float(compute_and_store_snapshot(employee).score) == 0


def test_latest_snapshots_returns_one_row_per_employee():
    a, b = EmployeeFactory(), EmployeeFactory()
    for employee in (a, a, b):
        compute_and_store_snapshot(employee)

    assert latest_snapshots().count() == 2


def test_recompute_score_task_handles_a_missing_employee():
    tasks.recompute_score(999999)  # must not raise

    assert RiskScoreSnapshot.objects.count() == 0


def test_recompute_all_fans_out_one_task_per_employee(monkeypatch):
    queued = []
    monkeypatch.setattr(tasks.recompute_score, "delay", lambda *args: queued.append(args))
    employees = [EmployeeFactory(), EmployeeFactory()]

    tasks.recompute_all_scores()

    assert sorted(a[0] for a in queued) == sorted(e.pk for e in employees)


@pytest.mark.parametrize(
    "event_type, expected",
    [
        (Event.EventType.LINK_CLICKED, True),
        (Event.EventType.CREDENTIAL_ATTEMPT, True),
        (Event.EventType.PHISHING_REPORTED, True),
        (Event.EventType.EMAIL_OPENED, False),
        (Event.EventType.EMAIL_SENT, False),
    ],
)
def test_only_scoring_events_trigger_a_recompute(monkeypatch, django_capture_on_commit_callbacks, event_type, expected):
    queued = []
    monkeypatch.setattr(tasks.recompute_score, "delay", lambda *args: queued.append(args))

    with django_capture_on_commit_callbacks(execute=True):
        event = EventFactory(event_type=event_type)

    assert bool(queued) is expected
    if expected:
        assert queued == [(event.employee_id,)]
