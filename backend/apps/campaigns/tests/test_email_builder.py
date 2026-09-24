"""The email builder compiles author input to email-safe HTML: escaped, tracked button, engine push on save."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.email_render import render_email
from apps.campaigns.models import EmailDraft
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def test_the_button_is_the_tracked_link_and_the_open_pixel_is_appended():
    html = render_email(layout="corporate", heading="Hi", body="Body", button_label="Open it")

    assert 'href="{{.URL}}"' in html and "Open it" in html
    assert html.endswith("{{.Tracker}}")


def test_no_button_label_means_no_link():
    assert "{{.URL}}" not in render_email(layout="minimal", body="Just a note")


def test_author_text_is_escaped_in_every_field():
    html = render_email(
        layout="alert", logo_url="https://cdn.example/logo.png", brand_name="<b>x</b>", heading="<script>1</script>",
        body="<img src=x onerror=1>\n\n- <i>bullet</i>", button_label='"><script>2</script>', footer_note="<u>f</u>",
    )

    for raw in ("<script>", "<img src=x", "<b>x</b>", "<i>bullet", "<u>f</u>"):
        assert raw not in html
    assert "&lt;script&gt;" in html


def test_merge_fields_survive_so_each_person_is_addressed_by_name():
    assert "Hi {{.FirstName}}," in render_email(layout="corporate", body="Hi {{.FirstName}},")


def test_layouts_differ_and_an_unknown_layout_falls_back():
    corporate = render_email(layout="corporate", body="x")
    alert = render_email(layout="alert", body="x")

    assert corporate != alert and "#c62828" in alert and "#0b5fff" in corporate
    assert render_email(layout="nonsense", body="x") == corporate


def test_a_logo_shows_in_the_header_and_in_place_of_the_brand_text():
    html = render_email(layout="corporate", logo_url="https://cdn.example/logo.png", brand_name="IT Desk", body="x")

    assert 'src="https://cdn.example/logo.png"' in html and "IT Desk" not in html


def login(client, django_user_model, group):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)


def test_saving_in_the_builder_pushes_the_compiled_email_to_the_engine(client, django_user_model, monkeypatch):
    engine = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: engine)
    login(client, django_user_model, "Campaign Manager")

    response = client.post(reverse("manage:template-new"), {
        "name": "Test email", "subject": "Hello", "layout": "corporate", "brand_name": "IT",
        "body_html": '<p>Hi {{.FirstName}}</p><p><a href="https://evil.example" data-button="1">Go</a></p>',
    })

    draft = EmailDraft.objects.get(name="Test email")
    assert response.url == reverse("manage:template-edit", args=[draft.pk])  # back to the editor, preview beside it
    stored = engine.get_email_template("Test email")
    assert stored["subject"] == "Hello" and 'href="{{.URL}}"' in stored["html"] and "evil.example" not in stored["html"]
    assert "Go: {{.URL}}" in stored["text"]


def test_editing_updates_the_same_template_rather_than_creating_another(client, django_user_model, monkeypatch):
    engine = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: engine)
    login(client, django_user_model, "Campaign Manager")
    draft = EmailDraft.objects.create(name="Mail", subject="One", body="x")

    client.post(reverse("manage:template-edit", args=[draft.pk]), {
        "name": "Mail", "subject": "Two", "layout": "alert", "brand_name": "", "body_html": "<p>x</p>",
    })

    assert EmailDraft.objects.count() == 1 and EmailDraft.objects.get().subject == "Two"
    assert engine.get_email_template("Mail")["subject"] == "Two"


def test_view_only_roles_cannot_use_the_builder(client, django_user_model, monkeypatch):
    monkeypatch.setattr("apps.manage.views.get_client", lambda: FakePhishingEngineClient())
    login(client, django_user_model, "Report Viewer")

    response = client.post(reverse("manage:template-new"), {"name": "x", "subject": "x", "layout": "corporate", "body_html": "<p>x</p>"})

    assert response.status_code == 403 and not EmailDraft.objects.exists()
