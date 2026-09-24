import hashlib
import hmac
import json

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import EmployeeFactory
from apps.events.models import Event

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _webhook_secret(settings):
    # Don't depend on the deployment's .env — the view rejects everything when the secret is unset.
    settings.GOPHISH_WEBHOOK_SECRET = "test-webhook-secret"


def _sign(body: bytes) -> str:
    digest = hmac.new(settings.GOPHISH_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _payload(campaign, employee, **overrides):
    data = {
        "campaign_id": campaign.gophish_campaign_id,
        "email": employee.email,
        "message": "Clicked Link",
        "time": timezone.now().isoformat(),
        "details": {},
    }
    data.update(overrides)
    return data


def _post(client, payload, signature=None):
    body = json.dumps(payload).encode()
    headers = {"HTTP_X_GOPHISH_SIGNATURE": signature if signature is not None else _sign(body)}
    return client.generic("POST", reverse("events:gophish-webhook"), data=body, content_type="application/json", **headers)


def test_duplicate_delivery_creates_one_event(client):
    campaign = CampaignFactory(gophish_campaign_id="42")
    employee = EmployeeFactory()
    payload = _payload(campaign, employee)

    response1 = _post(client, payload)
    response2 = _post(client, payload)  # same external_id — simulates Gophish's at-least-once redelivery

    assert response1.status_code == 200
    assert response2.status_code == 200
    assert Event.objects.count() == 1


def test_invalid_signature_rejected(client):
    campaign = CampaignFactory(gophish_campaign_id="42")
    employee = EmployeeFactory()
    payload = _payload(campaign, employee)

    response = _post(client, payload, signature="sha256=not-the-real-signature")

    assert response.status_code == 403
    assert Event.objects.count() == 0


def test_password_like_field_stripped_before_storage(client):
    campaign = CampaignFactory(gophish_campaign_id="42")
    employee = EmployeeFactory()
    payload = _payload(
        campaign,
        employee,
        message="Submitted Data",
        details={"payload": {"username": ["bob"], "password": ["hunter2"]}},
    )

    response = _post(client, payload)

    assert response.status_code == 200
    event = Event.objects.get()
    assert "hunter2" not in json.dumps(event.metadata)
    assert "password" not in event.metadata["payload"]
    assert event.metadata["payload"]["username"] == ["bob"]


@pytest.mark.parametrize("payload", [[1, 2, 3], "a string", 42, None])
def test_non_object_json_body_is_a_400_not_a_500(client, payload):
    response = _post(client, payload)

    assert response.status_code == 400


@pytest.mark.parametrize("time", ["2026-13-45T10:00:00Z", 1727170000, ["2026-09-24T10:00:00Z"]])
def test_malformed_time_is_acked_and_skipped_not_a_500(client, time):
    # parse_datetime raises (not returns None) for an out-of-range date or a non-string; a 500
    # would make Gophish redeliver the same bad event forever.
    campaign = CampaignFactory(gophish_campaign_id="42")
    employee = EmployeeFactory()

    response = _post(client, _payload(campaign, employee, time=time))

    assert response.status_code == 200
    assert Event.objects.count() == 0


def test_non_string_message_is_acked_and_ignored(client):
    campaign = CampaignFactory(gophish_campaign_id="42")
    employee = EmployeeFactory()

    response = _post(client, _payload(campaign, employee, message=["Clicked Link"]))

    assert response.status_code == 200
    assert Event.objects.count() == 0


def test_json_string_details_are_sanitized(client):
    # Gophish sends `details` as a JSON-encoded string, not an object.
    campaign = CampaignFactory(gophish_campaign_id="42")
    employee = EmployeeFactory()
    details = json.dumps({"payload": {"username": ["bob"], "password": ["hunter2"]}})

    response = _post(client, _payload(campaign, employee, message="Submitted Data", details=details))

    assert response.status_code == 200
    assert "hunter2" not in json.dumps(Event.objects.get().metadata)
