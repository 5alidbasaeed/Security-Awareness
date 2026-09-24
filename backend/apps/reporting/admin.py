from django.contrib import admin

from apps.core.admin_mixins import AuditedAdminMixin

from .models import GeneratedReport, ReportSchedule


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    """Read-only archive index. Downloads go through the dashboard so they are permission-checked and audit-logged."""

    list_display = ("filename", "kind", "period_start", "period_end", "scope_label", "generated_by", "generated_at")
    list_filter = ("kind", "file_format")
    search_fields = ("filename", "sha256")
    date_hierarchy = "generated_at"
    exclude = ("content",)
    readonly_fields = (
        "kind", "file_format", "filename", "content_type", "sha256", "size_bytes", "row_count", "period_start",
        "period_end", "as_of", "algorithm_version", "scope_label", "is_scoped", "contains_employee_data",
        "generated_by", "generated_at",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).without_content()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReportSchedule)
class ReportScheduleAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "report_schedule"
    list_display = ("name", "kind", "frequency", "is_active", "last_period_end", "created_by")
    list_filter = ("frequency", "is_active", "kind")
