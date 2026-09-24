"""
Use this in any test that would otherwise need a real Gophish instance — see
the testing-conventions skill: never hit real Gophish from a unit test.
"""

from apps.engine.base import EngineEvent, ExternalCampaignRef, ExternalGroupRef, PhishingEngineClient


class FakePhishingEngineClient(PhishingEngineClient):
    def __init__(self):
        self.created_campaigns = []
        self.launched_campaign_ids = []
        self.results_by_campaign_id: dict[str, list[EngineEvent]] = {}
        self.synced_groups: dict[str, list] = {}  # name -> contacts, last sync wins
        self.landing_pages: dict[str, str] = {}  # name -> html, populate in a test as needed
        self._next_campaign_id = 1
        self._next_group_id = 1

    def sync_target_group(self, *, name, contacts):
        self.synced_groups[name] = list(contacts)
        group_id = str(self._next_group_id)
        self._next_group_id += 1
        return ExternalGroupRef(external_id=group_id)

    def create_campaign(self, *, name, template_id, target_group_id, send_profile_id, page_id, url, **kwargs):
        external_id = str(self._next_campaign_id)
        self._next_campaign_id += 1
        self.created_campaigns.append(
            {
                "external_id": external_id,
                "name": name,
                "template_id": template_id,
                "target_group_id": target_group_id,
                "send_profile_id": send_profile_id,
                "page_id": page_id,
                "url": url,
            }
        )
        return ExternalCampaignRef(external_id=external_id)

    def find_campaign(self, name):
        for created in self.created_campaigns:
            if created["name"] == name:
                return ExternalCampaignRef(external_id=created["external_id"])
        return None

    def launch_campaign(self, external_campaign_id):
        self.launched_campaign_ids.append(external_campaign_id)

    def get_campaign_results(self, external_campaign_id):
        return self.results_by_campaign_id.get(external_campaign_id, [])

    def get_landing_page_html(self, page_name):
        if page_name not in self.landing_pages:
            raise ValueError(f"No Gophish landing page named {page_name!r}")
        return self.landing_pages[page_name]
