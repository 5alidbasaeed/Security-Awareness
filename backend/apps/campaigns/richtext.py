"""
Sanitiser for the email composer's rich text. The browser's editor produces whatever HTML it
likes, so nothing it sends is trusted: this rebuilds the message from an allow-list.

  * only basic formatting tags survive (paragraphs, bold/italic/underline, lists, headings, quotes);
  * every link points at the tracked page ({{.URL}}), whatever the author typed — a simulation has
    exactly one place to click, and no outside address is ever put into a message;
  * images must be ones from the image library (`data-image-id`) and are rewritten to their public
    address; anything else (data: URIs, remote pictures, scripts, styles, forms) is dropped;
  * only Gophish's merge fields ({{.FirstName}}, ...) are kept; any other `{{ }}` is defused so an
    author can't write a template action that breaks or abuses the engine's templating.
"""

import re
from html import escape
from html.parser import HTMLParser

ALLOWED = {"p", "br", "div", "span", "b", "strong", "i", "em", "u", "ul", "ol", "li", "h1", "h2", "h3", "blockquote", "hr", "a", "img"}
VOID = {"br", "hr", "img"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "noscript", "template", "svg", "math", "form", "textarea", "select", "button", "head", "title"}
MERGE_FIELDS = ("FirstName", "LastName", "Email", "Position")
_MERGE = re.compile(r"\{\{\s*\.(" + "|".join(MERGE_FIELDS) + r")\s*\}\}")
BUTTON_STYLE = "display:inline-block;padding:11px 24px;background:#0b5fff;color:#ffffff;text-decoration:none;border-radius:4px;font-weight:600"
IMG_STYLE = "max-width:100%;height:auto;border:0"


def _defuse(text: str) -> str:
    """Keep the allowed merge fields, neutralise every other template delimiter."""
    kept = []

    def stash(match):
        kept.append("{{." + match.group(1) + "}}")
        return f"\x00{len(kept) - 1}\x00"

    text = _MERGE.sub(stash, text).replace("{{", "{ {").replace("}}", "} }")
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0  # depth inside a dropped element

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in DROP_WITH_CONTENT:
            if tag not in VOID:
                self.skip += 1
            return
        if tag not in ALLOWED:
            return  # unknown wrapper: keep its text, drop the tag
        attrs = dict(attrs)
        if tag == "a":
            style = BUTTON_STYLE if "data-button" in attrs else "color:#0b5fff;text-decoration:underline"
            marker = ' data-button="1"' if "data-button" in attrs else ""
            self.out.append(f'<a href="{{{{.URL}}}}"{marker} style="{style}">')
        elif tag == "img":
            src = self._image_src(attrs.get("data-image-id"))
            if src:
                self.out.append(f'<img src="{escape(src)}" alt="{escape(attrs.get("alt") or "")}" style="{IMG_STYLE}">')
            return
        else:
            self.out.append(f"<{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and tag in ALLOWED and not self.skip and self.stack and self.stack[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in DROP_WITH_CONTENT:
                self.skip -= 1
            return
        if tag in ALLOWED and tag not in VOID and tag in self.stack:
            while self.stack:  # close anything left open inside it, then the tag itself
                self.out.append(f"</{(top := self.stack.pop())}>")
                if top == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(_defuse(escape(data, quote=False)))

    @staticmethod
    def _image_src(raw_id):
        from .models import EmailImage

        if not raw_id or not str(raw_id).isdigit():
            return ""
        image = EmailImage.objects.filter(pk=int(raw_id)).first()
        return image.public_url if image else ""

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out).strip()


def sanitize_email_html(html: str) -> str:
    parser = _Sanitizer()
    parser.feed(html or "")
    parser.close()
    return parser.result()


def to_editor_html(stored_html: str) -> str:
    """Stored (sanitised) HTML -> what the composer shows: library images point at the staff copy
    (the public address may not be reachable from the staff browser) and carry their id."""
    from django.conf import settings
    from django.urls import reverse

    from .models import EmailImage

    base = re.escape(settings.EMAIL_IMAGE_BASE_URL.rstrip("/") + "/")

    def swap(match):
        image = EmailImage.objects.filter(stored_name=match.group(1)).first()
        if image is None:
            return match.group(0)
        return f'<img src="{reverse("manage:image-file", args=[image.pk])}" data-image-id="{image.pk}"'

    html = re.sub(r'<img src="' + base + r"([0-9a-f]{32}\.(?:png|jpg|gif))\"", swap, stored_html or "")
    return re.sub(r' style="[^"]*"', "", html)  # the page's CSP forbids inline styles; the editor CSS styles them
