import csv
import hashlib
import io
import json
import zipfile
from datetime import date

import pytest

from apps.campaigns.tests.factories import CampaignFactory
from apps.core.audit import log_action
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.reporting import evidence, exports, pdf
from apps.reporting.tests.test_data import old_employee, report, utc

pytestmark = pytest.mark.django_db


def rows_of(content: bytes):
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))


def test_csv_neutralises_formula_injection_and_has_a_bom_for_excel():
    old_employee(full_name='=HYPERLINK("http://evil.example","x")', email="f@example.com")
    data = report(date(2026, 3, 1), date(2026, 3, 31))

    content, _ = exports.risk_scores_csv(data)

    assert content.startswith(b"\xef\xbb\xbf")
    assert not rows_of(content)[1][0].startswith("=")


def test_csv_keeps_non_latin_names_intact():
    old_employee(full_name="Zoë Łukasiewicz — 李雷", email="z@example.com")

    content, _ = exports.risk_scores_csv(report(date(2026, 3, 1), date(2026, 3, 31)))

    assert "李雷" in rows_of(content)[1][0]


def test_csv_columns_and_counts():
    old_employee(), old_employee()
    content, count = exports.risk_scores_csv(report(date(2026, 3, 1), date(2026, 3, 31)))

    assert rows_of(content)[0][:3] == ["Employee", "Email", "Department"]
    assert count == 2 and len(rows_of(content)) == 3


def test_pdf_is_a_valid_deterministic_document():
    department = DepartmentFactory(name="R&D <Labs>")  # must be escaped, not parsed as markup
    person = old_employee(department=department)
    EventFactory(employee=person, event_type=Event.EventType.LINK_CLICKED, occurred_at=utc(2026, 3, 20))
    data = report(date(2026, 3, 1), date(2026, 3, 31))

    first, second = pdf.render_executive_summary(data), pdf.render_executive_summary(data)

    assert first.startswith(b"%PDF") and len(first) > 2000
    assert first == second  # same inputs -> same bytes -> same SHA-256


def test_pdf_renders_with_no_data_at_all():
    assert pdf.render_executive_summary(report(date(2026, 3, 1), date(2026, 3, 31))).startswith(b"%PDF")


def test_evidence_package_contents_and_integrity():
    person = old_employee()
    campaign = CampaignFactory()
    EventFactory(employee=person, campaign=campaign, event_type=Event.EventType.LINK_CLICKED, occurred_at=utc(2026, 3, 20))
    log_action(actor=None, action="campaign_launched", target_description="x")
    data = report(date(2026, 3, 1), date(2026, 9, 30))

    content, _ = evidence.build_evidence_package(data, "admin", "2026-09-30T00:00:00+00:00")
    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())

    assert names == {
        "README.txt", "manifest.json", "SHA256SUMS.txt", "methodology.md", "executive_summary.pdf", "campaigns.csv",
        "departments.csv", "employee_risk_scores.csv", "training_assignments.csv", "audit_log.csv",
    }
    # Every hash in the manifest and in SHA256SUMS.txt matches the actual bytes.
    manifest = json.loads(archive.read("manifest.json"))
    for entry in manifest["files"]:
        assert hashlib.sha256(archive.read(entry["name"])).hexdigest() == entry["sha256"]
    for line in archive.read("SHA256SUMS.txt").decode().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    assert manifest["algorithm_version"] == "v1" and manifest["generated_by"] == "admin"
    assert "Passwords are never captured" in archive.read("methodology.md").decode()


def test_evidence_package_is_deterministic_for_the_same_inputs():
    old_employee()
    data = report(date(2026, 3, 1), date(2026, 3, 31))

    a, _ = evidence.build_evidence_package(data, "admin", "2026-09-30T00:00:00+00:00")
    b, _ = evidence.build_evidence_package(data, "admin", "2026-09-30T00:00:00+00:00")

    assert a == b


def test_evidence_audit_log_only_covers_the_period_and_names_the_actor(django_user_model):
    user = django_user_model.objects.create_user("alice", password="x")
    inside = log_action(actor=user, action="campaign_approved", target_description="in period")
    from apps.core.models import AuditLogEntry
    from django.db import connection

    with connection.cursor() as cursor:  # audit rows are auto_now_add; backdate one out of the period
        old = log_action(actor=user, action="campaign_launched", target_description="out of period")
        cursor.execute("UPDATE core_auditlogentry SET occurred_at = %s WHERE id = %s", [utc(2020, 1, 1), old.pk])
        cursor.execute("UPDATE core_auditlogentry SET occurred_at = %s WHERE id = %s", [utc(2026, 3, 5), inside.pk])
    assert AuditLogEntry.objects.count() == 2

    content, count = evidence._audit_log_csv(report(date(2026, 3, 1), date(2026, 3, 31)))

    assert count == 1
    assert rows_of(content)[1][1:3] == ["alice", "campaign_approved"]
