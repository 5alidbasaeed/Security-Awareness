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
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.campaigns.models import Campaign
from apps.engine.gophish import GOPHISH_MESSAGE_TO_EVENT_TYPE, build_gophish_external_id

from .services import IngestOutcome, ingest_engine_event

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
    event_type = GOPHISH_MESSAGE_TO_EVENT_TYPE.get(message) if isinstance(message, str) else None
    if event_type is None:
        logger.info("Gophish webhook: unrecognized message %r — ignored", message)
        return HttpResponse(status=200)  # ack — don't make Gophish retry an event we'll never understand

    gophish_campaign_id = str(payload.get("campaign_id", ""))
    campaign = Campaign.objects.filter(gophish_campaign_id=gophish_campaign_id).first()
    if campaign is None:
        logger.warning("Gophish webhook: no Campaign for gophish_campaign_id=%s — skipped", gophish_campaign_id)
        return HttpResponse(status=200)

    email, time = payload.get("email"), payload.get("time")
    external_id = build_gophish_external_id(campaign_id=gophish_campaign_id, email=email, message=message, time=time)
    outcome = ingest_engine_event(
        campaign=campaign,
        email=email,
        event_type=event_type,
        external_id=external_id,
        time=time,
        metadata=payload.get("details") or {},
    )
    if outcome == IngestOutcome.UNKNOWN_EMPLOYEE:
        logger.warning("Gophish webhook: no Employee for email=%r — skipped", email)
    elif outcome == IngestOutcome.BAD_TIME:
        logger.warning("Gophish webhook: unparseable time=%r — skipped", time)
    elif outcome == IngestOutcome.DUPLICATE:
        # Expected on redelivery of an already-ingested event — not an error.
        logger.debug("Gophish webhook: duplicate delivery for external_id=%s — deduped", external_id)
    # Always ack: a skipped event would be skipped again on every redelivery.
    return JsonResponse({"ok": True})
