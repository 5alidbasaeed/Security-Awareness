from django.contrib import admin

from apps.core.admin_mixins import DepartmentScopedAdminMixin

from .models import RiskScoreSnapshot


@admin.register(RiskScoreSnapshot)
class RiskScoreSnapshotAdmin(DepartmentScopedAdminMixin, admin.ModelAdmin):
    """Read-only: snapshots are computed, never hand-edited."""

    department_lookup = "employee__department"
    list_display = ("employee", "score", "algorithm_version", "computed_at")
    list_filter = ("algorithm_version", "employee__department")
    search_fields = ("employee__full_name", "employee__email")
    date_hierarchy = "computed_at"
    readonly_fields = ("employee", "score", "algorithm_version", "computed_at", "contributing_metrics")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
