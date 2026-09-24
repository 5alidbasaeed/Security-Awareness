"""
Recompute an employee's score when a scoring-relevant event lands — not on a
blanket schedule for everyone (see the backend-conventions skill). Deferred
to on_commit so the worker can never run before the event row is visible.
"""

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.events.models import Event

from .scoring import SCORING_EVENT_TYPES
from .tasks import recompute_score


@receiver(post_save, sender=Event)
def recompute_score_on_event(sender, instance, created, **kwargs):
    if not created or instance.event_type not in SCORING_EVENT_TYPES:
        return
    employee_id = instance.employee_id
    transaction.on_commit(lambda: recompute_score.delay(employee_id))
