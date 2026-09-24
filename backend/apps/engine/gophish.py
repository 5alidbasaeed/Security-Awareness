"""
Concrete PhishingEngineClient backed by Gophish's REST API. Nothing outside
this module should know these details — get an instance via factory.py.
"""

from datetime import datetime, timezone

import requests

from .base import (
    EngineEvent,
    ExternalCampaignRef,
    ExternalGroupRef,
    ExternalPageRef,
    ExternalTemplateRef,
    PhishingEngineClient,
    TargetContact,
)

# Gophish's `timeline` entries (and its webhook payloads — same vocabulary)
# use free-text `message` values, not a stable event-type enum. This mapping
# is our translation into the vocabulary used by apps.events.Event.event_type
# (see the database-schema skill) — verify against a live Gophish instance
# during Phase 1 hardening; these strings match Gophish's documented
# behavior as of the version pinned in gophish/Dockerfile but aren't covered
# by an automated contract test here. Shared with apps.events.views (webhook
# ingestion) so both paths agree on event_type for the same underlying event.
GOPHISH_MESSAGE_TO_EVENT_TYPE = {
    "Email Sent": "email_sent",
    "Email Delivered": "email_delivered",
    "Email Opened": "email_opened",
    "Clicked Link": "link_clicked",
    "Submitted Data": "credential_attempt",
    "Email Reported": "phishing_reported",
}


def build_gophish_external_id(*, campaign_id, email, message, time) -> str:
    """
    Shared by the webhook view (real-time) and get_campaign_results
    (reconciliation fallback) so both ingestion paths derive the same
    external_id for the same underlying Gophish event — that's what makes
    the DB's UNIQUE(source, external_id) constraint dedupe correctly no
    matter which path an event arrives through first. Gophish's timeline
    entries have no single stable ID field, hence the composite key.
    """
    return f"{campaign_id}:{email}:{message}:{time}"


class GophishClient(PhishingEngineClient):
    def __init__(self, *, base_url: str, api_key: str, timeout: int = 10):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _find_by_name(self, list_endpoint: str, name: str) -> dict | None:
        """Gophish's REST API has no get-by-name endpoint for groups/pages — list and filter
        client-side. Fine at this project's scale (hundreds to a few thousand employees, not
        thousands of groups/pages)."""
        response = requests.get(f"{self._base_url}{list_endpoint}", headers=self._headers(), timeout=self._timeout)
        response.raise_for_status()
        for item in response.json():
            if item.get("name") == name:
                return item
        return None

    def sync_target_group(self, *, name: str, contacts: list[TargetContact]) -> ExternalGroupRef:
        targets = [
            {"email": c.email, "first_name": c.first_name, "last_name": c.last_name, "position": ""}
            for c in contacts
        ]
        existing = self._find_by_name("/api/groups/", name)
        if existing is None:
            response = requests.post(
                f"{self._base_url}/api/groups/",
                json={"name": name, "targets": targets},
                headers=self._headers(),
                timeout=self._timeout,
            )
        else:
            response = requests.put(
                f"{self._base_url}/api/groups/{existing['id']}",
                json={"id": existing["id"], "name": name, "targets": targets},
                headers=self._headers(),
                timeout=self._timeout,
            )
        response.raise_for_status()
        return ExternalGroupRef(external_id=str(response.json()["id"]))

    def get_landing_page_html(self, page_name: str) -> str:
        page = self._find_by_name("/api/pages/", page_name)
        if page is None:
            raise ValueError(f"No Gophish landing page named {page_name!r}")
        return page.get("html", "")

    # --- Content authoring ---------------------------------------------------------------

    def upsert_email_template(self, *, name: str, subject: str, html: str, text: str = "") -> ExternalTemplateRef:
        body = {"name": name, "subject": subject, "html": html, "text": text}
        existing = self._find_by_name("/api/templates/", name)
        if existing is None:
            response = requests.post(
                f"{self._base_url}/api/templates/", json=body, headers=self._headers(), timeout=self._timeout
            )
        else:
            body["id"] = existing["id"]
            response = requests.put(
                f"{self._base_url}/api/templates/{existing['id']}", json=body, headers=self._headers(),
                timeout=self._timeout,
            )
        response.raise_for_status()
        return ExternalTemplateRef(external_id=str(response.json()["id"]))

    def list_email_templates(self) -> list[dict]:
        response = requests.get(f"{self._base_url}/api/templates/", headers=self._headers(), timeout=self._timeout)
        response.raise_for_status()
        return [{"name": t.get("name"), "external_id": str(t.get("id"))} for t in response.json()]

    def get_email_template(self, name: str) -> dict | None:
        template = self._find_by_name("/api/templates/", name)
        if template is None:
            return None
        return {
            "name": template.get("name"),
            "subject": template.get("subject", ""),
            "html": template.get("html", ""),
            "text": template.get("text", ""),
        }

    def upsert_landing_page(
        self, *, name: str, html: str, capture_credentials: bool = True, redirect_url: str = ""
    ) -> ExternalPageRef:
        # capture_passwords is hard-forced off, never taken from the caller — CLAUDE.md invariant #4.
        body = {
            "name": name,
            "html": html,
            "capture_credentials": bool(capture_credentials),
            "capture_passwords": False,
            "redirect_url": redirect_url,
        }
        existing = self._find_by_name("/api/pages/", name)
        if existing is None:
            response = requests.post(
                f"{self._base_url}/api/pages/", json=body, headers=self._headers(), timeout=self._timeout
            )
        else:
            body["id"] = existing["id"]
            response = requests.put(
                f"{self._base_url}/api/pages/{existing['id']}", json=body, headers=self._headers(),
                timeout=self._timeout,
            )
        response.raise_for_status()
        return ExternalPageRef(external_id=str(response.json()["id"]))

    def get_sending_profile(self, name: str) -> dict | None:
        profile = self._find_by_name("/api/smtp/", name)
        if profile is None:
            return None
        host, _, port = (profile.get("host") or "").rpartition(":")
        return {
            "name": profile.get("name"), "host": host or profile.get("host", ""), "port": int(port) if port.isdigit() else 25,
            "username": profile.get("username", ""), "from_address": profile.get("from_address", ""),
            "ignore_cert_errors": bool(profile.get("ignore_cert_errors")), "password_set": bool(profile.get("password")),
        }

    def upsert_sending_profile(self, *, name, host, port, username, password, from_address, ignore_cert_errors):
        existing = self._find_by_name("/api/smtp/", name)
        body = {
            "name": name, "interface_type": "SMTP", "host": f"{host}:{port}", "username": username,
            # Blank = keep what is stored; the API doesn't reliably do that for us on update.
            "password": password or (existing or {}).get("password", ""),
            "from_address": from_address, "ignore_cert_errors": bool(ignore_cert_errors), "headers": (existing or {}).get("headers", []),
        }
        if existing is None:
            response = requests.post(f"{self._base_url}/api/smtp/", json=body, headers=self._headers(), timeout=self._timeout)
        else:
            body["id"] = existing["id"]
            response = requests.put(
                f"{self._base_url}/api/smtp/{existing['id']}", json=body, headers=self._headers(), timeout=self._timeout,
            )
        response.raise_for_status()

    def send_test_email(self, *, profile_name, to_email, template=None, url=""):
        profile = self._find_by_name("/api/smtp/", profile_name)
        if profile is None:
            raise ValueError(f"No sending profile named {profile_name!r}. Save the settings first.")
        if template is None:
            content = {
                "subject": "Test message from Security Awareness",
                "text": "This is a test message. Your SMTP settings work.",
                "html": "<p>This is a test message. Your SMTP settings work.</p>",
            }
        else:
            content = {**template, "subject": f"[TEST] {template['subject']}"}
        body = {
            "template": {"name": "Test send", **content},
            "first_name": "Test", "last_name": "Recipient", "email": to_email, "position": "", "url": url,
            "smtp": profile,
        }
        # A test send waits on the relay, so allow longer than an ordinary API call.
        response = requests.post(
            f"{self._base_url}/api/util/send_test_email", json=body, headers=self._headers(), timeout=max(self._timeout, 30),
        )
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not response.ok or result.get("success") is False:
            raise RuntimeError(result.get("message") or f"the engine answered {response.status_code}")

    def import_site(self, url):
        response = requests.post(
            f"{self._base_url}/api/import/site", json={"url": url, "include_resources": False},
            headers=self._headers(), timeout=max(self._timeout, 30),
        )
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not response.ok or not result.get("html"):
            raise RuntimeError(result.get("message") or f"the engine could not fetch that page ({response.status_code})")
        return result["html"]

    def list_landing_pages(self) -> list[dict]:
        response = requests.get(f"{self._base_url}/api/pages/", headers=self._headers(), timeout=self._timeout)
        response.raise_for_status()
        return [{"name": p.get("name"), "external_id": str(p.get("id"))} for p in response.json()]

    def list_sending_profiles(self) -> list[dict]:
        response = requests.get(f"{self._base_url}/api/smtp/", headers=self._headers(), timeout=self._timeout)
        response.raise_for_status()
        return [{"name": s.get("name"), "external_id": str(s.get("id"))} for s in response.json()]

    def create_campaign(
        self,
        *,
        name: str,
        template_id: str,
        target_group_id: str,
        send_profile_id: str,
        page_id: str,
        url: str,
        launch_immediately: bool = True,
    ) -> ExternalCampaignRef:
        """
        Gophish creates and (per `launch_date`) sends a campaign in a single
        call — there is no separate "create, then launch later" REST
        endpoint. `launch_immediately=True` sets `launch_date` to now.
        """
        launch_date = datetime.now(timezone.utc).isoformat() if launch_immediately else None
        payload = {
            "name": name,
            "template": {"name": template_id},
            "page": {"name": page_id},
            "url": url,
            "smtp": {"name": send_profile_id},
            "groups": [{"name": target_group_id}],
        }
        if launch_date:
            payload["launch_date"] = launch_date

        response = requests.post(
            f"{self._base_url}/api/campaigns/", json=payload, headers=self._headers(), timeout=self._timeout
        )
        response.raise_for_status()
        return ExternalCampaignRef(external_id=str(response.json()["id"]))

    def find_campaign(self, name: str) -> ExternalCampaignRef | None:
        # /summary, not /api/campaigns/: the full listing embeds every result and timeline entry.
        response = requests.get(
            f"{self._base_url}/api/campaigns/summary", headers=self._headers(), timeout=self._timeout
        )
        response.raise_for_status()
        for item in response.json().get("campaigns") or []:
            if item.get("name") == name:
                return ExternalCampaignRef(external_id=str(item["id"]))
        return None

    def launch_campaign(self, external_campaign_id: str) -> None:
        raise NotImplementedError(
            "Gophish has no REST endpoint to launch an already-created campaign — "
            "campaigns launch at creation time via create_campaign()'s launch_date. "
            "This method exists to satisfy the adapter interface for engines that do "
            "support deferred launch; it's intentionally unimplemented for Gophish."
        )

    def get_campaign_results(self, external_campaign_id: str) -> list[EngineEvent]:
        response = requests.get(
            f"{self._base_url}/api/campaigns/{external_campaign_id}/results",
            headers=self._headers(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()

        events = []
        for entry in data.get("timeline", []):
            event_type = GOPHISH_MESSAGE_TO_EVENT_TYPE.get(entry.get("message"))
            if event_type is None:
                continue  # unrecognized message — skip rather than guess at a mapping
            external_id = build_gophish_external_id(
                campaign_id=external_campaign_id,
                email=entry.get("email"),
                message=entry.get("message"),
                time=entry.get("time"),
            )
            events.append(
                EngineEvent(
                    external_id=external_id,
                    event_type=event_type,
                    occurred_at=entry.get("time"),
                    raw=entry,
                )
            )
        return events
