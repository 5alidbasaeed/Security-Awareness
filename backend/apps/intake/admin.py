from django.contrib import admin

from apps.core.admin_mixins import AuditedAdminMixin

from .models import ReportedEmail


@admin.register(ReportedEmail)
class ReportedEmailAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "reported_email"
    list_display = ("subject", "sender", "reporter", "source", "verdict", "reported_at", "triaged_by")
    list_filter = ("verdict", "source")
    search_fields = ("subject", "sender", "reporter__email")
    readonly_fields = ("reporter", "subject", "sender", "notes", "headers", "source", "reported_at")
