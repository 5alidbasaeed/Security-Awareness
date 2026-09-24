"""
Image uploads for the email and landing-page builders.

Uploaded images are served to recipients' mail clients by nginx from a read-only view of the
same volume (see docker-compose.yml, `email-images`), under an unguessable name. Hence the
strict rules here: only PNG/JPEG/GIF (no SVG: it can carry script), identified by their real
bytes rather than the filename or the browser's claimed type, small, and stored under a random
name whose extension comes from that verified type.
"""

import re
import uuid
from pathlib import Path

from django.conf import settings

MAX_BYTES = 1_000_000
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)
CONTENT_TYPES = {ext: ctype for _, ext, ctype in SIGNATURES}
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif)$")


class ImageRejected(ValueError):
    """The upload isn't an acceptable image; the message is safe to show the user."""


def identify(data: bytes) -> str:
    for signature, ext, _ in SIGNATURES:
        if data.startswith(signature):
            return ext
    raise ImageRejected("Only PNG, JPEG and GIF images are accepted.")


def image_root() -> Path:
    return Path(settings.EMAIL_IMAGE_ROOT)


def public_url(stored_name: str) -> str:
    return settings.EMAIL_IMAGE_BASE_URL.rstrip("/") + "/" + stored_name


def save_upload(uploaded, user):
    """Validate and store an uploaded file; returns the EmailImage row."""
    from .models import EmailImage

    data = uploaded.read(MAX_BYTES + 1)
    if not data:
        raise ImageRejected("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ImageRejected(f"Images can be at most {MAX_BYTES // 1000} KB.")
    ext = identify(data)
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    root = image_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / stored_name).write_bytes(data)
    original = re.sub(r"[^\w.\- ]", "_", Path(uploaded.name or "image").name)[:100]
    return EmailImage.objects.create(
        stored_name=stored_name, original_name=original, size=len(data),
        uploaded_by=user if getattr(user, "is_authenticated", False) else None,
    )
