from apps.core.managers import AppendOnlyQuerySet


class EventQuerySet(AppendOnlyQuerySet):
    """The event log's queryset — see AppendOnlyQuerySet and CLAUDE.md invariant #3."""
