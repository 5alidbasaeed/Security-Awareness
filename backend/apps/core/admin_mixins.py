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
