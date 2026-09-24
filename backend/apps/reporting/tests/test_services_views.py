from datetime import date, timedelta

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.reporting import services
from apps.reporting.models import GeneratedReport
from apps.reporting.tasks import generate_monthly_executive_summary

pytestmark = pytest.mark.django_db
Kind = GeneratedReport.Kind
START, END = date(2026, 3, 1), date(2026, 3, 31)
END_NOW = timezone.now().date()  # employees created in a test exist "now", so people-reports need a recent period
START_NOW = END_NOW - timedelta(days=30)


def make_user(django_user_model, group, username="u"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return django_user_model.objects.get(pk=user.pk)


# --- permissions -------------------------------------------------------------


@pytest.mark.parametrize(
    "group, allowed",
    [
        ("Security Admin", {"executive_summary", "campaign_results", "department_summary", "training_compliance", "risk_scores", "evidence_package"}),
        ("Report Viewer", {"executive_summary", "campaign_results", "department_summary"}),
        ("Campaign Manager", {"executive_summary", "campaign_results", "department_summary"}),
        ("Department Manager", {"executive_summary", "campaign_results", "department_summary"}),
        ("Training Manager", set()),
    ],
)
def test_report_kinds_available_per_role(django_user_model, group, allowed):
    user = make_user(django_user_model, group)

    assert {spec.key for spec in services.allowed_kinds(user)} == allowed


def test_a_role_cannot_generate_a_kind_it_is_not_allowed(django_user_model):
    user = make_user(django_user_model, "Report Viewer")

    with pytest.raises(services.ReportError, match="permission"):
        services.generate_report(kind=Kind.RISK_SCORES, user=user, period_start=START, period_end=END)


def test_scheduler_can_only_make_aggregate_reports():
    with pytest.raises(services.ReportError):
        services.generate_report(kind=Kind.EVIDENCE_PACKAGE, user=None, period_start=START, period_end=END)


@pytest.mark.parametrize(
    "start, end, message",
    [
        (date(2026, 4, 1), date(2026, 3, 1), "on or before"),
        (date(2026, 3, 1), timezone.now().date() + timedelta(days=2), "future"),
        (date(2010, 1, 1), date(2026, 3, 1), "three years"),
    ],
)
def test_period_validation(django_user_model, start, end, message):
    user = make_user(django_user_model, "Security Admin")

    with pytest.raises(services.ReportError, match=message):
        services.generate_report(kind=Kind.EXECUTIVE_SUMMARY, user=user, period_start=start, period_end=end)


# --- generation, archive, audit ---------------------------------------------


def test_generating_archives_hashes_and_audits(django_user_model):
    user = make_user(django_user_model, "Security Admin")
    EmployeeFactory()

    report = services.generate_report(kind=Kind.RISK_SCORES, user=user, period_start=START_NOW, period_end=END_NOW)

    import hashlib

    assert report.sha256 == hashlib.sha256(bytes(report.content)).hexdigest()
    assert report.size_bytes == len(bytes(report.content)) and report.row_count == 1
    assert report.contains_employee_data and not report.is_scoped
    entry = AuditLogEntry.objects.get(action="report_generated")
    assert entry.actor == user and entry.metadata["sha256"] == report.sha256


def test_reports_are_immutable(django_user_model):
    user = make_user(django_user_model, "Security Admin")
    report = services.generate_report(kind=Kind.EXECUTIVE_SUMMARY, user=user, period_start=START, period_end=END)

    with pytest.raises(TypeError):
        report.save()
    with pytest.raises(TypeError):
        GeneratedReport.objects.all().update(filename="x")
    with pytest.raises(TypeError):
        GeneratedReport.objects.all().delete()


def test_regenerating_the_same_period_gives_identical_content(django_user_model):
    user = make_user(django_user_model, "Security Admin")
    EmployeeFactory()
    first = services.generate_report(kind=Kind.EXECUTIVE_SUMMARY, user=user, period_start=START_NOW, period_end=END_NOW)
    second = services.generate_report(kind=Kind.EXECUTIVE_SUMMARY, user=user, period_start=START_NOW, period_end=END_NOW)

    assert first.pk != second.pk and first.sha256 == second.sha256


def test_department_manager_reports_only_cover_their_departments(django_user_model):
    mine, other = DepartmentFactory(name="Mine"), DepartmentFactory(name="Theirs")
    user = make_user(django_user_model, "Department Manager")
    mine.managers.add(user)
    EmployeeFactory(department=mine), EmployeeFactory(department=other), EmployeeFactory(department=other)

    report = services.generate_report(kind=Kind.DEPARTMENT_SUMMARY, user=user, period_start=START_NOW, period_end=END_NOW)
    text = bytes(report.content).decode("utf-8-sig")

    assert report.is_scoped and report.scope_label == "Departments: Mine"
    assert "Mine" in text and "Theirs" not in text and report.row_count == 1


def test_report_visibility_rules(django_user_model):
    admin = make_user(django_user_model, "Security Admin", "admin1")
    viewer = make_user(django_user_model, "Report Viewer", "viewer1")
    manager = make_user(django_user_model, "Department Manager", "manager1")
    DepartmentFactory().managers.add(manager)
    org_aggregate = services.generate_report(kind=Kind.DEPARTMENT_SUMMARY, user=admin, period_start=START, period_end=END)
    org_people = services.generate_report(kind=Kind.RISK_SCORES, user=admin, period_start=START, period_end=END)
    manager_report = services.generate_report(kind=Kind.DEPARTMENT_SUMMARY, user=manager, period_start=START, period_end=END)

    assert set(services.visible_reports(admin)) == {org_aggregate, org_people, manager_report}
    assert set(services.visible_reports(viewer)) == {org_aggregate, manager_report}  # aggregates only, never people-level
    assert set(services.visible_reports(manager)) == {manager_report}  # never the org-wide reports


# --- scheduler ---------------------------------------------------------------


def test_monthly_summary_is_created_once(monkeypatch):
    first = generate_monthly_executive_summary()
    second = generate_monthly_executive_summary()

    assert first is not None and second is None
    report = GeneratedReport.objects.get()
    assert report.generated_by is None and report.kind == Kind.EXECUTIVE_SUMMARY
    assert report.period_start.day == 1


def test_previous_month_helper():
    assert services.previous_month(date(2026, 3, 15)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert services.previous_month(date(2026, 1, 2)) == (date(2025, 12, 1), date(2025, 12, 31))


# --- views -------------------------------------------------------------------


def login(client, user):
    client.force_login(user)


def test_anonymous_is_sent_to_login(client):
    assert client.get(reverse("reporting:index")).status_code == 302


def test_roles_without_report_permission_get_403(client, django_user_model):
    login(client, make_user(django_user_model, "Training Manager"))

    assert client.get(reverse("reporting:index")).status_code == 403


def test_reports_page_and_nav_link(client, django_user_model):
    login(client, make_user(django_user_model, "Report Viewer"))

    body = client.get(reverse("reporting:index")).content.decode()

    assert "Generate a report" in body and 'href="/reports/"' in body
    assert "Compliance evidence package" not in body  # not offered to this role


def test_post_generates_then_redirects_and_lists_the_report(client, django_user_model):
    login(client, make_user(django_user_model, "Security Admin"))

    response = client.post(
        reverse("reporting:index"), {"kind": "executive_summary", "period_start": "2026-03-01", "period_end": "2026-03-31"}
    )

    assert response.status_code == 302
    assert "executive_summary_20260301-20260331.pdf" in client.get(reverse("reporting:index")).content.decode()


def test_invalid_form_shows_errors_and_creates_nothing(client, django_user_model):
    login(client, make_user(django_user_model, "Security Admin"))

    response = client.post(
        reverse("reporting:index"), {"kind": "executive_summary", "period_start": "2026-04-01", "period_end": "2026-03-01"}
    )

    assert response.status_code == 200 and b"on or before" in response.content
    assert GeneratedReport.objects.count() == 0


def test_a_kind_the_role_cannot_use_is_rejected_by_the_form(client, django_user_model):
    login(client, make_user(django_user_model, "Report Viewer"))

    response = client.post(
        reverse("reporting:index"), {"kind": "risk_scores", "period_start": "2026-03-01", "period_end": "2026-03-31"}
    )

    assert response.status_code == 200 and GeneratedReport.objects.count() == 0


def test_download_streams_the_file_and_is_audited(client, django_user_model):
    user = make_user(django_user_model, "Security Admin")
    EmployeeFactory()
    report = services.generate_report(kind=Kind.RISK_SCORES, user=user, period_start=START_NOW, period_end=END_NOW)
    login(client, user)

    response = client.get(reverse("reporting:download", args=[report.pk]))

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"] == f'attachment; filename="{report.filename}"'
    assert response["X-Report-SHA256"] == report.sha256 and response["X-Content-Type-Options"] == "nosniff"
    assert response.content == bytes(report.content)
    assert AuditLogEntry.objects.filter(action="report_downloaded", actor=user).exists()


def test_download_respects_visibility(client, django_user_model):
    admin = make_user(django_user_model, "Security Admin", "admin1")
    viewer = make_user(django_user_model, "Report Viewer", "viewer1")
    people = services.generate_report(kind=Kind.RISK_SCORES, user=admin, period_start=START, period_end=END)
    login(client, viewer)

    assert client.get(reverse("reporting:download", args=[people.pk])).status_code == 404
    assert not AuditLogEntry.objects.filter(action="report_downloaded").exists()


def test_reporting_pages_carry_the_strict_csp(client, django_user_model):
    login(client, make_user(django_user_model, "Security Admin"))

    assert "script-src 'self'" in client.get(reverse("reporting:index"))["Content-Security-Policy"]


def test_reporting_templates_have_no_inline_styles_or_scripts():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "templates"
    for path in root.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert " style=" not in text and "onclick=" not in text and "<script>" not in text, path.name


def test_full_evidence_package_through_the_service(django_user_model):
    user = make_user(django_user_model, "Security Admin")
    CampaignFactory()

    report = services.generate_report(kind=Kind.EVIDENCE_PACKAGE, user=user, period_start=START, period_end=END)

    assert report.file_format == "zip" and report.content_type == "application/zip"
    assert bytes(report.content)[:2] == b"PK"


def test_scope_label_for_a_manager_of_many_departments_fits_the_column(django_user_model):
    user = make_user(django_user_model, "Department Manager")
    for i in range(30):
        DepartmentFactory(name=f"A department with a fairly long name {i}").managers.add(user)

    report = services.generate_report(kind=Kind.DEPARTMENT_SUMMARY, user=user, period_start=START_NOW, period_end=END_NOW)

    assert report.is_scoped and len(report.scope_label) <= 200 and report.scope_label.endswith("…")
