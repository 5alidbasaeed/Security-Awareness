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
