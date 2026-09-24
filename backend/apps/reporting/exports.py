"""
CSV rendering. Every cell goes through csv_safe (spreadsheet formula
injection — found in the Phase 0-4 review) and the file is UTF-8 with a BOM so
Excel opens non-Latin names correctly.
"""

import csv
import io

from apps.core.csv_safe import csv_safe

from .data import ReportData


def to_csv(columns: list[tuple[str, str]], rows: list[dict]) -> bytes:
    """columns = [(dict key, header label), ...]"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([csv_safe("" if row.get(key) is None else row.get(key)) for key, _ in columns])
    return buffer.getvalue().encode("utf-8-sig")


RISK_COLUMNS = [
    ("employee", "Employee"), ("email", "Email"), ("department", "Department"), ("exempt", "Exempt from simulations"),
    ("score", "Risk score (0-100)"), ("level", "Risk level"), ("campaigns_failed", "Campaigns failed"),
    ("repeat_failures", "Repeat failures"), ("campaigns_reported", "Campaigns reported"),
]
CAMPAIGN_COLUMNS = [
    ("campaign", "Campaign"), ("department", "Department"), ("launched", "Launched"), ("targeted", "Employees targeted"),
    ("failed", "Failed (clicked or submitted)"), ("clicked", "Clicked link"), ("submitted_data", "Submitted data"), ("reported", "Reported"),
    ("failure_rate_percent", "Failure rate %"), ("submit_rate_percent", "Data-submission rate %"),
    ("report_rate_percent", "Report rate %"),
]
DEPARTMENT_COLUMNS = [
    ("department", "Department"), ("employees", "Employees"), ("average_score", "Average risk score"),
    ("high_risk_employees", "High-risk employees"), ("medium_risk_employees", "Medium-risk employees"),
    ("training_assigned", "Training assigned"), ("training_completed", "Training completed"),
    ("training_overdue", "Training overdue"), ("training_completion_percent", "Training completion %"),
]
TRAINING_COLUMNS = [
    ("employee", "Employee"), ("email", "Email"), ("department", "Department"), ("module", "Module"),
    ("assigned", "Assigned"), ("due", "Due"), ("completed", "Completed"), ("status", "Status (as of report date)"),
]


def risk_scores_csv(data: ReportData) -> tuple[bytes, int]:
    return to_csv(RISK_COLUMNS, data.risk_rows), len(data.risk_rows)


def campaigns_csv(data: ReportData) -> tuple[bytes, int]:
    return to_csv(CAMPAIGN_COLUMNS, data.campaign_rows), len(data.campaign_rows)


def departments_csv(data: ReportData) -> tuple[bytes, int]:
    return to_csv(DEPARTMENT_COLUMNS, data.department_rows), len(data.department_rows)


def training_csv(data: ReportData) -> tuple[bytes, int]:
    return to_csv(TRAINING_COLUMNS, data.training_rows), len(data.training_rows)
