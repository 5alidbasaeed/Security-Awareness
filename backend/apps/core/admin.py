import csv

from django.contrib import admin, messages
from django.http import HttpResponse

from .audit import log_action
from .models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "actor", "action", "target_description")
    list_filter = ("action", "actor")
    search_fields = ("target_description", "actor__username")
    readonly_fields = ("actor", "action", "target_description", "occurred_at", "metadata")
    date_hierarchy = "occurred_at"
    actions = ["export_as_csv"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Export selected audit log entries as CSV")
    def export_as_csv(self, request, queryset):
        # Same "PII/sensitive-export is Security-Admin-only" precedent as
        # EmployeeAdmin.export_as_csv — the audit log itself is sensitive.
        if not self.require_permission_standalone(request):
            return

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=audit_log.csv"
        writer = csv.writer(response)
        writer.writerow(["occurred_at", "actor", "action", "target_description"])
        for entry in queryset:
            writer.writerow([entry.occurred_at, entry.actor, entry.action, entry.target_description])

        log_action(
            actor=request.user,
            action="audit_log_exported",
            target_description=f"{queryset.count()} entries",
        )
        return response

    def require_permission_standalone(self, request) -> bool:
        # AuditLogEntryAdmin doesn't use AuditedAdminMixin (it's read-only,
        # no save/delete to attribute) so it doesn't inherit
        # require_permission() — this is the same check inlined, since
        # pulling in the whole mixin for one method would be overkill here.
        if request.user.has_perm("core.change_auditlogentry"):
            return True
        self.message_user(request, "You don't have permission to export the audit log.", level=messages.ERROR)
        return False
