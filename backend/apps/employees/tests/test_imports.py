import pytest

from apps.employees.imports import import_employees, parse_csv
from apps.employees.models import Department, Employee
from apps.employees.tests.factories import EmployeeFactory

pytestmark = pytest.mark.django_db

CSV = """email,full_name,department,is_exempt,exempt_reason
alice@corp.example,Alice Ng,Finance,false,
bob@corp.example,Bob Ray,Finance,true,Works in the SOC
"""


def test_parse_rejects_a_file_missing_required_columns():
    rows, errors = parse_csv("name,team\nx,y\n")
    assert rows == [] and "email" in errors[0][1]


def test_import_creates_people_and_departments():
    rows, _ = parse_csv(CSV)
    result = import_employees(rows)

    assert result.created == 2 and result.ok
    assert Department.objects.filter(name="Finance").exists()
    assert Employee.objects.get(email="bob@corp.example").is_exempt is True


def test_reimport_updates_and_is_idempotent():
    rows, _ = parse_csv(CSV)
    import_employees(rows)
    result = import_employees(rows)

    assert result.created == 0 and result.updated == 2
    assert Employee.objects.count() == 2


def test_deactivate_missing_offboards_without_deleting():
    keep = EmployeeFactory(email="alice@corp.example", is_active=True)
    gone = EmployeeFactory(email="old@corp.example", is_active=True)
    rows, _ = parse_csv("email,full_name\nalice@corp.example,Alice\n")

    result = import_employees(rows, deactivate_missing=True)

    keep.refresh_from_db()
    gone.refresh_from_db()
    assert keep.is_active is True
    assert gone.is_active is False and Employee.objects.filter(pk=gone.pk).exists()  # deactivated, not deleted
    assert result.deactivated == 1


def test_a_row_missing_email_is_reported_not_fatal():
    rows, _ = parse_csv("email,full_name\n,No Email\ncarol@corp.example,Carol\n")
    result = import_employees(rows)

    assert result.created == 1 and len(result.errors) == 1
