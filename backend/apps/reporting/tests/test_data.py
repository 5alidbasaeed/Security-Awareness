from datetime import date, datetime, timezone as tz

import pytest

from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.models import Employee
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.reporting.data import ReportData, ReportScope
from apps.risk_scoring.services import ScoreHistory
from apps.training.models import TrainingAssignment
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db
E = Event.EventType


def utc(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=tz.utc)


def make_scope():
    return ReportScope(
        employees=Employee.objects.all(), campaigns=Campaign.objects.all(),
        assignments=TrainingAssignment.objects.all(), departments=None, label="All departments", is_scoped=False,
    )


def old_employee(**kwargs):
    """An employee who existed before every date the tests report on."""
    person = EmployeeFactory(**kwargs)
    Employee.objects.filter(pk=person.pk).update(created_at=utc(2025, 1, 1))
    return person


def report(start, end, now=None):
    return ReportData(make_scope(), start, end, now=now or utc(2026, 9, 30))


def _backdate(model, pk, **fields):
    model.objects.filter(pk=pk).update(**fields)


def test_scores_are_recomputed_as_of_the_report_date_not_read_from_today():
    """The core promise of a historical report: later events must not leak into an earlier date."""
    person = EmployeeFactory()
    _backdate(Employee, person.pk, created_at=utc(2026, 1, 1))
    early, late = CampaignFactory(), CampaignFactory()
    EventFactory(employee=person, campaign=early, event_type=E.CREDENTIAL_ATTEMPT, occurred_at=utc(2026, 3, 1))
    EventFactory(employee=person, campaign=late, event_type=E.LINK_CLICKED, occurred_at=utc(2026, 8, 1))

    in_april = report(date(2026, 4, 1), date(2026, 4, 30)).risk_rows[0]
    in_september = report(date(2026, 9, 1), date(2026, 9, 30)).risk_rows[0]

    assert in_april["campaigns_failed"] == 1 and 35 < in_april["score"] < 45  # 50 points, decayed ~60 days
    assert in_september["campaigns_failed"] == 2  # the August click now counts too


def test_a_past_report_is_reproducible_after_new_events_arrive():
    person = EmployeeFactory()
    _backdate(Employee, person.pk, created_at=utc(2026, 1, 1))
    EventFactory(employee=person, event_type=E.LINK_CLICKED, occurred_at=utc(2026, 3, 1))
    first = report(date(2026, 3, 1), date(2026, 3, 31)).risk_rows

    EventFactory(employee=person, event_type=E.CREDENTIAL_ATTEMPT, occurred_at=utc(2026, 8, 1))
    again = report(date(2026, 3, 1), date(2026, 3, 31)).risk_rows

    assert first == again


def test_employees_who_did_not_exist_yet_are_left_out():
    veteran, newcomer = EmployeeFactory(), EmployeeFactory()
    _backdate(Employee, veteran.pk, created_at=utc(2026, 1, 1))
    _backdate(Employee, newcomer.pk, created_at=utc(2026, 6, 1))

    rows = report(date(2026, 3, 1), date(2026, 3, 31)).risk_rows

    assert [r["email"] for r in rows] == [veteran.email]


def test_report_date_is_never_in_the_future():
    data = report(date(2026, 9, 1), date(2026, 12, 31), now=utc(2026, 9, 30))

    assert data.as_of == utc(2026, 9, 30)


def test_campaign_rows_only_include_campaigns_launched_in_the_period_and_events_up_to_the_date():
    department = DepartmentFactory()
    inside = CampaignFactory(name="In period", target_department=department)
    outside = CampaignFactory(name="Before period", target_department=department)
    _backdate(Campaign, inside.pk, launched_at=utc(2026, 3, 10), status="launched")
    _backdate(Campaign, outside.pk, launched_at=utc(2026, 1, 10), status="launched")
    a, b = EmployeeFactory(department=department), EmployeeFactory(department=department)
    for person in (a, b):
        EventFactory(employee=person, campaign=inside, event_type=E.EMAIL_SENT, occurred_at=utc(2026, 3, 10))
    EventFactory(employee=a, campaign=inside, event_type=E.LINK_CLICKED, occurred_at=utc(2026, 3, 11))
    EventFactory(employee=b, campaign=inside, event_type=E.LINK_CLICKED, occurred_at=utc(2026, 5, 1))  # after the date

    rows = report(date(2026, 3, 1), date(2026, 3, 31)).campaign_rows

    assert [r["campaign"] for r in rows] == ["In period"]
    assert rows[0]["targeted"] == 2 and rows[0]["failed"] == 1 and rows[0]["failure_rate_percent"] == 50.0


def test_training_status_is_evaluated_as_of_the_report_date():
    person = EmployeeFactory()
    assignment = TrainingAssignmentFactory(employee=person)
    _backdate(
        TrainingAssignment, assignment.pk,
        assigned_at=utc(2026, 3, 1), due_at=utc(2026, 3, 15), completed_at=utc(2026, 4, 20),
    )

    on_april_1 = report(date(2026, 3, 1), date(2026, 4, 1)).training_rows[0]
    on_may_1 = report(date(2026, 3, 1), date(2026, 5, 1)).training_rows[0]

    assert on_april_1["status"] == "Overdue" and on_april_1["completed"] == ""  # not yet done on that date
    assert on_may_1["status"] == "Completed"
    assert report(date(2026, 1, 1), date(2026, 2, 1)).training_rows == []  # not assigned yet


def test_department_rows_roll_up_scores_and_training():
    department = DepartmentFactory(name="Finance")
    a, b = EmployeeFactory(department=department), EmployeeFactory(department=department)
    for person in (a, b):
        _backdate(Employee, person.pk, created_at=utc(2026, 1, 1))
    EventFactory(employee=a, event_type=E.CREDENTIAL_ATTEMPT, occurred_at=utc(2026, 3, 30))
    done = TrainingAssignmentFactory(employee=a)
    _backdate(TrainingAssignment, done.pk, assigned_at=utc(2026, 3, 1), completed_at=utc(2026, 3, 5))

    row = report(date(2026, 3, 1), date(2026, 3, 31)).department_rows[0]

    assert row["department"] == "Finance" and row["employees"] == 2
    assert row["average_score"] == pytest.approx(25, abs=1)  # (~50 + 0) / 2
    assert row["training_completion_percent"] == 100.0


def test_headline_reports_direction_of_change():
    person = EmployeeFactory()
    _backdate(Employee, person.pk, created_at=utc(2026, 1, 1))
    EventFactory(employee=person, event_type=E.CREDENTIAL_ATTEMPT, occurred_at=utc(2026, 3, 20))

    headline = report(date(2026, 3, 1), date(2026, 3, 31)).headline

    assert headline["average_score_start"] == 0
    assert headline["score_change"] > 40 and headline["direction"] == "worsening"


def test_trend_has_one_point_per_week_capped():
    data = report(date(2025, 1, 1), date(2026, 3, 31))

    assert len(data.trend) == 26


def test_score_history_loads_events_once_and_scores_any_moment():
    person = EmployeeFactory()
    _backdate(Employee, person.pk, created_at=utc(2026, 1, 1))
    EventFactory(employee=person, event_type=E.LINK_CLICKED, occurred_at=utc(2026, 3, 1))
    history = ScoreHistory(Employee.objects.all(), until=utc(2026, 9, 1))

    assert history.scores_at(utc(2026, 2, 1))[person.pk].score == 0
    assert history.scores_at(utc(2026, 3, 2))[person.pk].score > 20
    assert history.scores_at(utc(2025, 12, 1)) == {}  # nobody existed yet
