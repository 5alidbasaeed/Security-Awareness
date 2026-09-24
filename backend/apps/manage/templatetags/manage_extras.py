from django import template

register = template.Library()


@register.filter
def status_badge(status):
    """CSS modifier for a campaign status pill."""
    return {"draft": "", "pending_approval": "badge--medium", "approved": "badge--low", "launched": "badge--accent"}.get(status, "")


_SECTIONS = (
    ("campaign", "campaigns"),
    ("content", "content"), ("catalog", "content"), ("smart-group", "content"), ("template", "content"),
    ("page", "content"), ("image", "content"), ("landing", "content"), ("email", "content"), ("deliverability", "content"),
    ("training", "training"), ("module", "training"), ("question", "training"), ("slide", "training"), ("polic", "training"),
    ("employee", "employees"), ("department", "departments"), ("reported", "reported"),
    ("schedule", "schedules"), ("api-key", "api-keys"), ("mail", "mail"),
    ("governance", "governance"), ("audit", "audit"), ("user", "users"), ("access-review", "users"),
    ("exemption", "exemptions"), ("assignment", "assignments"),
)


@register.simple_tag(takes_context=True)
def manage_section(context):
    """Which sidebar entry is current, from the URL name (so no template has to say)."""
    match = getattr(context.get("request"), "resolver_match", None)
    name = match.url_name if match else ""
    if name == "index":
        return "home"
    return next((section for prefix, section in _SECTIONS if name.startswith(prefix)), "")


_LABELS = {
    "Content url": "Link to external training",
    "Duration minutes": "Duration (minutes)",
    "Is active": "Active",
    "Is exempt": "Exempt from simulations",
    "Exempt reason": "Reason for the exemption",
    "Exempt until": "Exemption ends on",
    "Due days": "Days to complete",
    "Logo url": "Logo image URL",
    "Template name": "Email template",
    "Landing page name": "Landing page",
    "Landing page url": "Landing page address (URL)",
    "New hire days": "New hire window (days)",
}


@register.filter
def field_label(label):
    """Plain-language form label (auto-generated ones read like column names)."""
    return _LABELS.get(str(label), label)


@register.filter
def field_help(text):
    """Help text without engine jargon."""
    return str(text).replace("Gophish email template name.", "Name of an email template under Emails & pages.") \
        .replace("Gophish landing page name.", "Name of a landing page under Emails & pages.") \
        .replace("Gophish ", "")


def _plain(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_plain(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_plain(v) for v in value) or "none"
    return str(value)


@register.filter
def audit_details(metadata):
    """An audit entry's extra facts as one readable line (before/after, reasons, counts)."""
    if not isinstance(metadata, dict):
        return ""
    return " · ".join(f"{str(k).replace('_', ' ')}: {_plain(v)}" for k, v in metadata.items() if v not in ("", None))


@register.filter
def action_label(action):
    """'training_due_date_extended' -> 'Training due date extended'."""
    text = str(action).replace("_", " ").strip()
    return text[:1].upper() + text[1:]
