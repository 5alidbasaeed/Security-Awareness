"""Dashboard row scoping now lives in core.scoping (shared with reporting); re-exported here."""

from apps.core.scoping import visible_assignments, visible_campaigns, visible_departments, visible_employees

__all__ = ["visible_assignments", "visible_campaigns", "visible_departments", "visible_employees"]
