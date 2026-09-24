"""
Generating and archiving reports. One entry point, `generate_report`, used by
the dashboard and the scheduler — it owns the permission check, scoping,
hashing, archiving and the audit entries (same single-path idea as
campaigns.services.launch_campaign).
"""

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.core.audit import log_action
from apps.core.scoping import (
    managed_departments,
    visible_assignments,
    visible_campaigns,
    visible_departments,
    visible_employees,
)

from apps.employees.models import Employee
from apps.training.models import TrainingAssignment

from . import evidence, exports, pdf
from .data import ReportData, ReportScope
from .models import GeneratedReport

Kind = GeneratedReport.Kind
MAX_PERIOD_DAYS = 366 * 3


class ReportError(Exception):
    """A report can't be generated as requested (bad period, missing permission)."""


@dataclass(frozen=True)
class KindSpec:
    key: str
    label: str
    description: str
    file_format: str
    content_type: str
    employee_level: bool  # names individual employees -> needs export_employee_level
    build: Callable  # (data, ctx) -> (bytes, row_count)


CSV, PDF, ZIP = "text/csv; charset=utf-8", "application/pdf", "application/zip"

KINDS = {
    spec.key: spec
    for spec in [
        KindSpec(Kind.EXECUTIVE_SUMMARY, "Executive summary", "One-page PDF: headline numbers, trend, campaigns and departments.",
                 "pdf", PDF, False, lambda data, ctx: (pdf.render_executive_summary(data), len(data.department_rows))),
        KindSpec(Kind.CAMPAIGN_RESULTS, "Campaign results", "Every simulation launched in the period with click, submit and report rates.",
                 "csv", CSV, False, lambda data, ctx: exports.campaigns_csv(data)),
        KindSpec(Kind.DEPARTMENT_SUMMARY, "Department summary", "Average risk and training completion by department.",
                 "csv", CSV, False, lambda data, ctx: exports.departments_csv(data)),
        KindSpec(Kind.TRAINING_COMPLIANCE, "Training compliance", "Every training assignment, its due date and status on the report date.",
                 "csv", CSV, True, lambda data, ctx: exports.training_csv(data)),
        KindSpec(Kind.RISK_SCORES, "Employee risk scores", "Each employee's risk score on the report date.",
                 "csv", CSV, True, lambda data, ctx: exports.risk_scores_csv(data)),
        KindSpec(Kind.EVIDENCE_PACKAGE, "Compliance evidence package",
                 "ZIP for auditors: summary PDF, results, training, audit log, methodology and a SHA-256 manifest.",
                 "zip", ZIP, True, lambda data, ctx: evidence.build_evidence_package(data, ctx["by"], ctx["at"])),
    ]
}


def can_generate(user, spec: KindSpec) -> bool:
    if user is None:  # the scheduler
        return not spec.employee_level
    if not user.has_perm("reporting.generate_report"):
        return False
    return not spec.employee_level or user.has_perm("reporting.export_employee_level")


def allowed_kinds(user) -> list[KindSpec]:
    return [spec for spec in KINDS.values() if can_generate(user, spec)]


def visible_reports(user):
    """Archived reports this user may list and download."""
    reports = GeneratedReport.objects.without_content()
    if managed_departments(user) is not None:  # row-scoped users only ever see their own
        return reports.filter(generated_by=user)
    # Org-wide roles may see scoped reports too (they're subsets of what these roles can already see),
    # but reports that name individuals need the export permission.
    if not user.has_perm("reporting.export_employee_level"):
        reports = reports.filter(contains_employee_data=False)
    return reports


def _scope_for(user) -> ReportScope:
    if user is None:
        return ReportScope(
            employees=Employee.objects.all(), campaigns=Campaign.objects.all(), assignments=TrainingAssignment.objects.all(),
            departments=None, label="All departments", is_scoped=False,
        )
    managed = managed_departments(user)
    if managed is None:
        label, scoped = "All departments", False
    else:
        names = ", ".join(sorted(managed.values_list("name", flat=True))) or "no departments"
        label, scoped = f"Departments: {names}", True
    return ReportScope(
        employees=visible_employees(user), campaigns=visible_campaigns(user), assignments=visible_assignments(user),
        departments=visible_departments(user), label=label, is_scoped=scoped,
    )


def validate_period(period_start: date, period_end: date, today: date | None = None) -> None:
    today = today or timezone.now().date()
    if period_start > period_end:
        raise ReportError("The start date must be on or before the end date.")
    if period_end > today:
        raise ReportError("The end date can't be in the future — figures are computed from events that already happened.")
    if (period_end - period_start).days > MAX_PERIOD_DAYS:
        raise ReportError("Choose a period of three years or less.")


def generate_report(*, kind: str, user, period_start: date, period_end: date, now=None) -> GeneratedReport:
    """`user=None` means the scheduler (aggregate, org-wide reports only)."""
    spec = KINDS.get(kind)
    if spec is None:
        raise ReportError(f"Unknown report type {kind!r}.")
    if not can_generate(user, spec):
        raise ReportError("You don't have permission to generate this report.")
    validate_period(period_start, period_end, today=(now or timezone.now()).date())

    now = now or timezone.now()
    scope = _scope_for(user)
    data = ReportData(scope, period_start, period_end, now=now)
    generated_by = user.get_username() if user else "scheduler"
    content, rows = spec.build(data, {"by": generated_by, "at": now.isoformat()})

    filename = f"{spec.key}_{period_start:%Y%m%d}-{period_end:%Y%m%d}.{spec.file_format}"
    report = GeneratedReport.objects.create(
        kind=spec.key,
        file_format=spec.file_format,
        filename=filename,
        content_type=spec.content_type,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        row_count=rows,
        period_start=period_start,
        period_end=period_end,
        as_of=data.as_of,
        algorithm_version=data.algorithm_version,
        scope_label=scope.label,
        is_scoped=scope.is_scoped,
        contains_employee_data=spec.employee_level,
        generated_by=user,
    )
    log_action(
        actor=user,
        action="report_generated",
        target_description=filename,
        report_id=report.pk,
        kind=spec.key,
        sha256=report.sha256,
        scope=scope.label,
    )
    return report


def previous_month(today: date) -> tuple[date, date]:
    last_day = today.replace(day=1) - timedelta(days=1)
    return last_day.replace(day=1), last_day
