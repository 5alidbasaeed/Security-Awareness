"""
API keys for programmatic access. A key is a credential that acts *as a
specific human user*: it can never do anything its owner couldn't, and it is
further narrowed to an explicit set of scopes. The API surface itself exposes
only content-drafting operations — there is no launch/approve/send endpoint —
so no key, whatever its scopes or owner, can send a phishing email. Launching
stays a human action in the dashboard/admin (campaigns.services.launch_campaign).

Only a SHA-256 hash of the key is stored; the raw key is shown once at creation
and is unrecoverable afterwards, the same as an SSH key or a personal access token.
"""

import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

KEY_PREFIX = "sk_sim_"  # human-recognisable ("simulation platform key"), like GitHub's "ghp_"


class Scope(models.TextChoices):
    READ = "read", "Read analytics, campaigns, training and content"
    TRAINING_WRITE = "training:write", "Create and edit training modules and quizzes"
    CONTENT_WRITE = "content:write", "Create and edit email templates and landing pages"
    CAMPAIGNS_WRITE = "campaigns:write", "Create draft campaigns (never launch)"


# Each scope requires the owner to also hold this Django permission, so a key is never
# more powerful than the human who owns it (scope ∩ owner-permissions).
SCOPE_REQUIRED_PERMISSION = {
    Scope.READ: "risk_scoring.view_riskscoresnapshot",
    Scope.TRAINING_WRITE: "training.add_trainingmodule",
    Scope.CONTENT_WRITE: "campaigns.change_campaign",
    Scope.CAMPAIGNS_WRITE: "campaigns.add_campaign",
}


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ApiKey(models.Model):
    name = models.CharField(max_length=120, help_text="What this key is for, e.g. 'content-drafting bot'.")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
        help_text="The key acts as this user and can never exceed their permissions.",
    )
    prefix = models.CharField(max_length=16, unique=True, editable=False)  # plaintext, for lookup + display
    hashed_key = models.CharField(max_length=64, editable=False)  # sha256 of the full key
    scopes = models.JSONField(default=list, help_text="Which operations this key may perform.")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="After this the key stops working. Blank = no expiry.")
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, help_text="Set to disable the key immediately.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API key"

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @classmethod
    def generate(cls, *, name: str, owner, scopes: list[str], expires_at=None) -> tuple["ApiKey", str]:
        """Creates a key and returns (instance, raw_key). The raw key is never stored."""
        prefix = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        raw_key = f"{KEY_PREFIX}{prefix}_{secret}"
        instance = cls.objects.create(
            name=name, owner=owner, prefix=prefix, hashed_key=hash_key(raw_key),
            scopes=list(scopes), expires_at=expires_at,
        )
        return instance, raw_key

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def touch(self):
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])
