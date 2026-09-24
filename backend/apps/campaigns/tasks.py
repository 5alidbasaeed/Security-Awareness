"""
Celery Beat entry point — see CELERY_BEAT_SCHEDULE in settings/base.py.
Calls the exact same launch_campaign() service the admin "Launch" action
uses, so a scheduled launch and a manual launch produce identical results
(same rate limiting, same exemption filtering, same audit trail shape).
"""

import logging

from celery import shared_task
from django.utils import timezone

from apps.engine.factory import get_client

from .models import Campaign
from .services import CampaignLaunchError, launch_campaign

logger = logging.getLogger(__name__)


@shared_task
def launch_scheduled_campaigns():
    now = timezone.now()
    due = Campaign.objects.filter(
        status=Campaign.Status.APPROVED, scheduled_at__isnull=False, scheduled_at__lte=now
    )

    client = get_client()
    launched, failed = 0, 0
    for campaign in due:
        try:
            # actor=None: no user initiated this, the scheduler did.
            # log_action already treats a null actor as valid.
            launch_campaign(campaign, actor=None, client=client)
            launched += 1
        except CampaignLaunchError as exc:
            logger.warning("launch_scheduled_campaigns: could not launch %s — %s", campaign, exc)
            failed += 1
        except Exception:
            logger.exception("launch_scheduled_campaigns: unexpected error launching %s", campaign)
            failed += 1

    logger.info("launch_scheduled_campaigns: launched %d, failed %d", launched, failed)
