from django.conf import settings
from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=200, unique=True)
    manager = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_departments",
        help_text="The employee who manages this department (org-chart fact, not a login).",
    )
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="managed_departments_as_user",
        help_text=(
            "Django users in the Department Manager group whose admin access is scoped to this "
            "department. Distinct from `manager` above — that's an Employee (org-chart fact), "
            "this is a User (login/permission fact)."
        ),
    )

    def __str__(self):
        return self.name


class Employee(models.Model):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=200)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )
    is_exempt = models.BooleanField(
        default=False,
        help_text="Excluded from phishing simulation campaigns (e.g. legal hold, leave).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} <{self.email}>"
