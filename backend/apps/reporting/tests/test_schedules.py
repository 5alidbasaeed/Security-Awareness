from datetime import date

import pytest
from django.core import mail

from apps.reporting.models import GeneratedReport, ReportSchedule
from apps.reporting.tasks import _due_period, send_scheduled_reports

pytestmark = pytest.mark.django_db


def test_monthly_schedule_emails_last_months_report_once():
    ReportSchedule.objects.create(name="CISO monthly", kind="executive_summary", recipients="ciso@corp.example, cfo@corp.example")

    assert send_scheduled_reports() == 1
    assert send_scheduled_reports() == 0  # already sent for this period
    message = mail.outbox[0]
    assert set(message.to) == {"ciso@corp.example", "cfo@corp.example"}
    assert message.attachments[0][0].endswith(".pdf")
    assert GeneratedReport.objects.filter(generated_by__isnull=True).count() == 1


def test_employee_level_kinds_are_never_sent_on_a_schedule():
    ReportSchedule.objects.create(name="bad", kind="risk_scores", recipients="x@corp.example")

    assert send_scheduled_reports() == 0 and mail.outbox == []


def test_weekly_period_is_the_last_full_monday_to_sunday():
    schedule = ReportSchedule(frequency=ReportSchedule.Frequency.WEEKLY)
    assert _due_period(schedule, date(2026, 9, 24)) == (date(2026, 9, 14), date(2026, 9, 20))  # a Thursday


def test_manage_form_offers_only_aggregate_kinds_and_validates_addresses():
    from apps.manage.forms import ReportScheduleForm

    form = ReportScheduleForm(data={"name": "n", "kind": "risk_scores", "frequency": "monthly", "recipients": "not-an-email"})

    assert not form.is_valid()
    assert "kind" in form.errors and "recipients" in form.errors
    assert "risk_scores" not in dict(form.fields["kind"].choices)
