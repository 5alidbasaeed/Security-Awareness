"""
Regression tests from the Phase 6 branch review: POST /portal/login is public and emails
whoever it is asked about, so without limits anyone could use it to flood employees'
inboxes (and tie up web workers on the SMTP relay).
"""

import pytest
from django.core import mail
from django.urls import reverse

from apps.employees.tests.factories import EmployeeFactory

pytestmark = pytest.mark.django_db

LOGIN = "portal:login"


def request_link(client, email, ip="203.0.113.7"):
    return client.post(reverse(LOGIN), {"email": email}, REMOTE_ADDR=ip)


def test_only_a_few_links_per_address_per_window(client, settings):
    settings.PORTAL_LINK_EMAIL_LIMIT = 3
    EmployeeFactory(email="victim@corp.example")

    responses = [request_link(client, "victim@corp.example") for _ in range(6)]

    assert len(mail.outbox) == 3  # the rest are silently dropped
    assert len({r.content for r in responses}) == 1  # ...and the answer never changes (no oracle)
    assert all(r.status_code == 200 for r in responses)


def test_address_matching_ignores_case_so_it_cannot_be_dodged(client, settings):
    settings.PORTAL_LINK_EMAIL_LIMIT = 2
    EmployeeFactory(email="victim@corp.example")

    for variant in ["victim@corp.example", "VICTIM@corp.example", "Victim@Corp.Example", "victim@CORP.example"]:
        request_link(client, variant)

    assert len(mail.outbox) == 2


def test_one_address_being_throttled_does_not_block_other_people(client, settings):
    settings.PORTAL_LINK_EMAIL_LIMIT = 1
    EmployeeFactory(email="a@corp.example"), EmployeeFactory(email="b@corp.example")

    request_link(client, "a@corp.example")
    request_link(client, "a@corp.example")
    request_link(client, "b@corp.example")

    assert sorted(m.to[0] for m in mail.outbox) == ["a@corp.example", "b@corp.example"]


def test_one_client_cannot_spray_many_addresses(client, settings):
    settings.PORTAL_LINK_IP_LIMIT = 5
    for n in range(10):
        EmployeeFactory(email=f"person{n}@corp.example")

    for n in range(10):
        request_link(client, f"person{n}@corp.example", ip="198.51.100.9")

    assert len(mail.outbox) == 5


def test_a_different_client_is_not_affected_by_anothers_limit(client, settings):
    settings.PORTAL_LINK_IP_LIMIT = 1
    EmployeeFactory(email="a@corp.example"), EmployeeFactory(email="b@corp.example")

    request_link(client, "a@corp.example", ip="198.51.100.1")
    request_link(client, "b@corp.example", ip="198.51.100.2")

    assert len(mail.outbox) == 2


def test_unknown_addresses_count_too_so_probing_is_limited(client, settings):
    settings.PORTAL_LINK_IP_LIMIT = 3

    for n in range(6):
        request_link(client, f"ghost{n}@corp.example", ip="198.51.100.5")
    EmployeeFactory(email="real@corp.example")
    request_link(client, "real@corp.example", ip="198.51.100.5")

    assert len(mail.outbox) == 0  # the same client is already over its limit


def test_the_throttle_state_is_not_keyed_on_a_readable_address(client, settings):
    from django.core.cache import cache

    EmployeeFactory(email="private@corp.example")
    request_link(client, "private@corp.example")

    keys = str(getattr(cache, "_cache", {}).keys())
    assert "private@corp.example" not in keys  # only a hash is stored, never the address


def test_email_sending_cannot_hang_a_web_worker_forever(settings):
    assert settings.EMAIL_TIMEOUT and settings.EMAIL_TIMEOUT <= 30
