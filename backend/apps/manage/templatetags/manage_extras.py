from django import template

register = template.Library()


@register.filter
def status_badge(status):
    """CSS modifier for a campaign status pill."""
    return {"draft": "", "pending_approval": "badge--accent", "approved": "badge--low", "launched": "badge--high"}.get(status, "")


_SECTIONS = (
    ("campaign", "campaigns"),
    ("content", "content"), ("catalog", "content"), ("smart-group", "content"), ("template", "content"),
    ("page", "content"), ("image", "content"), ("landing", "content"), ("email", "content"), ("deliverability", "content"),
    ("training", "training"), ("module", "training"), ("question", "training"), ("slide", "training"), ("polic", "training"),
    ("employee", "employees"), ("department", "departments"), ("reported", "reported"),
    ("schedule", "schedules"), ("api-key", "api-keys"), ("mail", "mail"),
)


@register.simple_tag(takes_context=True)
def manage_section(context):
    """Which sidebar entry is current, from the URL name (so no template has to say)."""
    match = getattr(context.get("request"), "resolver_match", None)
    name = match.url_name if match else ""
    if name == "index":
        return "home"
    return next((section for prefix, section in _SECTIONS if name.startswith(prefix)), "")
