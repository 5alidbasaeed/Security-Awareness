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
    def launch_campaign(self, external_campaign_id: str) -> None: ...

    @abstractmethod
    def get_campaign_results(self, external_campaign_id: str) -> list[EngineEvent]: ...

    @abstractmethod
    def get_landing_page_html(self, page_name: str) -> str:
        """Fetches a landing page's raw HTML by name, for the dry-run/preview feature."""
        ...
