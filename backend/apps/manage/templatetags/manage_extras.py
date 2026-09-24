from django import template

register = template.Library()


@register.filter
def status_badge(status):
    """CSS modifier for a campaign status pill."""
    return {"draft": "", "pending_approval": "badge--accent", "approved": "badge--low", "launched": "badge--high"}.get(status, "")
