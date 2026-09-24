"""
Row-level visibility, shared by the admin mixin, the /analytics/ API, the
dashboard and reporting so none of them can disagree about what a Department
Manager may see. Every query that lists people, campaigns or assignments for a
user must start from one of these — never from Model.objects directly.
"""

from apps.campaigns.models import Campaign
from apps.employees.models import Department, Employee
from apps.training.models import TrainingAssignment


def managed_departments(user):
    """
    None if `user` is not row-scoped (superuser, Security Admin, Report
    Viewer, ...); otherwise the queryset of departments a Department Manager
    may see.
    """
    if user.is_superuser or not user.groups.filter(name="Department Manager").exists():
        return None
    return Department.objects.filter(managers=user)


def visible_departments(user):
    managed = managed_departments(user)
    return managed if managed is not None else Department.objects.all()


def visible_employees(user):
    managed = managed_departments(user)
    if managed is None:
        return Employee.objects.all()
    return Employee.objects.filter(department__in=managed)


def visible_campaigns(user):
    managed = managed_departments(user)
    if managed is None:
        return Campaign.objects.all()
    return Campaign.objects.filter(target_department__in=managed)


def visible_assignments(user):
    return TrainingAssignment.objects.filter(employee__in=visible_employees(user))
