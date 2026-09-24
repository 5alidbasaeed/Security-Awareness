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
