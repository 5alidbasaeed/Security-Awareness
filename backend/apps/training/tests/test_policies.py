from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.training.models import TrainingAssignment, TrainingPolicy
from apps.training.tasks import enforce_training_policies, escalate_overdue_training
from apps.training.tests.factories import TrainingAssignmentFactory, TrainingModuleFactory

pytestmark = pytest.mark.django_db


def test_policy_enrols_everyone_in_scope_once():
    module = TrainingModuleFactory()
    sales, ops = DepartmentFactory(), DepartmentFactory()
    a, b = EmployeeFactory(department=sales), EmployeeFactory(department=sales)
    EmployeeFactory(department=ops)
    EmployeeFactory(department=sales, is_active=False)
    TrainingPolicy.objects.create(name="Sales annual", module=module, department=sales, due_days=10)

    assert enforce_training_policies() == 2
    assert enforce_training_policies() == 0  # idempotent
    assert set(TrainingAssignment.objects.values_list("employee_id", flat=True)) == {a.pk, b.pk}
    assert TrainingAssignment.objects.first().due_at.date() == (timezone.now() + timedelta(days=10)).date()


def test_policy_re_enrols_after_the_repeat_period():
    module = TrainingModuleFactory()
    employee = EmployeeFactory()
    TrainingPolicy.objects.create(name="Annual", module=module, repeat_every_days=365)
    old = TrainingAssignmentFactory(employee=employee, module=module)
    TrainingAssignment.objects.filter(pk=old.pk).update(
        assigned_at=timezone.now() - timedelta(days=400), completed_at=timezone.now() - timedelta(days=390))

    assert enforce_training_policies() == 1


def test_inactive_policy_or_module_enrols_nobody():
    EmployeeFactory()
    TrainingPolicy.objects.create(name="Off", module=TrainingModuleFactory(), is_active=False)
    TrainingPolicy.objects.create(name="Dead module", module=TrainingModuleFactory(is_active=False))

    assert enforce_training_policies() == 0


def test_managers_are_told_about_people_overdue_by_over_a_week():
    department = DepartmentFactory()
    manager = User.objects.create_user("mgr", email="mgr@corp.example")
    department.managers.add(manager)
    late = TrainingAssignmentFactory(employee=EmployeeFactory(department=department))
    TrainingAssignment.objects.filter(pk=late.pk).update(due_at=timezone.now() - timedelta(days=10))
    slightly = TrainingAssignmentFactory(employee=EmployeeFactory(department=department))
    TrainingAssignment.objects.filter(pk=slightly.pk).update(due_at=timezone.now() - timedelta(days=2))

    assert escalate_overdue_training() == 1
    assert mail.outbox[0].to == ["mgr@corp.example"]
    assert late.employee.full_name in mail.outbox[0].body and slightly.employee.full_name not in mail.outbox[0].body

    assert escalate_overdue_training() == 0  # cooldown: not again within 7 days
