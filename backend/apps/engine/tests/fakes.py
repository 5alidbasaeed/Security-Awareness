"""
Use this in any test that would otherwise need a real Gophish instance — see
the testing-conventions skill: never hit real Gophish from a unit test.
"""

from apps.engine.base import (
    EngineEvent,
    ExternalCampaignRef,
    ExternalGroupRef,
    ExternalPageRef,
    ExternalTemplateRef,
    PhishingEngineClient,
)


class FakePhishingEngineClient(PhishingEngineClient):
    def __init__(self):
        self.created_campaigns = []
        self.launched_campaign_ids = []
        self.results_by_campaign_id: dict[str, list[EngineEvent]] = {}
        self.synced_groups: dict[str, list] = {}  # name -> contacts, last sync wins
        self.landing_pages: dict[str, str] = {}  # name -> html, populate in a test as needed
        self.email_templates: dict[str, dict] = {}  # name -> {subject, html, text}
        self.pages: dict[str, dict] = {}  # name -> {html, capture_credentials, redirect_url}
        self.sending_profiles: dict[str, str] = {"default": "1"}  # name -> id
        self.profile_settings: dict[str, dict] = {}
        self.test_emails: list[tuple] = []
        self.last_test: dict = {}  # template/url of the most recent test send
        self.site_pages: dict[str, str] = {}  # url -> html served by import_site, populate in a test as needed
        self._next_campaign_id = 1
        self._next_group_id = 1
        self._next_content_id = 1

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
        if page_name in self.pages:
            return self.pages[page_name]["html"]
        if page_name not in self.landing_pages:
            raise ValueError(f"No Gophish landing page named {page_name!r}")
        return self.landing_pages[page_name]

    def _content_id(self):
        cid = str(self._next_content_id)
        self._next_content_id += 1
        return cid

    def upsert_email_template(self, *, name, subject, html, text=""):
        self.email_templates[name] = {"subject": subject, "html": html, "text": text}
        return ExternalTemplateRef(external_id=self._content_id())

    def list_email_templates(self):
        return [{"name": n, "external_id": "t"} for n in sorted(self.email_templates)]

    def get_email_template(self, name):
        t = self.email_templates.get(name)
        return {"name": name, **t} if t else None

    def upsert_landing_page(self, *, name, html, capture_credentials=True, redirect_url=""):
        # Mirror the real adapter: passwords are never captured, whatever the caller asks.
        self.pages[name] = {
            "html": html, "capture_credentials": bool(capture_credentials),
            "capture_passwords": False, "redirect_url": redirect_url,
        }
        return ExternalPageRef(external_id=self._content_id())

    def list_landing_pages(self):
        return [{"name": n, "external_id": "p"} for n in sorted(self.pages)]

    def get_sending_profile(self, name):
        profile = self.profile_settings.get(name)
        return None if profile is None else {k: v for k, v in profile.items() if k != "password"} | {"name": name, "password_set": bool(profile.get("password"))}

    def upsert_sending_profile(self, *, name, host, port, username, password, from_address, ignore_cert_errors):
        old = self.profile_settings.get(name, {})
        self.profile_settings[name] = {
            "host": host, "port": port, "username": username, "password": password or old.get("password", ""),
            "from_address": from_address, "ignore_cert_errors": ignore_cert_errors,
        }
        self.sending_profiles.setdefault(name, self._content_id())

    def send_test_email(self, *, profile_name, to_email, template=None, url=""):
        if profile_name not in self.profile_settings:
            raise ValueError(f"No sending profile named {profile_name!r}.")
        if getattr(self, "test_email_error", None):
            raise RuntimeError(self.test_email_error)
        self.test_emails.append((profile_name, to_email))
        self.last_test = {"template": template, "url": url}

    def import_site(self, url):
        if url not in self.site_pages:
            raise RuntimeError("the engine could not fetch that page (404)")
        return self.site_pages[url]

    def list_sending_profiles(self):
        return [{"name": n, "external_id": i} for n, i in sorted(self.sending_profiles.items())]
