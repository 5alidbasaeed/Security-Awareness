"""
Use this in any test that would otherwise need a real Gophish instance — see
the testing-conventions skill: never hit real Gophish from a unit test.
"""

from apps.engine.base import EngineEvent, ExternalCampaignRef, PhishingEngineClient


class FakePhishingEngineClient(PhishingEngineClient):
    def __init__(self):
        self.created_campaigns = []
        self.launched_campaign_ids = []
        self.results_by_campaign_id: dict[str, list[EngineEvent]] = {}
        self._next_id = 1

    def create_campaign(self, *, name, template_id, target_group_id, send_profile_id, url, **kwargs):
        external_id = str(self._next_id)
        self._next_id += 1
        self.created_campaigns.append(
            {
                "external_id": external_id,
                "name": name,
                "template_id": template_id,
                "target_group_id": target_group_id,
                "send_profile_id": send_profile_id,
                "url": url,
            }
        )
        return ExternalCampaignRef(external_id=external_id)

    def launch_campaign(self, external_campaign_id):
        self.launched_campaign_ids.append(external_campaign_id)

    def get_campaign_results(self, external_campaign_id):
        return self.results_by_campaign_id.get(external_campaign_id, [])
