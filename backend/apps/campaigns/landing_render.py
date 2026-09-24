"""
Compiles the landing-page builder's fields into a complete HTML page. Everything an author types
is escaped. The form is always a native <form method="post"> with real input fields, and the
password box is a genuine type="password" input: Gophish (capture_passwords=false) then strips it
before anything is stored, and a page that submitted through JavaScript could bypass that
(CLAUDE.md invariant #4). No script is ever emitted.
"""

import re

from django.utils.html import format_html
from django.utils.safestring import mark_safe

DEFAULT_ACCENT = "#0b5fff"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#111827;background:#f3f4f6}
.wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:32px 16px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 8px 30px rgba(17,24,39,.08);width:100%;max-width:400px;padding:32px}
.brand{display:block;margin:0 0 20px;font-size:18px;font-weight:700;color:var(--accent)}
.logo{display:block;height:40px;margin:0 0 20px}
h1{font-size:22px;line-height:1.3;margin:0 0 8px}
.sub{margin:0 0 22px;color:#6b7280;font-size:14px;line-height:1.5}
label{display:block;font-size:13px;font-weight:600;margin:0 0 6px}
input{display:block;width:100%;padding:11px 12px;margin:0 0 16px;border:1px solid #d1d5db;border-radius:8px;font-size:15px}
input:focus{outline:2px solid var(--accent);outline-offset:1px;border-color:transparent}
button{width:100%;padding:12px;border:0;border-radius:8px;background:var(--accent);color:#fff;font-size:15px;font-weight:600;cursor:pointer}
.foot{margin:22px 0 0;font-size:12px;color:#6b7280;line-height:1.5}
.doc{display:flex;align-items:center;gap:12px;margin:0 0 20px;padding:12px;border:1px solid #e5e7eb;border-radius:8px;background:#f9fafb}
.doc i{display:block;width:34px;height:42px;border-radius:4px;background:var(--accent);position:relative}
.doc i:after{content:"";position:absolute;left:8px;right:8px;top:14px;height:3px;background:#fff;box-shadow:0 8px #fff,0 16px #fff}
.doc span{font-size:14px;font-weight:600}.doc small{display:block;font-weight:400;color:#6b7280;font-size:12px}
.split{display:flex;width:100%;max-width:820px;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;box-shadow:0 8px 30px rgba(17,24,39,.08);background:#fff}
.split .side{flex:1;background:var(--accent);color:#fff;padding:36px 30px;display:flex;flex-direction:column;justify-content:center}
.split .side .brand{color:#fff}.split .side p{margin:0;line-height:1.6;opacity:.92}
.split .main{flex:1;padding:36px 32px}
@media(max-width:640px){.split{flex-direction:column}.split .side{padding:24px}}
"""


def _accent(value: str) -> str:
    return value if _HEX.match(value or "") else DEFAULT_ACCENT


def render_landing(*, layout, brand_name="", logo_url="", accent=DEFAULT_ACCENT, heading="", subtext="",
                   username_label="Work email", ask_password=True, button_label="Sign in", footer_note="") -> str:
    accent = _accent(accent)
    brand = (
        format_html('<img class="logo" src="{}" alt="">', logo_url) if logo_url
        else format_html('<span class="brand">{}</span>', brand_name) if brand_name else ""
    )
    sub = format_html('<p class="sub">{}</p>', subtext) if subtext else ""
    password = mark_safe(  # noqa: S308 — constant markup, no author text
        '<label for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" required>'
    ) if ask_password else ""
    form = format_html(
        '<form method="post"><label for="un">{}</label><input id="un" name="username" type="text" autocomplete="username" required>'
        "{}<button type=\"submit\">{}</button></form>",
        username_label, password, button_label or "Continue",
    )
    foot = format_html('<p class="foot">{}</p>', footer_note) if footer_note else ""
    title = heading or brand_name or "Sign in"

    if layout == "verify":
        body = format_html(
            '<div class="split"><div class="side">{}<h1>{}</h1>{}</div><div class="main">{}{}</div></div>',
            brand, heading, format_html("<p>{}</p>", subtext) if subtext else "", form, foot,
        )
    else:
        doc = format_html('<div class="doc"><i></i><span>{}<small>Secure document</small></span></div>', heading) if layout == "document" else ""
        head = "" if layout == "document" else format_html("<h1>{}</h1>", heading)
        body = format_html('<div class="card">{}{}{}{}{}{}</div>', brand, doc, head, sub, form, foot)

    return format_html(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>{}</title><style>:root{{--accent:{}}}{}</style></head><body><div class="wrap">{}</div></body></html>',
        title, accent, mark_safe(CSS.replace("\n", "")), body,  # noqa: S308 — constant stylesheet
    )


def learn_url() -> str:
    """The teachable-moment page people land on after submitting a simulated form."""
    from django.conf import settings
    from django.urls import reverse

    return settings.PORTAL_BASE_URL.rstrip("/") + reverse("portal:learn")
