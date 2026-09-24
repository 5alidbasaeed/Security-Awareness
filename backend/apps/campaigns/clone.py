"""
Cloning a real login page into a landing-page draft.

Two hazards shape this module:

* **Server-side request forgery.** The page is fetched by the phishing engine (Django itself sits on
  the internal-only network and can't reach the internet), from a machine that can also reach
  the company network. So the URL is checked first: http/https only, no credentials in the address,
  and no host that is (or resolves to) a private, loopback, link-local or otherwise internal address.
  `LANDING_CLONE_ALLOW_PRIVATE_HOSTS` switches that off for development only.
* **Whatever the source page contains ends up on our phishing listener.** So the fetched HTML is
  rebuilt from an allow-list: no scripts (a JavaScript form submit would bypass Gophish's password
  stripping, CLAUDE.md invariant #4), no frames/objects/SVG, no event handlers, every link inert, every
  form forced to a plain POST to the landing page itself, relative resources made absolute so the page
  still looks right. Password inputs are kept as real `type="password"` inputs so the engine strips them.
"""

import ipaddress
import re
import socket
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from django.conf import settings

MAX_HTML_BYTES = 1_500_000


class CloneError(ValueError):
    """The address can't be cloned; the message is safe to show the user."""


def validate_clone_url(raw: str) -> str:
    url = (raw or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CloneError("Enter a full web address starting with http:// or https://.")
    if parts.username or parts.password:
        raise CloneError("The address can't contain a username or password.")
    if settings.LANDING_CLONE_ALLOW_PRIVATE_HOSTS:
        return url
    host = parts.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".localhost", ".internal", ".lan", ".corp", ".home")) or "." not in host and not _is_ip(host):
        raise CloneError("That address points at an internal host. Only public websites can be cloned.")
    addresses = _resolve(host)
    if any(_internal(a) for a in addresses):
        raise CloneError("That address points at an internal host. Only public websites can be cloned.")
    return url


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _internal(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _resolve(host: str) -> list[str]:
    if _is_ip(host):
        return [host.strip("[]")]
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []  # this machine can't resolve it (it has no internet); the engine will, and refuses what it can't reach


# ---- sanitising -----------------------------------------------------------------------------

KEEP = {
    "html", "head", "body", "title", "meta", "style", "link", "div", "span", "p", "a", "img", "form", "input", "button",
    "label", "select", "option", "textarea", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "thead",
    "tbody", "tfoot", "tr", "td", "th", "br", "hr", "strong", "b", "em", "i", "u", "small", "section", "header",
    "footer", "main", "nav", "aside", "article", "fieldset", "legend", "center", "font", "caption",
}
VOID = {"meta", "link", "img", "input", "br", "hr"}
DROP_WITH_CONTENT = {"script", "noscript", "iframe", "frame", "frameset", "object", "embed", "svg", "math", "template", "canvas", "audio", "video", "applet"}
GLOBAL_ATTRS = {"class", "id", "title", "lang", "dir", "role", "width", "height", "align", "valign", "colspan", "rowspan", "bgcolor", "color", "size", "face", "cellpadding", "cellspacing", "border"}
INPUT_TYPES = {"text", "password", "email", "tel", "number", "search", "url", "checkbox", "radio", "hidden", "submit", "button"}
UNSAFE_CSS = re.compile(r"javascript:|expression\s*\(|behaviou?r\s*:|-moz-binding|@import", re.I)
SAFE_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$")


class _Cloner(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip = 0
        self.in_style = False
        self.stats = {"scripts": 0, "forms": 0, "passwords": 0, "stylesheets": 0}

    def _absolute(self, value: str) -> str:
        target = urljoin(self.base, (value or "").strip())
        return target if urlsplit(target).scheme in ("http", "https") else ""

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in DROP_WITH_CONTENT:
            if tag == "script":
                self.stats["scripts"] += 1
            if tag not in VOID:
                self.skip += 1
            return
        if tag not in KEEP:
            return
        attrs = {k: (v or "") for k, v in attrs}
        rendered = self._render(tag, attrs)
        if rendered is None:
            return
        self.out.append(rendered)
        if tag not in VOID:
            self.stack.append(tag)
            self.in_style = tag == "style"

    def _render(self, tag, attrs):  # noqa: C901 — one branch per tag is the clearest form for an allow-list
        keep = {k: v for k, v in attrs.items() if k in GLOBAL_ATTRS or k.startswith("aria-")}
        style = attrs.get("style", "")
        if style and not UNSAFE_CSS.search(style):
            keep["style"] = style
        if tag == "a":
            keep["href"] = "#"  # every link is inert: the page is for one thing only
        elif tag == "img":
            src = attrs.get("src", "")
            src = src if SAFE_DATA_IMAGE.match(src) else self._absolute(src)
            if not src:
                return None
            keep.update(src=src, alt=attrs.get("alt", ""))
        elif tag == "link":
            if "stylesheet" not in attrs.get("rel", "").lower():
                return None
            href = self._absolute(attrs.get("href", ""))
            if not href:
                return None
            self.stats["stylesheets"] += 1
            keep = {"rel": "stylesheet", "href": href}
        elif tag == "meta":
            if "charset" in attrs:
                keep = {"charset": "utf-8"}
            elif attrs.get("name", "").lower() == "viewport":
                keep = {"name": "viewport", "content": attrs.get("content", "")}
            else:
                return None
        elif tag == "form":
            self.stats["forms"] += 1
            keep.update(method="post")  # no action: it posts back to the landing page, where the engine records it
        elif tag == "input":
            kind = attrs.get("type", "text").lower()
            if kind not in INPUT_TYPES:
                return None
            if kind == "password":
                self.stats["passwords"] += 1
            keep.update(type=kind)
            for name in ("name", "value", "placeholder", "autocomplete", "maxlength"):
                if name in attrs:
                    keep[name] = attrs[name]
            for flag in ("required", "checked"):
                if flag in attrs:
                    keep[flag] = flag
        elif tag == "button":
            keep["type"] = "submit" if attrs.get("type", "submit").lower() not in ("button", "reset") else "button"
        elif tag == "label" and "for" in attrs:
            keep["for"] = attrs["for"]
        elif tag == "select" and "name" in attrs:
            keep["name"] = attrs["name"]
        elif tag == "option":
            for name in ("value", "selected"):
                if name in attrs:
                    keep[name] = attrs[name]
        elif tag == "textarea":
            for name in ("name", "rows", "cols", "placeholder"):
                if name in attrs:
                    keep[name] = attrs[name]
        pairs = "".join(f' {name}="{escape(value)}"' for name, value in keep.items())
        return f"<{tag}{pairs}>"

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and tag in KEEP and not self.skip and self.stack and self.stack[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in DROP_WITH_CONTENT:
                self.skip -= 1
            return
        if tag in KEEP and tag not in VOID and tag in self.stack:
            while self.stack:
                closed = self.stack.pop()
                self.out.append(f"</{closed}>")
                if closed == tag:
                    break
            self.in_style = "style" in self.stack

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_style:
            self.out.append("" if UNSAFE_CSS.search(data) else data)  # a stylesheet with a script hook is dropped whole
        else:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_cloned_html(html: str, base_url: str) -> tuple[str, list[str]]:
    """Returns (safe page, warnings for the person cloning it)."""
    if len(html.encode("utf-8", "ignore")) > MAX_HTML_BYTES:
        raise CloneError("That page is too large to clone.")
    parser = _Cloner(base_url)
    parser.feed(html or "")
    parser.close()
    cleaned = parser.result()
    stats, warnings = parser.stats, []
    if stats["scripts"]:
        warnings.append(f"Removed {stats['scripts']} script(s). The page won't run any JavaScript.")
    if not stats["forms"]:
        warnings.append("No sign-in form was found (it may be built with JavaScript). The page shows no input to submit.")
    elif not stats["passwords"]:
        warnings.append("The form has no password field, so entering a password can't be tested.")
    if stats["stylesheets"]:
        warnings.append("Styling is loaded from the original website; the page looks wrong if that site is unreachable.")
    if not cleaned.strip():
        raise CloneError("Nothing usable was found at that address.")
    return cleaned, warnings
