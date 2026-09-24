from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone

from apps.employees.tests.factories import EmployeeFactory
from apps.engagement.deliverability import check_domain
from apps.engagement.points import leaderboard
from apps.engagement.retention import anonymize_stale_employees
from apps.engagement.tasks import export_event_to_siem, send_coaching_email
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring import analytics
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db


# --- coaching ----------------------------------------------------------------------------


def test_coaching_email_sent_once_per_day(settings):
    settings.COACHING_ENABLED = True
    event = EventFactory(event_type="link_clicked")
    later = EventFactory(employee=event.employee, event_type="credential_attempt")

    send_coaching_email(event.pk)
    send_coaching_email(later.pk)  # within 24h → suppressed

    assert len(mail.outbox) == 1
    assert "simulated phishing test" in mail.outbox[0].body
    assert "/portal/learn" in mail.outbox[0].body


def test_coaching_skips_a_deactivated_employee():
    event = EventFactory(event_type="link_clicked")
    event.employee.is_active = False
    event.employee.save()

    send_coaching_email(event.pk)

    assert mail.outbox == []


def test_coaching_fires_on_failure_when_enabled(settings, django_capture_on_commit_callbacks):
    settings.COACHING_ENABLED = True
    with patch("apps.engagement.signals.send_coaching_email.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            EventFactory(event_type="credential_attempt")
    delay.assert_called_once()


def test_no_coaching_when_disabled(settings, django_capture_on_commit_callbacks):
    settings.COACHING_ENABLED = False
    with patch("apps.engagement.signals.send_coaching_email.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            EventFactory(event_type="link_clicked")
    delay.assert_not_called()


# --- SIEM export -------------------------------------------------------------------------


def test_siem_export_posts_event_json_without_passwords(settings):
    settings.SIEM_WEBHOOK_URL = "https://siem.example/ingest"
    event = EventFactory(event_type="credential_attempt")

    with patch("apps.engagement.tasks.requests.post") as post:
        export_event_to_siem(event.pk)

    body = post.call_args.kwargs["json"]
    assert body["event_type"] == "credential_attempt" and body["employee_email"] == event.employee.email
    assert "password" not in str(body).lower()


def test_siem_export_is_a_noop_without_a_url(settings):
    settings.SIEM_WEBHOOK_URL = ""
    event = EventFactory()
    with patch("apps.engagement.tasks.requests.post") as post:
        export_event_to_siem(event.pk)
    post.assert_not_called()


# --- points ------------------------------------------------------------------------------


def test_points_reward_reporting_and_training_not_failure():
    e = EmployeeFactory()
    EventFactory(employee=e, event_type="phishing_reported")
    EventFactory(employee=e, event_type="credential_attempt")  # failing costs no points
    TrainingAssignmentFactory(employee=e, completed_at=timezone.now())

    from apps.employees.models import Employee

    (row,) = leaderboard(Employee.objects.filter(pk=e.pk))
    assert (row["reports"], row["training_completed"], row["points"]) == (1, 1, 15)


def test_leaderboard_ranks_reporters_and_hides_zero_scores():
    from apps.employees.models import Employee

    a, b, _c = EmployeeFactory(), EmployeeFactory(), EmployeeFactory()
    EventFactory(employee=a, event_type="phishing_reported")
    EventFactory(employee=b, event_type="phishing_reported")
    EventFactory(employee=b, event_type="phishing_reported")

    board = leaderboard(Employee.objects.all())
    assert [r["employee"].pk for r in board] == [b.pk, a.pk]  # _c (0 points) excluded


# --- min cohort --------------------------------------------------------------------------


def test_small_departments_are_suppressed_in_aggregate(settings):
    from apps.employees.models import Employee

    settings.MIN_REPORTING_COHORT = 5
    for _ in range(3):
        EmployeeFactory()

    summary = analytics.scope_summary(Employee.objects.all())
    assert summary["suppressed"] and summary["average_score"] is None


# --- retention ---------------------------------------------------------------------------


def test_anonymise_scrubs_long_deactivated_employees(settings):
    from apps.employees.models import Employee

    settings.PII_RETENTION_DAYS = 30
    keep = EmployeeFactory(is_active=True)
    old = EmployeeFactory(is_active=False, full_name="Jo Real", email="jo@corp.example")
    Employee.objects.filter(pk=old.pk).update(deactivated_at=timezone.now() - timedelta(days=60))
    event = EventFactory(employee=old)  # history must survive

    assert anonymize_stale_employees() == 1
    old.refresh_from_db()
    assert old.full_name == "Former employee" and old.email.endswith("@example.invalid")
    assert Event.objects.filter(pk=event.pk).exists()
    keep.refresh_from_db()
    assert keep.full_name == "Jo Real" or keep.full_name != "Former employee"


def test_retention_disabled_by_default(settings):
    settings.PII_RETENTION_DAYS = 0
    assert anonymize_stale_employees() == 0


# --- deliverability ----------------------------------------------------------------------


def test_deliverability_reports_spf_and_dmarc():
    def fake(name, rdtype):
        return {
            "mail.example.com": ["v=spf1 include:_spf.example.com -all"],
            "_dmarc.mail.example.com": ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"],
        }.get(name, [])

    result = check_domain("mail.example.com", resolver=fake)

    assert result["checks"]["spf"]["ok"] and result["checks"]["dmarc"]["ok"]
    assert result["checks"]["dmarc"]["policy"] == "reject"
    assert result["checks"]["ready"]


def test_deliverability_flags_a_missing_dmarc():
    def fake(name, rdtype):
        return ["v=spf1 -all"] if name == "x.example" else []

    result = check_domain("x.example", resolver=fake)

    assert result["checks"]["spf"]["ok"]
    assert not result["checks"]["dmarc"]["ok"] and not result["checks"]["ready"]
