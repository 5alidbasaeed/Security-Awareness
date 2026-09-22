import csv

from django.contrib import admin, messages
from django.http import HttpResponse

from apps.core.admin_mixins import AuditedAdminMixin
from apps.core.audit import log_action

from .models import Department, Employee


@admin.register(Employee)
class EmployeeAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "employee"
    list_display = ("full_name", "email", "department", "is_exempt", "created_at")
    list_filter = ("department", "is_exempt")
    search_fields = ("full_name", "email")
    actions = ["export_as_csv"]

    @admin.action(description="Export selected employees as CSV")
    def export_as_csv(self, request, queryset):
        # PII export is Admin-only, not just "can view employees" — a Viewer
        # (view_employee only, per setup_groups) must not be able to pull
        # the raw employee list even though the action is visible to them.
        if not request.user.has_perm("employees.change_employee"):
            self.message_user(request, "You don't have permission to export employee data.", level=messages.ERROR)
            return

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=employees.csv"
        writer = csv.writer(response)
        writer.writerow(["full_name", "email", "department", "is_exempt"])
        for employee in queryset:
            writer.writerow(
                [employee.full_name, employee.email, employee.department, employee.is_exempt]
            )

        log_action(
            actor=request.user,
            action="employee_data_exported",
            target_description=f"{queryset.count()} employee(s)",
        )
        return response


@admin.register(Department)
class DepartmentAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "department"
    list_display = ("name", "manager")
    search_fields = ("name",)
