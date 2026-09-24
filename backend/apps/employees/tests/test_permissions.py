import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import RequestFactory

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.admin import EmployeeAdmin
from apps.employees.models import Employee
from apps.employees.tests.factories import EmployeeFactory

pytestmark = pytest.mark.django_db


class _DummyMessages:
    def add(self, level, message, extra_tags=""):
        pass


def test_report_viewer_cannot_export_employee_csv(rf: RequestFactory, django_user_model):
    SetupGroupsCommand().handle()
    viewer_group = Group.objects.get(name="Report Viewer")
    user = django_user_model.objects.create_user(username="viewer", password="x")
    user.groups.add(viewer_group)
    user = django_user_model.objects.get(pk=user.pk)  # fresh instance, no stale perm cache

    EmployeeFactory()
    request = rf.post("/admin/employees/employee/")
    request.user = user
    request._messages = _DummyMessages()

    employee_admin = EmployeeAdmin(Employee, admin.site)
    response = employee_admin.export_as_csv(request, Employee.objects.all())

    assert response is None  # denied — no CSV returned


def _admin_request(rf, django_user_model, group):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=group.replace(" ", "").lower(), password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    request = rf.post("/admin/employees/employee/")
    request.user = django_user_model.objects.get(pk=user.pk)
    request._messages = _DummyMessages()
    return request


def test_department_manager_cannot_export_employee_csv(rf, django_user_model):
    request = _admin_request(rf, django_user_model, "Department Manager")
    EmployeeFactory()

    assert EmployeeAdmin(Employee, admin.site).export_as_csv(request, Employee.objects.all()) is None


def test_security_admin_can_export_employee_csv(rf, django_user_model):
    request = _admin_request(rf, django_user_model, "Security Admin")
    EmployeeFactory()

    assert EmployeeAdmin(Employee, admin.site).export_as_csv(request, Employee.objects.all()).status_code == 200


def test_department_manager_cannot_change_exemption_or_clear_department(rf, django_user_model):
    from apps.employees.tests.factories import DepartmentFactory

    request = _admin_request(rf, django_user_model, "Department Manager")
    DepartmentFactory().managers.add(request.user)
    employee_admin = EmployeeAdmin(Employee, admin.site)

    assert "is_exempt" in employee_admin.get_readonly_fields(request)
    department_field = employee_admin.formfield_for_foreignkey(Employee._meta.get_field("department"), request)
    assert department_field.required


def test_security_admin_can_change_exemption(rf, django_user_model):
    request = _admin_request(rf, django_user_model, "Security Admin")

    assert "is_exempt" not in EmployeeAdmin(Employee, admin.site).get_readonly_fields(request)
