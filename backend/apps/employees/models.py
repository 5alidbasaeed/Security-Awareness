from django.conf import settings
from django.db import models
from django.utils import timezone


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
    is_active = models.BooleanField(
        default=True,
        help_text="Offboarded employees are deactivated, never deleted — their event history is immutable.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deactivated_at = models.DateTimeField(
        null=True, blank=True, editable=False,
        help_text="When they were offboarded. Starts the privacy-retention clock; cleared if they are reactivated.",
    )

    class Meta:
        # Separate from change_employee: Department Managers can edit their own people but: Department Managers can edit their own people but
        # must not be able to pull the raw employee list out of the system.
        permissions = [("export_employee_data", "Can export employee data as CSV")]

    def save(self, *args, **kwargs):
        # Stamp the moment of deactivation on every path that goes through save(), including the
        # importer's save(update_fields=["is_active"]). Retention counts from here, not from created_at.
        if not self.is_active and self.deactivated_at is None:
            self.deactivated_at = timezone.now()
        elif self.is_active and self.deactivated_at is not None:
            self.deactivated_at = None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "is_active" in update_fields:
            kwargs["update_fields"] = list(set(update_fields) | {"deactivated_at"})
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} <{self.email}>"
