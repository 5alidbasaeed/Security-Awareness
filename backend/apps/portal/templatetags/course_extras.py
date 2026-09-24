from django import template

from apps.core.text_blocks import render_text_blocks

register = template.Library()


@register.filter
def slide_body(text):
    """Render a slide's plain-text body (paragraphs and "- " bullets), fully escaped."""
    return render_text_blocks(text)
