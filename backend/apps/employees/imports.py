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
    departments_created: list = field(default_factory=list)

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


class ImportRefused(Exception):
    """The whole file was rolled back; the message is safe to show."""


def _max_deactivations(active_count: int) -> int:
    from django.conf import settings

    percent = getattr(settings, "IMPORT_MAX_DEACTIVATE_PERCENT", 10)
    return max(1, active_count * percent // 100)


@transaction.atomic
def import_employees(rows: list[dict], *, deactivate_missing: bool = False, actor=None) -> ImportResult:
    """
    Every exemption or deactivation the file causes is audited per person (via="import"), exactly like
    the same change made by hand. Raises ImportRefused, rolling everything back, if "deactivate anyone
    not in the file" would offboard more than IMPORT_MAX_DEACTIVATE_PERCENT of active staff: a partial
    export or the wrong file must never be able to switch off most of the organisation in one click.
    """
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    from django.utils import timezone

    from apps.core.audit import log_action

    result = ImportResult()
    seen_emails = set()
    departments = {d.name.lower(): d for d in Department.objects.all()}
    active_before = Employee.objects.filter(is_active=True).count()

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        email = row.get("email", "").strip()
        full_name = row.get("full_name", "").strip()
        if email:
            # Counted as "in the file" even if the row is rejected below: a typo in someone's row must
            # never be the reason they get offboarded.
            seen_emails.add(email.lower())
        if not email or not full_name:
            result.errors.append((i, "email and full_name are required."))
            continue
        try:
            validate_email(email)
        except ValidationError:
            result.errors.append((i, f"“{email[:80]}” is not a valid email address."))
            continue

        department = None
        dept_name = row.get("department", "").strip()
        if dept_name:
            department = departments.get(dept_name.lower())
            if department is None:
                department = Department.objects.create(name=dept_name)
                departments[dept_name.lower()] = department
                result.departments_created.append(dept_name)

        defaults = {"full_name": full_name, "department": department, "is_active": True}
        employee = Employee.objects.filter(email__iexact=email).first()
        exempt_change = None
        if "is_exempt" in row and row.get("is_exempt", "").strip() != "":
            wants_exempt = _truthy(row["is_exempt"])
            currently = bool(employee and employee.is_exempt)
            if wants_exempt and not currently:
                reason = row.get("exempt_reason", "").strip()
                if not reason:
                    result.errors.append((i, "is_exempt needs an exempt_reason: an exemption without a reason can't be audited."))
                    continue
                defaults.update(is_exempt=True, exempt_reason=reason[:300], exempt_set_by=actor)
                exempt_change = "exemption_set"
            elif not wants_exempt and currently:
                defaults["is_exempt"] = False
                exempt_change = "exemption_ended"

        if employee is None:
            employee = Employee.objects.create(email=email, **defaults)
            result.created += 1
        else:
            was_inactive = not employee.is_active
            for k, v in defaults.items():
                setattr(employee, k, v)
            employee.save()
            result.updated += 1
            if was_inactive:
                result.reactivated += 1
        if exempt_change:
            log_action(actor=actor, action=exempt_change, target_description=str(employee),
                       reason=defaults.get("exempt_reason", ""), via="import")

    if deactivate_missing:
        stale = [e for e in Employee.objects.filter(is_active=True) if e.email.lower() not in seen_emails]
        limit = _max_deactivations(active_before)
        if len(stale) > limit:
            raise ImportRefused(
                f"Nothing was imported: this file would deactivate {len(stale)} of {active_before} active employees "
                f"(the limit is {limit}). Check it is the complete staff list, or deactivate people individually."
            )
        now = timezone.now()
        for employee in stale:
            employee.is_active = False
            employee.deactivated_at = now
            employee.save(update_fields=["is_active", "deactivated_at"])
            log_action(actor=actor, action="employee_deactivated", target_description=str(employee), via="import")
            result.deactivated += 1

    return result
