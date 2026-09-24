"""
Reconciliation fallback — see CLAUDE.md invariant #3: webhooks are the
primary ingestion path, this exists only to catch a missed delivery.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.engine.factory import get_client

from .services import IngestOutcome, ingest_engine_event

logger = logging.getLogger(__name__)


@shared_task
def reconcile_all_active_campaigns():
    """
    Celery Beat entry point — see CELERY_BEAT_SCHEDULE in settings/base.py.
    Fans out to reconcile_campaign per launched campaign. Campaigns have no
    closed state, so "active" means launched within RECONCILE_WINDOW_DAYS:
    Gophish results stop changing soon after a send, and without a cut-off
    every campaign ever launched would be polled every 15 minutes forever.
    """
    window_start = timezone.now() - timedelta(days=settings.RECONCILE_WINDOW_DAYS)
    campaign_ids = list(
        Campaign.objects.filter(status=Campaign.Status.LAUNCHED, launched_at__gte=window_start).values_list(
            "pk", flat=True
        )
    )
    for campaign_id in campaign_ids:
        reconcile_campaign.delay(campaign_id)
    logger.info("reconcile_all_active_campaigns: queued %d campaign(s)", len(campaign_ids))


@shared_task
def reconcile_campaign(campaign_id: int):
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.warning("reconcile_campaign: no Campaign with pk=%s", campaign_id)
        return

    if not campaign.gophish_campaign_id:
        return  # not launched yet — nothing to reconcile

    client = get_client()
    events = client.get_campaign_results(campaign.gophish_campaign_id)

    created, deduped, skipped = 0, 0, 0
    for engine_event in events:
        outcome = ingest_engine_event(
            campaign=campaign,
            email=engine_event.raw.get("email"),
            event_type=engine_event.event_type,
            external_id=engine_event.external_id,
            time=engine_event.occurred_at,
            # Same field the webhook stores, so an event's metadata has one shape whichever path saw it first.
            metadata=engine_event.raw.get("details") or {},
        )
        if outcome == IngestOutcome.CREATED:
            created += 1
        elif outcome == IngestOutcome.DUPLICATE:
            deduped += 1
        else:
            skipped += 1

    logger.info(
        "reconcile_campaign(%s): %d created, %d already present, %d skipped",
        campaign_id, created, deduped, skipped,
    )
