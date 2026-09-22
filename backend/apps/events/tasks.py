"""
Reconciliation fallback — see CLAUDE.md invariant #3: webhooks are the
primary ingestion path, this exists only to catch a missed delivery.
"""

import logging

from celery import shared_task
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from apps.campaigns.models import Campaign
from apps.employees.models import Employee
from apps.engine.factory import get_client

from .models import Event
from .sanitize import strip_sensitive_fields

logger = logging.getLogger(__name__)


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
        email = engine_event.raw.get("email")
        employee = Employee.objects.filter(email__iexact=email).first() if email else None
        occurred_at = parse_datetime(engine_event.occurred_at) if engine_event.occurred_at else None

        if employee is None or occurred_at is None:
            skipped += 1
            continue

        try:
            with transaction.atomic():  # savepoint — see views.py for why this matters
                Event.objects.create(
                    event_type=engine_event.event_type,
                    employee=employee,
                    campaign=campaign,
                    source=Event.Source.GOPHISH,
                    external_id=engine_event.external_id,
                    occurred_at=occurred_at,
                    metadata=strip_sensitive_fields(engine_event.raw),
                )
            created += 1
        except IntegrityError:
            deduped += 1

    logger.info(
        "reconcile_campaign(%s): %d created, %d already present, %d skipped",
        campaign_id, created, deduped, skipped,
    )
