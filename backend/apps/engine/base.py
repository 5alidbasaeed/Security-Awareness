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
class EngineEvent:
    external_id: str
    event_type: str
    occurred_at: str
    raw: dict


class PhishingEngineClient(ABC):
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
