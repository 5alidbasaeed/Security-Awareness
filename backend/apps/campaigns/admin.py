from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.core.admin_mixins import AuditedAdminMixin, DepartmentScopedAdminMixin
from apps.core.audit import log_action
from apps.engine.factory import get_client

from .models import Campaign
from .services import CampaignLaunchError, launch_campaign


@admin.register(Campaign)
class CampaignAdmin(DepartmentScopedAdminMixin, AuditedAdminMixin, admin.ModelAdmin):
    audit_object_name = "campaign"
    department_lookup = "target_department"
    list_display = (
        "name",
        "status",
        "target_department",
        "training_module",
        "gophish_campaign_id",
        "scheduled_at",
        "launched_at",
        "preview_link",
    )
    list_filter = ("status", "target_department")
    search_fields = ("name",)
    actions = ["submit_for_approval", "approve_campaign", "launch_campaign_action"]

    @admin.display(description="Landing page")
    def preview_link(self, campaign):
        url = reverse("admin:campaigns_campaign_preview_landing_page", args=[campaign.pk])
        return format_html('<a href="{}" target="_blank">Preview</a>', url)

    def get_urls(self):
        urls = [
            path(
                "<int:campaign_id>/preview-landing-page/",
                self.admin_site.admin_view(self.preview_landing_page_view),
                name="campaigns_campaign_preview_landing_page",
            ),
        ]
        return urls + super().get_urls()

    def preview_landing_page_view(self, request, campaign_id):
        # self.get_queryset(request), not Campaign.objects: keeps Department
        # Manager scoping and requires nothing beyond what the changelist
        # already exposes. A missing Gophish page is a 404, not a 500.
        if not self.has_view_permission(request):
            raise PermissionDenied
        campaign = get_object_or_404(self.get_queryset(request), pk=campaign_id)
        try:
            html = get_client().get_landing_page_html(campaign.landing_page_name)
        except ValueError as exc:
            raise Http404(str(exc)) from exc
        return HttpResponse(html)

    @admin.action(description="Submit selected draft campaign(s) for approval")
    def submit_for_approval(self, request, queryset):
        for campaign in queryset:
            if campaign.status != Campaign.Status.DRAFT:
                self.message_user(request, f"{campaign} is not a draft — skipped.", level=messages.WARNING)
                continue
            campaign.status = Campaign.Status.PENDING_APPROVAL
            campaign.submitted_by = request.user
            campaign.submitted_at = timezone.now()
            campaign.save()
            log_action(actor=request.user, action="campaign_submitted", target_description=str(campaign))
            self.message_user(request, f"{campaign} submitted for approval.")

    @admin.action(description="Approve selected pending campaign(s)")
    def approve_campaign(self, request, queryset):
        if not self.require_permission(
            request, "campaigns.approve_campaign", "You don't have permission to approve campaigns."
        ):
            return
        for campaign in queryset:
            if campaign.status != Campaign.Status.PENDING_APPROVAL:
                self.message_user(request, f"{campaign} is not pending approval — skipped.", level=messages.WARNING)
                continue
            campaign.status = Campaign.Status.APPROVED
            campaign.approved_by = request.user
            campaign.approved_at = timezone.now()
            campaign.save()
            log_action(actor=request.user, action="campaign_approved", target_description=str(campaign))
            self.message_user(request, f"{campaign} approved.")

    @admin.action(description="Launch selected campaign(s) via Gophish")
    def launch_campaign_action(self, request, queryset):
        # Explicit check, not just "action is visible to staff": launching a
        # real campaign is a change, and the Viewer/Report Viewer group per
        # setup_groups only ever gets view_* permissions — this is what
        # actually enforces that boundary rather than relying on Django
        # admin's default action visibility rules, which are looser than
        # "has change permission."
        if not self.require_permission(request, "campaigns.change_campaign", "You don't have permission to launch campaigns."):
            return

        client = get_client()
        for campaign in queryset:
            try:
                launch_campaign(campaign, actor=request.user, client=client)
            except CampaignLaunchError as exc:
                self.message_user(request, str(exc), level=messages.ERROR)
                continue
            except Exception as exc:  # noqa: BLE001 — surface any adapter failure to the admin, don't swallow it
                self.message_user(request, f"Failed to launch {campaign}: {exc}", level=messages.ERROR)
                continue
            self.message_user(request, f"Launched {campaign} (Gophish campaign {campaign.gophish_campaign_id}).")