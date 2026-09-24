from django.conf import settings
from django.core.exceptions import ValidationError
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
    exempt_reason = models.CharField(
        max_length=300, blank=True,
        help_text="Why this person is excluded from simulations (legal hold, leave, accessibility...). Required when exempt.",
    )
    exempt_until = models.DateField(
        null=True, blank=True,
        help_text="The exemption lapses automatically after this date. Leave empty only for a documented open-ended reason.",
    )
    exempt_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="+",
    )
    exempt_set_at = models.DateTimeField(null=True, blank=True, editable=False)
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

    def clean(self):
        super().clean()
        if getattr(self, "_exemption_locked", False):
            return  # the editor can't change exemptions (Department Manager), so don't block their other edits
        if self.is_exempt and not self.exempt_reason.strip():
            raise ValidationError({"exempt_reason": "Say why this person is exempt: an exemption without a reason can't be audited."})
        if self.exempt_until and not self.is_exempt:
            raise ValidationError({"exempt_until": "An end date only makes sense while the person is exempt."})
        if self.is_exempt and self.exempt_until and self.exempt_until < timezone.localdate():
            raise ValidationError({"exempt_until": "The end date has already passed."})

    def save(self, *args, **kwargs):
        # Stamp the moment of deactivation on every path that goes through save(), including the
        # importer's save(update_fields=["is_active"]). Retention counts from here, not from created_at.
        if not self.is_active and self.deactivated_at is None:
            self.deactivated_at = timezone.now()
        elif self.is_active and self.deactivated_at is not None:
            self.deactivated_at = None
        touched = {"deactivated_at"}
        # Same for exemptions: stamp when it began, and drop the justification when it ends (the audit
        # log keeps the history) so a stale reason never sits next to a person who is no longer exempt.
        if self.is_exempt and self.exempt_set_at is None:
            self.exempt_set_at = timezone.now()
            touched.add("exempt_set_at")
        elif not self.is_exempt and (self.exempt_set_at or self.exempt_reason or self.exempt_until):
            self.exempt_set_at, self.exempt_reason, self.exempt_until, self.exempt_set_by = None, "", None, None
            touched |= {"exempt_set_at", "exempt_reason", "exempt_until", "exempt_set_by"}
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and ({"is_active", "is_exempt"} & set(update_fields)):
            kwargs["update_fields"] = list(set(update_fields) | touched)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} <{self.email}>"
