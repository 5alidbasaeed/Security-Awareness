"""
The one place that writes AuditLogEntry rows. Call this instead of creating
AuditLogEntry objects directly, so every caller stays consistent.
"""

from .models import AuditLogEntry

_TARGET_MAX_LENGTH = AuditLogEntry._meta.get_field("target_description").max_length


def truncate(text: str, max_length: int) -> str:
    return text if len(text) <= max_length else text[: max_length - 1] + "…"


def log_action(*, actor, action: str, target_description: str, **metadata) -> AuditLogEntry:
    # str(obj) of e.g. a TrainingAssignment (name + email + module title) can exceed the column;
    # an over-long description must never make the audited action itself fail.
    return AuditLogEntry.objects.create(
        actor=actor,
        action=action,
        target_description=truncate(target_description, _TARGET_MAX_LENGTH),
        metadata=metadata,
    )
