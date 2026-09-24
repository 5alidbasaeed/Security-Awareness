import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.admin import EmployeeAdmin
from apps.employees.models import Employee
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory

pytestmark = pytest.mark.django_db


def test_department_manager_only_sees_their_own_department(rf: RequestFactory, django_user_model):
    SetupGroupsCommand().handle()
    group = Group.objects.get(name="Department Manager")
    user = django_user_model.objects.create_user(username="dept_mgr", password="x")
    user.groups.add(group)
    user = django_user_model.objects.get(pk=user.pk)

    own_department = DepartmentFactory()
    own_department.managers.add(user)
    other_department = DepartmentFactory()

    own_employee = EmployeeFactory(department=own_department)
    EmployeeFactory(department=other_department)  # must not appear

    request = rf.get("/admin/employees/employee/")
    request.user = user

    employee_admin = EmployeeAdmin(Employee, admin.site)
    visible = list(employee_admin.get_queryset(request))

    assert visible == [own_employee]


def test_security_admin_sees_all_departments(rf: RequestFactory, django_user_model):
    SetupGroupsCommand().handle()
    group = Group.objects.get(name="Security Admin")
    user = django_user_model.objects.create_user(username="sec_admin", password="x")
    user.groups.add(group)
    user = django_user_model.objects.get(pk=user.pk)

    EmployeeFactory(department=DepartmentFactory())
    EmployeeFactory(department=DepartmentFactory())

    request = rf.get("/admin/employees/employee/")
    request.user = user

    employee_admin = EmployeeAdmin(Employee, admin.site)
    assert employee_admin.get_queryset(request).count() == 2
