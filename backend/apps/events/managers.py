from django.db import models


class EventQuerySet(models.QuerySet):
    """
    Blocks the two easy accidental-mutation paths on the append-only event
    log. See CLAUDE.md invariant #3 and the code-review-checklist skill —
    an escape hatch via raw SQL/direct DB access still exists, deliberately;
    this catches ordinary application code, not a determined operator.
    """

    def update(self, **kwargs):
        raise TypeError("Event rows are append-only — create a new Event instead of updating one.")

    def delete(self):
        raise TypeError("Event rows are append-only — they are never deleted.")
