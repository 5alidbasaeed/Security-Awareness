from django.contrib import messages

from .audit import log_action


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
