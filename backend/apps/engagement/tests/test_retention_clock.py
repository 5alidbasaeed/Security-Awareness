"""
Regression tests from the Phase 6 end-to-end review. The retention job anonymises people who have been
deactivated for PII_RETENTION_DAYS — but it measured that from `created_at` (when the record was made),
so someone employed for years and offboarded yesterday would have been irreversibly anonymised on the
next run. The clock has to start when the person is deactivated.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.employees.models import Employee
from apps.employees.tests.factories import EmployeeFactory
from apps.engagement.retention import anonymize_stale_employees

pytestmark = pytest.mark.django_db


def _age(employee, *, created_days=0, deactivated_days=None):
    fields = {"created_at": timezone.now() - timedelta(days=created_days)}
    if deactivated_days is not None:
        fields["deactivated_at"] = timezone.now() - timedelta(days=deactivated_days)
    Employee.objects.filter(pk=employee.pk).update(**fields)
    employee.refresh_from_db()


def test_a_long_serving_employee_deactivated_today_is_not_anonymised(settings):
    settings.PII_RETENTION_DAYS = 30
    person = EmployeeFactory(full_name="Long Serving", email="long@corp.example")
    _age(person, created_days=1500)  # employed for years

    person.is_active = False
    person.save()

    assert anonymize_stale_employees() == 0
    person.refresh_from_db()
    assert person.full_name == "Long Serving" and person.email == "long@corp.example"


def test_someone_deactivated_longer_ago_than_the_retention_period_is_anonymised(settings):
    settings.PII_RETENTION_DAYS = 30
    person = EmployeeFactory(is_active=False, full_name="Gone Long Ago", email="gone@corp.example")
    _age(person, created_days=900, deactivated_days=45)

    assert anonymize_stale_employees() == 1
    person.refresh_from_db()
    assert person.full_name == "Former employee" and person.email.endswith("@example.invalid")


def test_someone_deactivated_recently_is_kept_even_if_the_period_is_short(settings):
    settings.PII_RETENTION_DAYS = 30
    person = EmployeeFactory(is_active=False)
    _age(person, created_days=900, deactivated_days=10)

    assert anonymize_stale_employees() == 0


def test_deactivating_stamps_the_time_and_reactivating_clears_it():
    person = EmployeeFactory()
    assert person.deactivated_at is None

    person.is_active = False
    person.save()
    assert person.deactivated_at is not None

    person.is_active = True
    person.save()
    person.refresh_from_db()
    assert person.deactivated_at is None


def test_the_importers_partial_save_also_stamps_the_time():
    person = EmployeeFactory()

    person.is_active = False
    person.save(update_fields=["is_active"])  # exactly how employees/imports.py deactivates people

    person.refresh_from_db()
    assert person.deactivated_at is not None


def test_a_rehired_person_does_not_keep_a_stale_clock(settings):
    settings.PII_RETENTION_DAYS = 30
    person = EmployeeFactory(is_active=False)
    _age(person, deactivated_days=90)

    person.is_active = True
    person.save()

    assert anonymize_stale_employees() == 0
    person.refresh_from_db()
    assert person.full_name != "Former employee"


def test_rows_deactivated_before_the_clock_existed_start_it_on_the_first_run(settings):
    """Legacy rows have no timestamp. Guessing would either anonymise them at once or never; instead the clock starts now."""
    settings.PII_RETENTION_DAYS = 30
    legacy = EmployeeFactory(is_active=False)
    Employee.objects.filter(pk=legacy.pk).update(deactivated_at=None, created_at=timezone.now() - timedelta(days=2000))

    assert anonymize_stale_employees() == 0
    legacy.refresh_from_db()
    assert legacy.deactivated_at is not None and legacy.full_name != "Former employee"


def test_already_anonymised_people_are_not_counted_again(settings):
    settings.PII_RETENTION_DAYS = 30
    person = EmployeeFactory(is_active=False)
    _age(person, deactivated_days=45)

    assert anonymize_stale_employees() == 1
    assert anonymize_stale_employees() == 0
