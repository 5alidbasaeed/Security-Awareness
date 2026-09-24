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


@register.filter
def duration(seconds):
    """Seconds -> the unit a person would say out loud (45 s, 12 min, 3.5 h, 2.1 days)."""
    if seconds is None:
        return "—"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


@register.filter
def ratio(value):
    return "—" if value is None else f"{value:g}"


@register.filter
def points(value):
    """Percentage-point gap, signed: +2.5 pts."""
    if value is None:
        return ""
    return f"+{value:g} pts" if value > 0 else f"{value:g} pts"


GRADE_LABELS = {"met": "Meets target", "missed": "Below target", "no_data": "No data"}
GRADE_BADGES = {"met": "badge--low", "missed": "badge--high", "no_data": ""}


@register.filter
def grade_label(status):
    return GRADE_LABELS.get(status, "")


@register.filter
def grade_badge(status):
    return GRADE_BADGES.get(status, "")


OUTCOME_LABELS = {"submitted": "Submitted data", "clicked": "Clicked the link", "reported": "Reported",
                  "no_action": "No action"}
OUTCOME_BADGES = {"submitted": "badge--high", "clicked": "badge--medium", "reported": "badge--low", "no_action": ""}


@register.filter
def outcome_label(outcome):
    return OUTCOME_LABELS.get(outcome, "")


@register.filter
def outcome_badge(outcome):
    return OUTCOME_BADGES.get(outcome, "")
