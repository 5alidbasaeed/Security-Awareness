from django.db import models


class Department(models.Model):
    name = models.CharField(max_length=200, unique=True)
    manager = models.ForeignKey(
        "employees.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_departments",
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
