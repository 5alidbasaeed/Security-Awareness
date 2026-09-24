"""
PhishingEngineClient — the only interface any other app is allowed to depend
on for phishing-engine operations. See CLAUDE.md invariant #1 and the
backend-conventions skill: no code outside apps.engine may import a Gophish
SDK/HTTP client or construct a Gophish API URL directly.

See gophish.py for the concrete implementation and factory.py for how to
obtain a configured instance.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalCampaignRef:
    external_id: str


@dataclass(frozen=True)
class ExternalGroupRef:
    external_id: str


@dataclass(frozen=True)
class TargetContact:
    """One row of a target group — deliberately not an Employee: the adapter
    boundary shouldn't know about Django models, see CLAUDE.md invariant #1."""

    email: str
    first_name: str = ""
    last_name: str = ""


@dataclass(frozen=True)
class ExternalTemplateRef:
    external_id: str


@dataclass(frozen=True)
class ExternalPageRef:
    external_id: str


@dataclass(frozen=True)
class EngineEvent:
    external_id: str
    event_type: str
    occurred_at: str
    raw: dict


class PhishingEngineClient(ABC):
    @abstractmethod
    def sync_target_group(self, *, name: str, contacts: list[TargetContact]) -> ExternalGroupRef:
        """Creates the group if it doesn't exist, or replaces its target list if it does —
        always leaves the group's membership exactly matching `contacts`."""
        ...

    @abstractmethod
    def create_campaign(
        self,
        *,
        name: str,
        template_id: str,
        target_group_id: str,
        send_profile_id: str,
        page_id: str,
        url: str,
    ) -> ExternalCampaignRef: ...

    @abstractmethod
    def find_campaign(self, name: str) -> ExternalCampaignRef | None:
        """The engine's campaign with exactly this name, if one exists. Lets a launch whose
        create call timed out (after the engine had already sent) adopt it instead of re-sending."""
        ...

    @abstractmethod
    def launch_campaign(self, external_campaign_id: str) -> None: ...

    @abstractmethod
    def get_campaign_results(self, external_campaign_id: str) -> list[EngineEvent]: ...

    @abstractmethod
    def get_landing_page_html(self, page_name: str) -> str:
        """Fetches a landing page's raw HTML by name, for the dry-run/preview feature."""
        ...

    # --- Content authoring (Phase 6: dashboard builder + API draft content) ---------------
    # These let staff (or an API client) create the email templates and landing pages a
    # campaign references, without hand-editing them in Gophish. Creating content sends
    # nothing: an email/page is inert until a human approves and launches a campaign that
    # uses it (see campaigns.services.launch_campaign — the only path that sends).

    @abstractmethod
    def upsert_email_template(self, *, name: str, subject: str, html: str, text: str = "") -> ExternalTemplateRef:
        """Creates the email template, or replaces it if one with this name already exists."""
        ...

    @abstractmethod
    def list_email_templates(self) -> list[dict]:
        """[{"name": ..., "external_id": ...}] for every email template the engine holds."""
        ...

    @abstractmethod
    def get_email_template(self, name: str) -> dict | None:
        """{"name", "subject", "html", "text"} for one template, or None."""
        ...

    @abstractmethod
    def upsert_landing_page(
        self, *, name: str, html: str, capture_credentials: bool = True, redirect_url: str = ""
    ) -> ExternalPageRef:
        """
        Creates/replaces a landing page. `capture_passwords` is never accepted as a parameter and is
        always forced off — the platform must never capture real passwords (CLAUDE.md invariant #4),
        so no caller (dashboard or API) can turn it on.
        """
        ...

    @abstractmethod
    def list_landing_pages(self) -> list[dict]:
        """[{"name": ..., "external_id": ...}] for every landing page the engine holds."""
        ...

    @abstractmethod
    def list_sending_profiles(self) -> list[dict]:
        """[{"name": ..., "external_id": ...}] for every sending (SMTP) profile the engine holds."""
        ...

    @abstractmethod
    def get_sending_profile(self, name: str) -> dict | None:
        """
        {"name", "host", "port", "username", "from_address", "ignore_cert_errors", "password_set"}, or None.
        The password is NEVER returned, only whether one is set.
        """
        ...

    @abstractmethod
    def upsert_sending_profile(
        self, *, name: str, host: str, port: int, username: str, password: str, from_address: str,
        ignore_cert_errors: bool,
    ) -> None:
        """Creates/updates the SMTP relay the engine sends through. A blank password keeps the stored one."""
        ...

    @abstractmethod
    def send_test_email(self, *, profile_name: str, to_email: str) -> None:
        """Sends ONE plain test message through the profile. Raises with the relay's message on failure."""
        ...
