"""
The one place a campaign actually gets sent — the admin "Launch" action and
the Celery Beat scheduler both call this, so there's exactly one tested
launch path instead of two copies drifting apart. See the backend-conventions
skill for why this pattern matters (same reasoning as events/services.py).
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.models import AuditLogEntry
from apps.employees.models import Employee
from apps.engine.base import TargetContact

from .audience import campaign_audience
from .models import Campaign


class CampaignLaunchError(Exception):
    """Raised for any reason a launch can't proceed — caller decides how to surface it."""


def approval_blocked_reason(campaign: Campaign, user) -> str | None:
    """
    Segregation of duties (ISO 27001 A.5.3): with REQUIRE_SEPARATE_APPROVER on (the default), the
    person who submitted a campaign can't also approve it. Both approval paths (the console and the
    Django admin) ask this one function. Turning the setting off is the documented break-glass for a
    single-admin deployment, and is shown on the governance page.
    """
    if getattr(settings, "REQUIRE_SEPARATE_APPROVER", True) and campaign.submitted_by_id == user.pk:
        return "You submitted this campaign, so someone else has to approve it (separation of duties)."
    return None


def reset_approvals_using(*, actor, email_template: str | None = None, landing_page: str | None = None, via: str = "manage") -> int:
    """
    An approval covers the content that was reviewed. Email templates and landing pages are shared by
    name, so changing one after a campaign using it was submitted or approved would send something the
    approver never saw. Every content save calls this: those campaigns go back to Draft (audited).
    Launched campaigns are history and are left alone. Returns how many were reset.
    """
    from django.db.models import Q

    match = Q()
    if email_template:
        match |= Q(template_name=email_template)
    if landing_page:
        match |= Q(landing_page_name=landing_page)
    if not match:
        return 0
    affected = list(Campaign.objects.filter(match, status__in=[Campaign.Status.PENDING_APPROVAL, Campaign.Status.APPROVED]))
    for campaign in affected:
        previous = campaign.status
        campaign.status = Campaign.Status.DRAFT
        campaign.submitted_by = campaign.submitted_at = None
        campaign.approved_by = campaign.approved_at = None
        campaign.save(update_fields=["status", "submitted_by", "submitted_at", "approved_by", "approved_at"])
        log_action(actor=actor, action="campaign_approval_reset", target_description=str(campaign),
                   reason="content changed after " + previous, email_template=email_template or "",
                   landing_page=landing_page or "", via=via)
    return len(affected)


def create_draft_campaign(*, actor, name, template_name, landing_page_name, landing_page_url,
                          target_department=None, training_module=None, scheduled_at=None) -> Campaign:
    """
    Creates a campaign in Draft — the only state anything programmatic (the API, the dashboard
    builder) may create. It is inert: no Gophish call, no send. It reaches a real send only after
    a human submits it, a Security Admin approves it, and a human launches it (launch_campaign).
    """
    campaign = Campaign.objects.create(
        name=name,
        template_name=template_name,
        landing_page_name=landing_page_name,
        landing_page_url=landing_page_url,
        target_department=target_department,
        training_module=training_module,
        scheduled_at=scheduled_at,
        status=Campaign.Status.DRAFT,
        created_by=actor,
    )
    log_action(actor=actor, action="campaign_created", target_description=str(campaign))
    return campaign


def _rate_limited() -> bool:
    window_start = timezone.now() - timedelta(hours=24)
    recent_launches = AuditLogEntry.objects.filter(action="campaign_launched", occurred_at__gte=window_start).count()
    return recent_launches >= settings.CAMPAIGN_LAUNCH_RATE_LIMIT


def _contact_from_employee(employee: Employee) -> TargetContact:
    parts = employee.full_name.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""
    return TargetContact(email=employee.email, first_name=first_name, last_name=last_name)


def _copy_contacts(campaign: Campaign, already: set[str]) -> list[TargetContact]:
    """
    The campaign's "also send a copy to" addresses, as extra targets: Gophish sends every target their
    own message, so this is how a copy (CC/BCC-style) reaches someone. Only addresses that are NOT
    employees qualify. An employee is either already in the audience or deliberately not, and exempt
    people must never be emailed; a non-employee's events map to no one, so results stay clean.
    """
    contacts = []
    for address in campaign.copy_addresses():
        key = address.lower()
        if key in already or Employee.objects.filter(email__iexact=address).exists():
            continue
        already.add(key)
        contacts.append(TargetContact(email=address, first_name=address.split("@")[0], last_name="(copy)"))
    return contacts


def engine_campaign_name(campaign: Campaign) -> str:
    """Unique per Django campaign, so a launch can recognise one it already sent (see find_campaign)."""
    return f"{campaign.name} [#{campaign.pk}]"


def _mark_launched(campaign: Campaign, external_id: str, *, actor, target_count, adopted=False) -> Campaign:
    campaign.gophish_campaign_id = external_id
    campaign.status = Campaign.Status.LAUNCHED
    campaign.launched_at = timezone.now()
    campaign.save()
    log_action(
        actor=actor,
        action="campaign_launched",
        target_description=str(campaign),
        gophish_campaign_id=external_id,
        target_count=target_count,
        adopted_existing_engine_campaign=adopted,
    )
    return campaign


def launch_campaign(campaign: Campaign, *, actor, client) -> Campaign:
    """
    actor may be None (the Celery Beat scheduler has no user) — log_action
    handles a null actor already. Raises CampaignLaunchError instead of
    returning a bool/None so a caller can't accidentally ignore a failure.

    The campaign row is locked and re-read first: the admin button and the
    scheduler can hold copies of the same campaign, and without this both
    would see "approved" and both would send it.
    """
    with transaction.atomic():
        Campaign.objects.select_for_update().get(pk=campaign.pk)
        campaign.refresh_from_db()

        if campaign.status == Campaign.Status.LAUNCHED:
            raise CampaignLaunchError(f"{campaign} is already launched.")
        if campaign.status != Campaign.Status.APPROVED:
            raise CampaignLaunchError(
                f"{campaign} must be Approved before launching (currently {campaign.get_status_display()})."
            )
        if not campaign.target_department_id and not campaign.target_smart_group_id:
            raise CampaignLaunchError(f"{campaign} has no target department or smart group.")

        # A previous attempt may have timed out after Gophish had already created (and sent) the
        # campaign, leaving it Approved here. Adopt that one — creating another would email
        # everyone a second time. Checked before the rate limit: adopting sends nothing.
        existing = client.find_campaign(engine_campaign_name(campaign))
        if existing is not None:
            return _mark_launched(campaign, existing.external_id, actor=actor, target_count=None, adopted=True)

        if not _rate_limited():
            # Exemption enforcement lives here, not in Gophish — is_exempt=False is
            # the only filter standing between "in this department" and "gets
            # targeted." See CLAUDE.md Phase 3 notes on why this didn't exist before.
            employees, group_name = campaign_audience(campaign)
            contacts = [_contact_from_employee(e) for e in employees]
            recipients = len(contacts)
            contacts += _copy_contacts(campaign, {c.email.lower() for c in contacts})
            if not recipients:
                raise CampaignLaunchError(
                    f"{campaign} has no eligible recipients "
                    "(everyone is exempt/inactive, or the audience is empty)."
                )

            client.sync_target_group(name=group_name, contacts=contacts)

            ref = client.create_campaign(
                name=engine_campaign_name(campaign),
                template_id=campaign.template_name,
                target_group_id=group_name,
                send_profile_id=settings.GOPHISH_DEFAULT_SEND_PROFILE,
                page_id=campaign.landing_page_name,
                url=campaign.landing_page_url,
            )

            return _mark_launched(campaign, ref.external_id, actor=actor, target_count=len(contacts))

    # Only reached when rate limited (the launch path returns above). Logged outside the atomic
    # block: raising inside it would roll the audit entry back.
    log_action(actor=actor, action="campaign_launch_rate_limited", target_description=str(campaign))
    raise CampaignLaunchError(
        f"Campaign launch rate limit reached ({settings.CAMPAIGN_LAUNCH_RATE_LIMIT}/24h) — try again later."
    )
