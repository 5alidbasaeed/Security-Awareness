from django import template

from apps.events.models import Event
from apps.risk_scoring import analytics

register = template.Library()

EVENT_LABELS = {
    Event.EventType.EMAIL_SENT: "Email sent",
    Event.EventType.EMAIL_DELIVERED: "Email delivered",
    Event.EventType.EMAIL_OPENED: "Email opened",
    Event.EventType.LINK_CLICKED: "Clicked the link",
    Event.EventType.CREDENTIAL_ATTEMPT: "Submitted data",
    Event.EventType.PHISHING_REPORTED: "Reported the email",
    Event.EventType.TRAINING_ASSIGNED: "Training assigned",
    Event.EventType.TRAINING_STARTED: "Training started",
    Event.EventType.TRAINING_COMPLETED: "Training completed",
    Event.EventType.QUIZ_COMPLETED: "Quiz completed",
}
LEVEL_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "unscored": "Not scored"}


@register.filter
def risk_level(score):
    return analytics.risk_level(score)


@register.filter
def level_label(level):
    return LEVEL_LABELS.get(level, "")


@register.filter
def score_fmt(value):
    """Scores are 0-100; show one decimal, or an em dash when there is none."""
    return "—" if value is None else f"{float(value):.1f}"


TREND_LABELS = {
    "improving": "Improving",
    "stagnant": "Stagnant",
    "worsening": "Worsening",
    "insufficient_data": "Not enough history",
}


@register.filter
def trend_label(trend):
    return TREND_LABELS.get(trend, "")


@register.filter
def percent(value):
    return "—" if value is None else f"{value:g}%"


@register.filter
def event_label(event_type):
    return EVENT_LABELS.get(event_type, str(event_type).replace("_", " ").capitalize())


@register.filter
def signed(value):
    if value is None:
        return ""
    return f"+{value:g}" if value > 0 else f"{value:g}"


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """Current query string with `changes` applied (None removes a key) — for sort and pagination links."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.simple_tag(takes_context=True)
def sort_link(context, field):
    """Query string that sorts by `field`, flipping direction if it is already the active sort."""
    current = context["request"].GET.get("sort") or context.get("default_sort", "")
    target = f"-{field}" if current == field else field
    return qs(context, sort=target, page=None)


@register.simple_tag(takes_context=True)
def sort_state(context, field):
    """aria-sort value for a column header."""
    current = context["request"].GET.get("sort") or context.get("default_sort", "")
    if current == field:
        return "ascending"
    if current == f"-{field}":
        return "descending"
    return "none"
