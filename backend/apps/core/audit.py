"""
The one place that writes AuditLogEntry rows. Call this instead of creating
AuditLogEntry objects directly, so every caller stays consistent.
"""

from .models import AuditLogEntry


def log_action(*, actor, action: str, target_description: str, **metadata) -> AuditLogEntry:
    return AuditLogEntry.objects.create(
        actor=actor,
        action=action,
        target_description=target_description,
        metadata=metadata,
    )
