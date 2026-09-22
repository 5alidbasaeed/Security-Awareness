from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "event_type", "employee", "campaign", "source")
    list_filter = ("event_type", "source", "campaign")
    search_fields = ("employee__email", "employee__full_name")
    readonly_fields = [f.name for f in Event._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
