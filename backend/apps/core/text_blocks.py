"""
Tiny plain-text-to-HTML renderer shared by course slides and the email builder: a blank line
starts a new paragraph and "- " lines are bullets. Every piece of text is escaped, so authors
can't inject markup or script. (Gophish merge fields such as {{.FirstName}} pass through as text.)
"""

from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe


def render_text_blocks(text, *, p_style="", ul_style="", li_style=""):
    """Return safe HTML. The *_style arguments add inline styles (email clients ignore stylesheets)."""
    p_attr = format_html(' style="{}"', p_style) if p_style else ""
    ul_attr = format_html(' style="{}"', ul_style) if ul_style else ""
    li_attr = format_html(' style="{}"', li_style) if li_style else ""
    blocks, paragraph, bullets = [], [], []

    def flush():
        if paragraph:
            blocks.append(format_html("<p{}>{}</p>", p_attr, " ".join(paragraph)))
            paragraph.clear()
        if bullets:
            items = format_html_join("", "<li{}>{}</li>", ((li_attr, b) for b in bullets))
            blocks.append(format_html("<ul{}>{}</ul>", ul_attr, items))
            bullets.clear()

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("- "):
            if paragraph:
                flush()
            bullets.append(line[2:].strip())
        else:
            if bullets:
                flush()
            paragraph.append(line)
    flush()
    return mark_safe("".join(blocks))  # noqa: S308 — assembled only from format_html (escaped) pieces
