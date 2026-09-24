from django.db import models


class AppendOnlyQuerySet(models.QuerySet):
    """
    Blocks the two easy accidental-mutation paths on an append-only table
    (the event log, risk-score snapshots). An escape hatch via raw SQL still
    exists, deliberately: this catches ordinary application code, not a
    determined operator. See CLAUDE.md invariants #3 and #6.
    """

    def update(self, **kwargs):
        raise TypeError(f"{self.model.__name__} rows are append-only — create a new row instead of updating one.")

    def delete(self):
        raise TypeError(f"{self.model.__name__} rows are append-only — they are never deleted.")
