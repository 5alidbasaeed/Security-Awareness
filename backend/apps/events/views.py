"""
Gophish webhook ingestion. See CLAUDE.md invariants #3/#4 and the
backend-conventions/code-review-checklist skills: verify the signature
before touching the payload, strip password-like fields before storing
metadata, and rely on the DB unique constraint (not app-level checks alone)
for idempotency — Gophish delivers webhooks at-least-once, so redelivery
of the same event is expected, not an error.
"""

import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.campaigns.models import Campaign
from apps.employees.models import Employee
from apps.engine.gophish import GOPHISH_MESSAGE_TO_EVENT_TYPE, build_gophish_external_id

from .models import Event
from .sanitize import strip_sensitive_fields
from .services import record_event

logger = logging.getLogger(__name__)


def _valid_signature(request) -> bool:
    header = request.headers.get("X-Gophish-Signature", "")
    signature = header.removeprefix("sha256=")
    expected = hmac.new(
        settings.GOPHISH_WEBHOOK_SECRET.encode(), request.body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@csrf_exempt
@require_POST
def gophish_webhook(request):
    if not settings.GOPHISH_WEBHOOK_SECRET or not _valid_signature(request):
        logger.warning("Gophish webhook rejected: invalid or missing X-Gophish-Signature")
        return HttpResponseForbidden("invalid signature")

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return HttpResponseBadRequest("invalid JSON")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("expected a JSON object")

    message = payload.get("message")
    event_type = GOPHISH_MESSAGE_TO_EVENT_TYPE.get(message)
    if event_type is None:
        logger.info("Gophish webhook: unrecognized message %r — ignored", message)
        return HttpResponse(status=200)  # ack — don't make Gophish retry an event we'll never understand

    gophish_campaign_id = str(payload.get("campaign_id", ""))
    campaign = Campaign.objects.filter(gophish_campaign_id=gophish_campaign_id).first()
    if campaign is None:
        logger.warning("Gophish webhook: no Campaign for gophish_campaign_id=%s — skipped", gophish_campaign_id)
        return HttpResponse(status=200)

    email = payload.get("email", "")
    employee = Employee.objects.filter(email__iexact=email).first()
    if employee is None:
        logger.warning("Gophish webhook: no Employee for email=%s — skipped", email)
        return HttpResponse(status=200)

    occurred_at = parse_datetime(payload.get("time", "")) if payload.get("time") else None
    if occurred_at is None:
        logger.warning("Gophish webhook: unparseable time=%r — skipped", payload.get("time"))
        return HttpResponse(status=200)

    external_id = build_gophish_external_id(
        campaign_id=gophish_campaign_id, email=email, message=message, time=payload.get("time")
    )
    metadata = strip_sensitive_fields(payload.get("details", {}) or {})

    event = record_event(
        event_type=event_type,
        employee=employee,
        campaign=campaign,
        source=Event.Source.GOPHISH,
        external_id=external_id,
        occurred_at=occurred_at,
        metadata=metadata,
    )
    if event is None:
        # Expected on redelivery of an already-ingested event — not an error.
        logger.debug("Gophish webhook: duplicate delivery for external_id=%s — deduped", external_id)

    return JsonResponse({"ok": True})
