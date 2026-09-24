"""
Fires coaching and SIEM export on new events, deferred to on_commit so the row
is visible when the worker runs. Coaching only on simulation failures; SIEM
export on every event when a webhook is configured.
"""

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.events.models import Event

from .tasks import export_event_to_siem, send_coaching_email

FAILURE_TYPES = {Event.EventType.LINK_CLICKED, Event.EventType.CREDENTIAL_ATTEMPT}


@receiver(post_save, sender=Event)
def on_event(sender, instance, created, **kwargs):
    if not created:
        return
    event_id = instance.pk
    if instance.event_type in FAILURE_TYPES and settings.COACHING_ENABLED:
        transaction.on_commit(lambda: send_coaching_email.delay(event_id))
    if getattr(settings, "SIEM_WEBHOOK_URL", ""):
        transaction.on_commit(lambda: export_event_to_siem.delay(event_id))
