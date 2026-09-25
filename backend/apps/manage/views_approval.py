"""
The approval review page (opus_comments AG3): an approver sees exactly what they are signing off —
the rendered email, the landing page, who it reaches and when — before they approve or send it back.
Approve and reject still post to the existing, audited endpoints; this page only shows and links.
"""

from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from apps.campaigns.models import Campaign, EmailDraft, LandingDraft
from apps.campaigns.services import approval_blocked_reason
from apps.core.models import AuditLogEntry
from apps.core.scoping import visible_campaigns

from .access import manage_access
from .views_ux import _audience_summary


@manage_access("campaigns.approve_campaign", methods=("GET",))
def campaign_review(request, pk):
    campaign = get_object_or_404(
        visible_campaigns(request.user).select_related("target_department", "target_smart_group", "training_module",
                                                       "submitted_by", "created_by"),
        pk=pk,
    )
    if campaign.target_smart_group_id:
        audience_key = f"smart:{campaign.target_smart_group_id}"
    elif campaign.target_department_id:
        audience_key = f"dept:{campaign.target_department_id}"
    else:
        audience_key = ""
    email = EmailDraft.objects.filter(name=campaign.template_name).first()
    page = LandingDraft.objects.filter(name=campaign.landing_page_name).first()
    history = AuditLogEntry.objects.filter(target_description=str(campaign)).select_related("actor").order_by("-occurred_at")[:10]
    return render(request, "manage/campaign_review.html", {
        "active": "manage",
        "campaign": campaign,
        "audience": _audience_summary(request.user, audience_key) if audience_key else None,
        "email": email,
        "page": page,
        "email_preview": reverse("manage:email-preview", args=[campaign.template_name]) if campaign.template_name else None,
        "page_preview": reverse("manage:page-preview", args=[campaign.landing_page_name]) if campaign.landing_page_name else None,
        "blocked": approval_blocked_reason(campaign, request.user),
        "pending": campaign.status == Campaign.Status.PENDING_APPROVAL,
        "history": history,
    })
