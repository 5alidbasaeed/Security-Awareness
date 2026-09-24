"""
Compiles the email builder's fields into email-safe HTML: table layout and inline styles only
(mail clients ignore stylesheets and scripts). Everything an author types is escaped.

The button and the optional logo-link both point at Gophish's {{.URL}} merge field, so a click on
either is tracked exactly like any other link in a simulation; {{.Tracker}} adds the open pixel.
"""

import re
from html import unescape

from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.core.text_blocks import render_text_blocks

# accent = header bar / button colour; bar_text = text colour on the accent; surface = page background
LAYOUTS = {
    "corporate": {"accent": "#0b5fff", "bar_text": "#ffffff", "surface": "#eef1f6", "bar": True},
    "alert": {"accent": "#c62828", "bar_text": "#ffffff", "surface": "#f6eeee", "bar": True},
    "minimal": {"accent": "#1f2937", "bar_text": "#ffffff", "surface": "#ffffff", "bar": False},
}
FONT = "font-family:Segoe UI,Helvetica,Arial,sans-serif"


def render_email(*, layout, logo_url="", hero_url="", heading="", body="", button_label="", footer_note="", brand_name="",
                 body_html="") -> str:
    if layout == "minimal":
        return _plain_email(body_html, heading=heading, body=body, button_label=button_label, footer_note=footer_note)
    spec = LAYOUTS.get(layout, LAYOUTS["corporate"])
    accent = spec["accent"]

    logo = format_html('<img src="{}" alt="" height="36" style="display:block;border:0;height:36px">', logo_url) if logo_url else ""
    bar = ""
    if spec["bar"]:
        bar = format_html(
            '<tr><td style="background:{};padding:18px 28px">{}</td></tr>', accent, logo or format_html(
                '<span style="{};font-size:16px;font-weight:600;color:{}">{}</span>', FONT, spec["bar_text"], brand_name or "Notification",
            ),
        )
    elif logo:
        bar = format_html('<tr><td style="padding:24px 28px 0">{}</td></tr>', logo)

    hero = format_html(
        '<tr><td style="padding:0"><img src="{}" alt="" width="600" style="display:block;width:100%;max-width:600px;height:auto;border:0"></td></tr>',
        hero_url,
    ) if hero_url else ""

    heading_html = format_html(
        '<h1 style="{};font-size:22px;line-height:1.3;margin:0 0 16px;color:#111827">{}</h1>', FONT, heading,
    ) if heading else ""
    paragraphs = format_html('<div style="{};font-size:15px;line-height:1.6;color:#374151">{}</div>', FONT, mark_safe(body_html)) if body_html else render_text_blocks(  # noqa: S308 — already sanitised (richtext.py)
        body, p_style=f"{FONT};font-size:15px;line-height:1.6;margin:0 0 14px;color:#374151",
        ul_style="margin:0 0 14px;padding-left:20px", li_style=f"{FONT};font-size:15px;line-height:1.6;color:#374151",
    )
    button = format_html(
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:22px 0 8px"><tr>'
        '<td style="background:{};border-radius:6px"><a href="{{{{.URL}}}}" style="{};display:inline-block;padding:12px 26px;'
        'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none">{}</a></td></tr></table>',
        accent, FONT, button_label,
    ) if button_label else ""
    footer = format_html(
        '<tr><td style="padding:18px 28px;border-top:1px solid #e5e7eb;{};font-size:12px;line-height:1.5;color:#6b7280">{}</td></tr>',
        FONT, footer_note,
    ) if footer_note else ""

    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{spec["surface"]};padding:24px 0"><tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;'
        'border:1px solid #e5e7eb;border-radius:8px;overflow:hidden">'
        f"{bar}{hero}<tr><td style=\"padding:28px\">{heading_html}{paragraphs}{button}</td></tr>{footer}"
        "</table></td></tr></table>{{.Tracker}}"
    )


def _plain_email(body_html, *, heading, body, button_label, footer_note) -> str:
    """A normal-looking message: no card, no header bar, just text in a mail client's default font."""
    inner = mark_safe(body_html) if body_html else render_text_blocks(  # noqa: S308 — already sanitised (richtext.py)
        body, p_style="margin:0 0 12px", ul_style="margin:0 0 12px;padding-left:20px",
    )
    head = format_html('<h2 style="font-size:18px;margin:0 0 12px">{}</h2>', heading) if heading else ""
    button = format_html(
        '<p><a href="{{{{.URL}}}}" style="display:inline-block;padding:11px 24px;background:#0b5fff;color:#ffffff;'
        'text-decoration:none;border-radius:4px;font-weight:600">{}</a></p>', button_label,
    ) if button_label and not body_html else ""
    foot = format_html('<p style="color:#6b7280;font-size:12px;margin-top:18px">{}</p>', footer_note) if footer_note and not body_html else ""
    return f'<div style="{FONT};font-size:14px;line-height:1.5;color:#111827">{head}{inner}{button}{foot}</div>{{{{.Tracker}}}}'


def _strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-3]>", "\n", html)
    text = re.sub(r"<a [^>]*data-button[^>]*>(.*?)</a>", lambda m: f"{m.group(1)}: {{{{.URL}}}}", text)
    return re.sub(r"<[^>]+>", "", text)


def render_plain_text(*, heading="", body="", button_label="", footer_note="", body_html="") -> str:
    """Plain-text alternative part (Gophish sends it alongside the HTML)."""
    if body_html:
        return unescape(re.sub(r"\n{3,}", "\n\n", _strip_tags(body_html))).strip()
    parts = [heading, body, f"{button_label}: {{{{.URL}}}}" if button_label else "", footer_note]
    return "\n\n".join(part for part in parts if part)
