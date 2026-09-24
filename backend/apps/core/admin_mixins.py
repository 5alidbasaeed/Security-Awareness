from django.contrib import messages

from apps.employees.models import Department

from .audit import log_action
from .scoping import managed_departments


class AuditedAdminMixin:
    """
    Attributes create/update/delete to the logged-in admin user. Signals
    can't do this without extra thread-local plumbing since they don't
    receive the request — overriding these ModelAdmin hooks gets a real
    actor for free, which is the point of an audit log.

    Set `audit_object_name` (e.g. "employee") on the subclass.
    """

    audit_object_name = None

    def require_permission(self, request, perm: str, error_message: str) -> bool:
        """
        Returns True if request.user has `perm`; otherwise shows
        `error_message` via the admin messages framework and returns False.
        Use at the top of a custom admin action — Django's default action
        visibility only requires *some* permission on the model, which is
        looser than what a specific action (e.g. launching a real campaign)
        should require.
        """
        if request.user.has_perm(perm):
            return True
        self.message_user(request, error_message, level=messages.ERROR)
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        log_action(
            actor=request.user,
            action=f"{self.audit_object_name}_updated" if change else f"{self.audit_object_name}_created",
            target_description=str(obj),
        )

    def delete_model(self, request, obj):
        description = str(obj)
        super().delete_model(request, obj)
        log_action(actor=request.user, action=f"{self.audit_object_name}_deleted", target_description=description)

    def delete_queryset(self, request, queryset):
        descriptions = [str(obj) for obj in queryset]
        super().delete_queryset(request, queryset)
        for description in descriptions:
            log_action(actor=request.user, action=f"{self.audit_object_name}_deleted", target_description=description)


class DepartmentScopedAdminMixin:
    """
    Restricts the admin's queryset to the departments a Department Manager
    manages (via Department.managers). Django's built-in permission
    framework is model-level only ("can change Employee at all"), not
    row-level ("which ones") — this mixin is the row-level half. Users NOT
    in the Department Manager group (Security Admin, Report Viewer, etc.)
    are unaffected; this only ever narrows access, never widens it beyond a
    user's existing model-level permissions.

    Set `department_lookup` on the subclass to the field path from this
    model to Department (e.g. "department" on Employee, "target_department"
    on Campaign).
    """

    department_lookup = "department"

    def _managed_departments(self, request):
        """None if the user isn't row-scoped; otherwise the departments they manage."""
        return managed_departments(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        managed = self._managed_departments(request)
        if managed is None:
            return qs
        return qs.filter(**{f"{self.department_lookup}__in": managed})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # Scoping the list isn't enough: without this a manager could move a
        # record into (or create one for) a department they don't manage.
        managed = self._managed_departments(request)
        if managed is not None and db_field.related_model is Department:
            kwargs["queryset"] = managed
            # ...or clear the department, which moves the record out of every
            # manager's scope and out of every campaign at once.
            formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
            formfield.required = True
            return formfield
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
