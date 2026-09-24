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

from .models import Campaign


class CampaignLaunchError(Exception):
    """Raised for any reason a launch can't proceed — caller decides how to surface it."""


def _rate_limited() -> bool:
    window_start = timezone.now() - timedelta(hours=24)
    recent_launches = AuditLogEntry.objects.filter(action="campaign_launched", occurred_at__gte=window_start).count()
    return recent_launches >= settings.CAMPAIGN_LAUNCH_RATE_LIMIT


def _contact_from_employee(employee: Employee) -> TargetContact:
    parts = employee.full_name.split(" ", 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""
    return TargetContact(email=employee.email, first_name=first_name, last_name=last_name)


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
        if not campaign.target_department:
            raise CampaignLaunchError(f"{campaign} has no target department.")

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
            employees = Employee.objects.filter(department=campaign.target_department, is_exempt=False)
            contacts = [_contact_from_employee(e) for e in employees]
            if not contacts:
                raise CampaignLaunchError(
                    f"{campaign} has no eligible employees in {campaign.target_department} "
                    "(everyone is exempt or the department is empty)."
                )

            client.sync_target_group(name=campaign.target_department.name, contacts=contacts)

            ref = client.create_campaign(
                name=engine_campaign_name(campaign),
                template_id=campaign.template_name,
                target_group_id=campaign.target_department.name,
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
