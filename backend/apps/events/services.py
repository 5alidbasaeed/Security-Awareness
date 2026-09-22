"""
The one place Event rows get created from ingestion code. Both the webhook
view (real-time) and the reconciliation task (fallback) call this instead of
each independently wrapping Event.objects.create() in its own
transaction.atomic()/IntegrityError dance — see the backend-conventions
skill for why the savepoint matters.
"""

from django.db import IntegrityError, transaction

from .models import Event


def record_event(
    *, event_type, employee, campaign, source, external_id, occurred_at, metadata
) -> Event | None:
    """Returns the created Event, or None if external_id was already recorded (dedup)."""
    try:
        with transaction.atomic():
            return Event.objects.create(
                event_type=event_type,
                employee=employee,
                campaign=campaign,
                source=source,
                external_id=external_id,
                occurred_at=occurred_at,
                metadata=metadata,
            )
    except IntegrityError:
        return None
