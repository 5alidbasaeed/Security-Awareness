from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring import analytics
from apps.risk_scoring.models import RiskScoreSnapshot
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db
E = Event.EventType


def _snapshot(employee, score, days_ago=0):
    snapshot = RiskScoreSnapshot.objects.create(employee=employee, score=score, algorithm_version="v1")
    # computed_at is auto_now_add and queryset.update() is blocked by design, so backdate with raw SQL.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE risk_scoring_riskscoresnapshot SET computed_at = %s WHERE id = %s",
            [timezone.now() - timedelta(days=days_ago), snapshot.pk],
        )
    return snapshot


def test_campaign_summary_counts_distinct_employees_and_rates():
    campaign = CampaignFactory()
    a, b, c, d = (EmployeeFactory() for _ in range(4))
    for employee in (a, b, c, d):
        EventFactory(campaign=campaign, employee=employee, event_type=E.EMAIL_SENT)
    EventFactory(campaign=campaign, employee=a, event_type=E.LINK_CLICKED)
    EventFactory(campaign=campaign, employee=a, event_type=E.CREDENTIAL_ATTEMPT)
    EventFactory(campaign=campaign, employee=b, event_type=E.LINK_CLICKED)
    EventFactory(campaign=campaign, employee=c, event_type=E.PHISHING_REPORTED)
    EventFactory(campaign=campaign, employee=d, event_type=E.EMAIL_OPENED)

    summary = analytics.campaign_summary(campaign)

    assert summary["targeted"] == 4
    assert summary["failed"] == 2  # a and b, a counted once
    assert summary["failure_rate_percent"] == 50.0
    assert summary["submit_rate_percent"] == 25.0
    assert summary["report_rate_percent"] == 25.0
    assert summary["opened_diagnostic_only"] == 1


def test_campaign_summary_with_no_events_has_no_rates():
    summary = analytics.campaign_summary(CampaignFactory())

    assert summary["targeted"] == 0
    assert summary["failure_rate_percent"] is None


def test_training_compliance_is_separate_from_risk_and_counts_overdue():
    employee = EmployeeFactory()
    now = timezone.now()
    TrainingAssignmentFactory(employee=employee, completed_at=now)
    TrainingAssignmentFactory(employee=employee, due_at=now - timedelta(days=1))
    TrainingAssignmentFactory(employee=employee, due_at=now + timedelta(days=5))

    result = analytics.training_compliance(employee.training_assignments.all())

    assert result["assigned"] == 3
    assert result["completed"] == 1
    assert result["overdue"] == 1
    assert result["outstanding"] == 2
    assert result["completion_rate_percent"] == 33.3


def test_department_summary_uses_each_employees_latest_snapshot():
    department = DepartmentFactory()
    a, b = EmployeeFactory(department=department), EmployeeFactory(department=department)
    _snapshot(a, 90, days_ago=10)
    _snapshot(a, 20, days_ago=1)  # a's latest — the 90 must not count
    _snapshot(b, 70)
    EmployeeFactory(department=department)  # never scored

    summary = analytics.department_summary(department)

    assert summary["employees"] == 3
    assert summary["employees_scored"] == 2
    assert summary["average_score"] == 45.0
    assert summary["high_risk_employees"] == 1


@pytest.mark.parametrize(
    "old, new, expected",
    [(60, 40, "improving"), (40, 60, "worsening"), (40, 42, "stagnant")],
)
def test_trend_direction(old, new, expected):
    employee = EmployeeFactory()
    _snapshot(employee, old, days_ago=45)
    _snapshot(employee, new, days_ago=0)

    assert analytics.trend_direction(analytics.score_history(employee)) == expected


def test_trend_needs_a_month_of_history():
    employee = EmployeeFactory()
    _snapshot(employee, 50, days_ago=2)

    assert analytics.trend_direction(analytics.score_history(employee)) == "insufficient_data"
    assert analytics.trend_direction([]) == "insufficient_data"


def test_department_trend_carries_each_employees_last_score_forward():
    department = DepartmentFactory()
    a, b = EmployeeFactory(department=department), EmployeeFactory(department=department)
    _snapshot(a, 80, days_ago=20)
    _snapshot(b, 40, days_ago=2)

    series = analytics.department_trend(department, weeks=4)

    assert len(series) == 4
    assert series[0]["average_score"] is None  # nobody scored yet 3 weeks ago
    assert series[-1]["average_score"] == 60.0  # both scored; a's 80 carried forward
    assert series[-1]["employees_scored"] == 2
