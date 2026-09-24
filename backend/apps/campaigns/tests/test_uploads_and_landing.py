"""Image uploads (strictly validated) and the landing-page builder (native form, no script, escaped)."""


import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.campaigns.email_render import render_email
from apps.campaigns.images import STORED_NAME, ImageRejected, save_upload
from apps.campaigns.landing_render import render_landing
from apps.campaigns.models import EmailDraft, EmailImage, LandingDraft
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40


@pytest.fixture(autouse=True)
def image_dir(settings, tmp_path):
    settings.EMAIL_IMAGE_ROOT = str(tmp_path / "images")
    settings.EMAIL_IMAGE_BASE_URL = "https://send.example/i/"


def upload(data, name="logo.png"):
    return SimpleUploadedFile(name, data)


# --- upload validation -------------------------------------------------------------------


def test_a_png_is_stored_under_a_random_name_with_the_verified_extension(tmp_path):
    image = save_upload(upload(PNG, "My Logo.png"), None)

    assert STORED_NAME.match(image.stored_name) and image.stored_name.endswith(".png")
    assert image.stored_name != "My Logo.png" and (tmp_path / "images" / image.stored_name).read_bytes() == PNG
    assert image.public_url == f"https://send.example/i/{image.stored_name}"


def test_the_type_comes_from_the_bytes_not_the_filename():
    assert save_upload(upload(JPG, "photo.png"), None).stored_name.endswith(".jpg")


@pytest.mark.parametrize("data,name", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "x.svg"),
    (b"<?php echo 1;", "shell.png"),
    (b"GIF", "short.gif"),
    (b"MZ" + b"\x00" * 30, "app.exe"),
])
def test_anything_that_is_not_a_png_jpeg_or_gif_is_rejected(data, name):
    with pytest.raises(ImageRejected):
        save_upload(upload(data, name), None)
    assert not EmailImage.objects.exists()


def test_empty_and_oversized_uploads_are_rejected():
    with pytest.raises(ImageRejected):
        save_upload(upload(b""), None)
    with pytest.raises(ImageRejected):
        save_upload(upload(PNG + b"\x00" * 1_000_001), None)


def test_a_path_in_the_filename_cannot_escape_the_image_folder(tmp_path):
    image = save_upload(upload(PNG, "../../etc/passwd.png"), None)

    assert "/" not in image.stored_name and "/" not in image.original_name and ".." not in image.stored_name


# --- rendering ---------------------------------------------------------------------------


def test_the_banner_image_appears_in_the_email():
    assert 'src="https://send.example/i/b.png"' in render_email(layout="corporate", hero_url="https://send.example/i/b.png", body="x")


def test_landing_pages_use_a_native_form_with_a_real_password_input_and_no_script():
    for layout in ("signin", "document", "verify"):
        html = render_landing(layout=layout, heading="Sign in", button_label="Go")
        assert '<form method="post">' in html and 'type="password"' in html and 'name="password"' in html
        assert "<script" not in html.lower() and "onsubmit" not in html.lower() and "fetch(" not in html


def test_landing_password_field_can_be_left_out():
    assert 'type="password"' not in render_landing(layout="signin", heading="Hi", ask_password=False)


def test_landing_page_author_text_is_escaped_and_bad_colours_fall_back():
    html = render_landing(
        layout="verify", brand_name="<b>x</b>", heading="<script>1</script>", subtext="<img src=x onerror=1>",
        username_label="\"><script>2</script>", button_label="<i>go</i>", footer_note="<u>f</u>",
        accent="red;}</style><script>3</script>",
    )

    for raw in ("<script>", "<img src=x", "<b>x</b>", "<i>go</i>", "<u>f</u>"):
        assert raw not in html
    assert "--accent:#0b5fff" in html  # unusable colour replaced, never injected into the stylesheet


# --- views -------------------------------------------------------------------------------


def login(client, django_user_model, group="Campaign Manager"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)


def test_the_composer_uploads_an_image_and_returns_its_id(client, django_user_model):
    login(client, django_user_model)

    ok = client.post(reverse("manage:image-upload"), {"file": upload(PNG)})
    bad = client.post(reverse("manage:image-upload"), {"file": upload(b"<svg onload=1>", "logo.svg")})

    assert ok.status_code == 200 and EmailImage.objects.get().pk == ok.json()["id"]
    assert bad.status_code == 400 and "PNG, JPEG and GIF" in bad.json()["error"] and EmailImage.objects.count() == 1


def test_a_library_image_placed_in_a_message_is_sent_with_its_public_address(client, django_user_model, monkeypatch):
    engine = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: engine)
    login(client, django_user_model)
    image = save_upload(upload(PNG), None)

    client.post(reverse("manage:template-new"), {
        "name": "Pic", "subject": "S", "layout": "minimal",
        "body_html": f'<p>Hi</p><img src="/manage/content/images/{image.pk}/file/" data-image-id="{image.pk}" alt="logo">',
    })

    assert image.public_url in engine.get_email_template("Pic")["html"]


def test_the_landing_builder_pushes_a_page_that_never_captures_passwords(client, django_user_model, monkeypatch):
    engine = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: engine)
    login(client, django_user_model)

    response = client.post(reverse("manage:landing-new"), {
        "name": "Portal", "layout": "signin", "brand_name": "Portal", "accent_color": "#0b5fff", "heading": "Sign in",
        "username_label": "Email", "ask_password": "on", "button_label": "Go",
    })

    draft = LandingDraft.objects.get(name="Portal")
    assert response.url == reverse("manage:landing-edit", args=[draft.pk])
    page = engine.pages["Portal"]
    assert page["capture_passwords"] is False and page["capture_credentials"] is True
    assert page["redirect_url"].endswith("/portal/learn/")  # blank redirect = the teachable-moment page
    assert 'type="password"' in page["html"]


def test_a_bad_accent_colour_is_rejected_by_the_form(client, django_user_model, monkeypatch):
    monkeypatch.setattr("apps.manage.views.get_client", lambda: FakePhishingEngineClient())
    login(client, django_user_model)

    response = client.post(reverse("manage:landing-new"), {
        "name": "X", "layout": "signin", "accent_color": "red;}", "heading": "H", "username_label": "E", "button_label": "Go",
    })

    assert response.status_code == 200 and not LandingDraft.objects.exists()


def test_the_image_library_serves_thumbnails_to_staff_only_and_blocks_removal_while_in_use(client, django_user_model):
    image = save_upload(upload(PNG), None)
    EmailDraft.objects.create(name="Uses it", subject="S", body="x", logo_image=image)

    assert client.get(reverse("manage:image-file", args=[image.pk])).status_code == 302  # not signed in
    login(client, django_user_model)
    served = client.get(reverse("manage:image-file", args=[image.pk]))
    assert served.status_code == 200 and served["Content-Type"] == "image/png"

    client.post(reverse("manage:image-delete", args=[image.pk]))
    assert EmailImage.objects.filter(pk=image.pk).exists()  # still referenced

    EmailDraft.objects.all().delete()
    client.post(reverse("manage:image-delete", args=[image.pk]))
    assert not EmailImage.objects.filter(pk=image.pk).exists()


def test_view_only_roles_cannot_upload(client, django_user_model):
    login(client, django_user_model, group="Report Viewer")

    response = client.post(reverse("manage:images"), {"files": upload(PNG)})

    assert response.status_code == 403 and not EmailImage.objects.exists()


def test_sample_content_includes_landing_pages_and_a_banner(settings, monkeypatch):
    from django.core.management import call_command

    engine = FakePhishingEngineClient()
    monkeypatch.setattr("apps.core.management.commands.seed_sample_content.get_client", lambda: engine)
    settings.DEBUG = False

    call_command("seed_sample_content", verbosity=0)
    call_command("seed_sample_content", verbosity=0)

    assert LandingDraft.objects.count() == 3 and EmailImage.objects.count() == 2  # idempotent
    assert all('type="password"' in p["html"] and p["capture_passwords"] is False for p in engine.pages.values())
    assert sum(1 for d in EmailDraft.objects.all() if "/i/" in d.body_html) == 2  # the two sample banners


def test_previews_inline_uploaded_images_so_they_show_without_the_public_domain(client, django_user_model, monkeypatch):
    engine = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views.get_client", lambda: engine)
    login(client, django_user_model)
    image = save_upload(upload(PNG), None)
    engine.upsert_email_template(name="Pic", subject="S", html=f'<img src="{image.public_url}">')

    html = client.get(reverse("manage:email-preview", args=["Pic"])).content.decode()

    assert "data:image/png;base64," in html and image.public_url not in html


def test_an_image_placed_inside_a_message_counts_as_in_use(client, django_user_model):
    image = save_upload(upload(PNG), None)
    EmailDraft.objects.create(name="Inline", subject="S", body_html=f'<img src="https://send.example/i/{image.stored_name}">')
    login(client, django_user_model)

    client.post(reverse("manage:image-delete", args=[image.pk]))

    assert EmailImage.objects.filter(pk=image.pk).exists()


def test_names_cannot_contain_slashes_and_cannot_change_after_creation(client, django_user_model, monkeypatch):
    monkeypatch.setattr("apps.manage.views.get_client", lambda: FakePhishingEngineClient())
    login(client, django_user_model)

    bad = client.post(reverse("manage:template-new"), {"name": "a/b", "subject": "S", "layout": "minimal", "body_html": "<p>x</p>"})
    assert bad.status_code == 200 and not EmailDraft.objects.exists()

    draft = EmailDraft.objects.create(name="Fixed", subject="S", body_html="<p>x</p>")
    client.post(reverse("manage:template-edit", args=[draft.pk]), {"name": "Renamed", "subject": "S2", "layout": "minimal", "body_html": "<p>x</p>"})
    draft.refresh_from_db()
    assert draft.name == "Fixed" and draft.subject == "S2"
