"""
Pre-flight checks for a composed email: things that get a message filtered or make a simulation
less useful. Advice only; nothing here blocks saving. Each result is (level, message) where level
is "warn" or "info"; an empty list means nothing was found.
"""

import re

SPAMMY = ("free", "winner", "act now", "click here", "guarantee", "100%", "no obligation", "risk-free", "cash", "$$$")


def check_email(*, subject: str, html: str, text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    subject = (subject or "").strip()
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()

    if not subject:
        found.append(("warn", "The subject is empty."))
    else:
        if len(subject) > 78:
            found.append(("warn", "The subject is long; many mail apps cut it off after about 78 characters."))
        letters = [c for c in subject if c.isalpha()]
        if len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
            found.append(("warn", "The subject is mostly capital letters, which spam filters dislike."))
        if "!!" in subject or "$$" in subject:
            found.append(("warn", "Repeated ! or $ in the subject can trigger spam filters."))
    lowered = f"{subject} {visible}".lower()
    hits = [word for word in SPAMMY if word in lowered]
    if hits:
        found.append(("warn", "Words that often trigger spam filters: " + ", ".join(hits) + "."))

    links = len(re.findall(r"\{\{\.URL\}\}", html or ""))
    if links == 0:
        found.append(("warn", "There is no link or button, so nobody can click and nothing can be recorded."))
    elif links > 3:
        found.append(("warn", f"There are {links} links. Real messages rarely repeat one link this often."))

    images = re.findall(r"<img\b[^>]*>", html or "")
    if any(not re.search(r'alt="[^"]+"', tag) for tag in images):
        found.append(("info", "Some pictures have no description (alt text), which some mail apps show instead of the picture."))
    if images and len(visible) < 200:
        found.append(("warn", "Mostly pictures with very little text; filters treat this as suspicious."))
    if len(visible) < 40:
        found.append(("warn", "The message is very short."))
    if "{{.FirstName}}" not in (html or ""):
        found.append(("info", "It doesn't use the recipient's name, so it looks like a mass mailing."))
    if not (text or "").strip():
        found.append(("warn", "There is no plain-text version."))
    if len((html or "").encode()) > 100_000:
        found.append(("warn", "The message is over 100 KB; some mail apps clip large messages."))
    return found
