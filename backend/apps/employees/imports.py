"""
Employee CSV import. Idempotent: keyed on email, it creates new people and
updates existing ones, and (optionally) deactivates anyone not in the file —
offboarding never deletes, because their event history is immutable
(CLAUDE.md invariant #3). Pure function over already-parsed rows, so it's
testable without an HTTP upload.
"""

import csv
import io
from dataclasses import dataclass, field

from django.db import transaction

from .models import Department, Employee

REQUIRED_COLUMNS = {"email", "full_name"}


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    reactivated: int = 0
    errors: list = field(default_factory=list)  # (row_number, message)

    @property
    def ok(self):
        return not self.errors


def parse_csv(text: str) -> tuple[list[dict], list]:
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - headers
    if missing:
        return [], [(0, f"Missing required column(s): {', '.join(sorted(missing))}.")]
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    return rows, []


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "x"}


@transaction.atomic
def import_employees(rows: list[dict], *, deactivate_missing: bool = False) -> ImportResult:
    result = ImportResult()
    seen_emails = set()
    departments = {d.name.lower(): d for d in Department.objects.all()}

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        email = row.get("email", "").strip()
        full_name = row.get("full_name", "").strip()
        if not email or not full_name:
            result.errors.append((i, "email and full_name are required."))
            continue

        department = None
        dept_name = row.get("department", "").strip()
        if dept_name:
            department = departments.get(dept_name.lower())
            if department is None:
                department = Department.objects.create(name=dept_name)
                departments[dept_name.lower()] = department

        defaults = {"full_name": full_name, "department": department, "is_active": True}
        if "is_exempt" in row:
            defaults["is_exempt"] = _truthy(row.get("is_exempt", ""))

        seen_emails.add(email.lower())
        employee = Employee.objects.filter(email__iexact=email).first()
        if employee is None:
            Employee.objects.create(email=email, **defaults)
            result.created += 1
        else:
            was_inactive = not employee.is_active
            for k, v in defaults.items():
                setattr(employee, k, v)
            employee.save()
            result.updated += 1
            if was_inactive:
                result.reactivated += 1

    if deactivate_missing:
        stale = Employee.objects.filter(is_active=True).exclude(email__in=list(seen_emails))
        # exclude is case-sensitive; re-check case-insensitively
        for employee in stale:
            if employee.email.lower() not in seen_emails:
                employee.is_active = False
                employee.save(update_fields=["is_active"])
                result.deactivated += 1

    return result
