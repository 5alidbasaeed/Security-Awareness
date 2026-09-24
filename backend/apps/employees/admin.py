import csv

from django.contrib import admin
from django.http import HttpResponse

from apps.core.admin_mixins import AuditedAdminMixin, DepartmentScopedAdminMixin
from apps.core.audit import log_action
from apps.core.csv_safe import csv_safe
from apps.core.scoping import managed_departments

from .models import Department, Employee


@admin.register(Employee)
class EmployeeAdmin(DepartmentScopedAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "employee"
    department_lookup = "department"
    list_display = ("full_name", "email", "department", "is_exempt", "created_at")
    list_filter = ("department", "is_exempt")
    search_fields = ("full_name", "email")
    actions = ["export_as_csv"]

    def get_readonly_fields(self, request, obj=None):
        # Exempting someone removes them from every simulation. A Department Manager must not be
        # able to exempt their own weakest performers and so improve their department's figures.
        if managed_departments(request.user) is not None:
            return (*super().get_readonly_fields(request, obj), "is_exempt")
        return super().get_readonly_fields(request, obj)

    @admin.action(description="Export selected employees as CSV")
    def export_as_csv(self, request, queryset):
        # PII export is Security-Admin-only, not "can view/change employees": a Report Viewer or a
        # Department Manager must not be able to pull the raw employee list even though the
        # action is visible to them.
        if not self.require_permission(
            request, "employees.export_employee_data", "You don't have permission to export employee data."
        ):
            return

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=employees.csv"
        writer = csv.writer(response)
        writer.writerow(["full_name", "email", "department", "is_exempt"])
        for employee in queryset:
            writer.writerow(
                csv_safe(v) for v in (employee.full_name, employee.email, employee.department, employee.is_exempt)
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
    filter_horizontal = ("managers",)
