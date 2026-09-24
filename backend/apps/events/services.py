"""
The one place Event rows get created from ingestion code. Both the webhook
view (real-time) and the reconciliation task (fallback) call
`ingest_engine_event` instead of each independently looking up the employee,
parsing the time, stripping credentials and wrapping Event.objects.create() in
its own transaction.atomic()/IntegrityError dance — see the
backend-conventions skill for why the savepoint matters.
"""

from datetime import timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.employees.models import Employee

from .models import Event
from .sanitize import strip_sensitive_fields


class IngestOutcome:
    CREATED = "created"
    DUPLICATE = "duplicate"  # expected on redelivery — not an error
    UNKNOWN_EMPLOYEE = "unknown_employee"
    BAD_TIME = "bad_time"


def parse_event_time(value):
    """
    An aware datetime, or None for anything unusable. parse_datetime() returns
    None for garbage but *raises* for a well-formed out-of-range value
    ("2026-13-45T...") or a non-string — one bad entry must never 500 the
    webhook (Gophish would redeliver it forever) or abort a reconciliation run.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


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


def ingest_engine_event(*, campaign, email, event_type, external_id, time, metadata) -> str:
    """
    Records one phishing-engine event for `campaign`. Credential stripping
    happens here, so no ingestion path can forget it (CLAUDE.md invariant #4).
    Returns an IngestOutcome value.
    """
    employee = Employee.objects.filter(email__iexact=email).first() if isinstance(email, str) and email else None
    if employee is None:
        return IngestOutcome.UNKNOWN_EMPLOYEE
    occurred_at = parse_event_time(time)
    if occurred_at is None:
        return IngestOutcome.BAD_TIME

    event = record_event(
        event_type=event_type,
        employee=employee,
        campaign=campaign,
        source=Event.Source.GOPHISH,
        external_id=external_id,
        occurred_at=occurred_at,
        metadata=strip_sensitive_fields(metadata),
    )
    return IngestOutcome.CREATED if event is not None else IngestOutcome.DUPLICATE
