"""
Auto-assigns training when an employee fails a campaign's simulation
(clicks the link or submits data) — see CLAUDE.md/plan doc Phase 2 scope.
Connected in apps.py::ready().
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.events.models import Event

from .models import TrainingAssignment

QUALIFYING_EVENT_TYPES = {Event.EventType.LINK_CLICKED, Event.EventType.CREDENTIAL_ATTEMPT}


@receiver(post_save, sender=Event)
def auto_assign_training_on_failure(sender, instance, created, **kwargs):
    if not created or instance.event_type not in QUALIFYING_EVENT_TYPES:
        return

    module = instance.campaign.training_module
    if module is None:
        return  # this campaign has no training module configured — nothing to assign

    # Dedupe: a second failure on the same module while an assignment is
    # already outstanding shouldn't spam a duplicate one.
    already_assigned = TrainingAssignment.objects.filter(
        employee=instance.employee, module=module, completed_at__isnull=True, waived_at__isnull=True
    ).exists()
    if already_assigned:
        return

    TrainingAssignment.objects.create(
        employee=instance.employee,
        module=module,
        triggered_by_event=instance,
    )
