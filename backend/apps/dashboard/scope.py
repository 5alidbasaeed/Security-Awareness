"""
Row-level visibility for the dashboard, built on core.scoping so it can never
disagree with the admin or the /analytics/ API about what a Department
Manager may see. Every dashboard query starts from one of these — never from
Model.objects directly (see the code-review-checklist skill).
"""

from apps.campaigns.models import Campaign
from apps.core.scoping import managed_departments
from apps.employees.models import Department, Employee
from apps.training.models import TrainingAssignment


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
