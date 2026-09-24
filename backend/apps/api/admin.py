from django.contrib import admin

from .models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    """
    Keys are managed here or in the dashboard. The raw key is shown once, right after
    creation, and never again. Prefer creating keys in the dashboard (Settings → API keys),
    which is built for it; this admin exists for support/revocation.
    """

    list_display = ("name", "prefix", "owner", "scopes", "created_at", "expires_at", "revoked_at", "last_used_at")
    list_filter = ("revoked_at", "owner")
    readonly_fields = ("prefix", "hashed_key", "created_at", "last_used_at")
    search_fields = ("name", "prefix", "owner__username")

    def has_add_permission(self, request):
        # Adding here can't show the raw key safely; create keys in the dashboard instead.
        return False
