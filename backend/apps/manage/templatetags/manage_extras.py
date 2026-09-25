from django import template

register = template.Library()


@register.simple_tag
def shared_content_editable(user):
    """True when `user` may change org-wide content (see manage.access.org_wide_content)."""
    from apps.core.scoping import managed_departments

    return user.has_perm("campaigns.change_campaign") and managed_departments(user) is None


@register.filter
def status_badge(status):
    """CSS modifier for a campaign status pill."""
    return {"draft": "", "pending_approval": "badge--medium", "approved": "badge--low", "launched": "badge--accent"}.get(status, "")


_SECTIONS = (
    ("campaign", "campaigns"),
    ("content", "content"), ("catalog", "content"), ("smart-group", "content"), ("template", "content"),
    ("page", "content"), ("image", "content"), ("landing", "content"), ("email", "content"), ("deliverability", "settings"),
    ("training", "training"), ("module", "training"), ("question", "training"), ("slide", "training"), ("polic", "training"),
    ("assignment", "training"),
    ("employee", "people"), ("department", "people"), ("exemption", "people"), ("reported", "reported"),
    ("schedule", "schedules"), ("api-key", "settings"), ("mail", "settings"),
    ("governance", "governance"), ("audit", "governance"), ("user", "governance"), ("access-review", "governance"),
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
    "Kind": "Report type",
    "Repeat every days": "Repeat every (days)",
    "Module": "Training module",
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
    if isinstance(value, bool):
        return "yes" if value else "no"  # not Python's True/False
    return str(value)


@register.filter
def audit_details(metadata):
    """An audit entry's extra facts as one readable line (before/after, reasons, counts)."""
    if not isinstance(metadata, dict):
        return ""
    return " · ".join(
        f"{str(k).replace('_', ' ').replace('gophish', 'engine')}: {_plain(v)}" for k, v in metadata.items() if v not in ("", None))


@register.filter
def action_label(action):
    """'training_due_date_extended' -> 'Training due date extended'."""
    text = str(action).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


# section -> [(tab label, url name, permission needed (None = any staff), url-name prefixes that make it current)]
_TABS = {
    "training": [
        ("Modules", "manage:training", "training.view_trainingmodule", ("training", "module", "question", "slide")),
        ("Assignments", "manage:assignments", "training.view_trainingassignment", ("assignment",)),
        ("Mandatory training", "manage:policies", "training.view_trainingpolicy", ("polic",)),
    ],
    "people": [
        ("Employees", "manage:employees", "employees.view_employee", ("employee",)),
        ("Departments", "manage:departments", "employees.view_department", ("department",)),
        ("Exemptions", "manage:exemptions", "employees.view_employee", ("exemption",)),
    ],
    "settings": [
        ("Mail server", "manage:mail-settings", "campaigns.approve_campaign", ("mail",)),
        ("Deliverability check", "manage:deliverability", "campaigns.change_campaign", ("deliverability",)),
        ("API keys", "manage:api-keys", None, ("api-key",)),
    ],
    "governance": [
        ("Program & controls", "manage:governance", "core.view_auditlogentry", ("governance",)),
        ("Users & access", "manage:users", "core.manage_user_access", ("user", "access-review")),
        ("Audit log", "manage:audit-log", "core.view_auditlogentry", ("audit",)),
    ],
}


@register.inclusion_tag("manage/_tabs.html", takes_context=True)
def section_tabs(context):
    """The tab strip for related pages (Training: modules / assignments / mandatory), shown on every page in
    the section. Only tabs the person may open are listed, and nothing is drawn for a single tab."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    name = match.url_name if match else ""
    section = next((sec for prefix, sec in _SECTIONS if name.startswith(prefix)), "")
    user = getattr(request, "user", None)
    tabs = []
    for label, url_name, perm, prefixes in _TABS.get(section, []):
        if perm is None or (user is not None and user.has_perm(perm)):
            tabs.append({"label": label, "url": url_name, "current": name.startswith(prefixes)})
    return {"tabs": tabs if len(tabs) > 1 else []}


def first_allowed(user, *options):
    """First (url_name, permission) the user may open: where a merged sidebar entry should land."""
    for url_name, perm in options:
        if perm is None or user.has_perm(perm):
            return url_name
    return options[-1][0]


@register.simple_tag
def landing(user, section):
    """Where the sidebar entry for a merged section leads for this person."""
    tabs = _TABS[section]
    return first_allowed(user, *[(url, perm) for _, url, perm, _ in tabs])
