from django.conf import settings
from django.contrib import admin, messages
from django.utils import timezone

from apps.core.admin_mixins import AuditedAdminMixin
from apps.core.audit import log_action
from apps.engine.factory import get_client

from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "campaign"
    list_display = ("name", "status", "target_department", "gophish_campaign_id", "launched_at")
    list_filter = ("status", "target_department")
    search_fields = ("name",)
    actions = ["launch_campaign"]

    @admin.action(description="Launch selected campaign(s) via Gophish")
    def launch_campaign(self, request, queryset):
        # Explicit check, not just "action is visible to staff": launching a
        # real campaign is a change, and the Viewer group per setup_groups
        # only ever gets view_* permissions — this is what actually enforces
        # that boundary rather than relying on Django admin's default action
        # visibility rules, which are looser than "has change permission."
        if not request.user.has_perm("campaigns.change_campaign"):
            self.message_user(request, "You don't have permission to launch campaigns.", level=messages.ERROR)
            return

        client = get_client()
        for campaign in queryset:
            if campaign.status == Campaign.Status.LAUNCHED:
                self.message_user(request, f"{campaign} is already launched — skipped.", level=messages.WARNING)
                continue
            if not campaign.target_department:
                self.message_user(
                    request, f"{campaign} has no target department — skipped.", level=messages.ERROR
                )
                continue

            try:
                ref = client.create_campaign(
                    name=campaign.name,
                    template_id=campaign.template_name,
                    target_group_id=campaign.target_department.name,
                    send_profile_id=settings.GOPHISH_DEFAULT_SEND_PROFILE,
                    page_id=campaign.landing_page_name,
                    url=campaign.landing_page_url,
                )
            except Exception as exc:  # noqa: BLE001 — surface any adapter failure to the admin, don't swallow it
                self.message_user(request, f"Failed to launch {campaign}: {exc}", level=messages.ERROR)
                continue

            campaign.gophish_campaign_id = ref.external_id
            campaign.status = Campaign.Status.LAUNCHED
            campaign.launched_at = timezone.now()
            campaign.save()

            log_action(
                actor=request.user,
                action="campaign_launched",
                target_description=str(campaign),
                gophish_campaign_id=campaign.gophish_campaign_id,
            )
            self.message_user(request, f"Launched {campaign} (Gophish campaign {ref.external_id}).")
