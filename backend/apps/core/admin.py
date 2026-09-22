from django.contrib import admin

from .models import AuditLogEntry


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "actor", "action", "target_description")
    list_filter = ("action",)
    search_fields = ("target_description", "actor__username")
    readonly_fields = ("actor", "action", "target_description", "occurred_at", "metadata")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
