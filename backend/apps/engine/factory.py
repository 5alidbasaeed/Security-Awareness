"""
The one place outside apps.engine itself that anything is allowed to import
to get a PhishingEngineClient instance. See CLAUDE.md invariant #1.
"""

from django.conf import settings

from .base import PhishingEngineClient
from .gophish import GophishClient


def get_client() -> PhishingEngineClient:
    return GophishClient(base_url=settings.GOPHISH_API_URL, api_key=settings.GOPHISH_API_KEY)
