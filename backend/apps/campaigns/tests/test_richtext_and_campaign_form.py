"""The composer's sanitiser (nothing the browser sends is trusted) and the dropdown-driven campaign form."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.models import Campaign, EmailDraft, EmailImage, LandingDraft, SmartGroup
from apps.campaigns.richtext import sanitize_email_html, to_editor_html
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.tests.factories import DepartmentFactory

pytestmark = pytest.mark.django_db


# --- sanitiser ---------------------------------------------------------------------------


def test_basic_formatting_survives():
    html = sanitize_email_html("<p>Hi <b>there</b></p><ul><li>one</li></ul><h2>Head</h2>")

    assert html == "<p>Hi <b>there</b></p><ul><li>one</li></ul><h2>Head</h2>"


@pytest.mark.parametrize("dirty", [
    "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", '<iframe src="https://evil.example"></iframe>',
    '<p onclick="x()">t</p>', "<style>p{}</style>", '<a href="javascript:alert(1)">x</a>',
    '<form action="https://evil.example"><input></form>', '<svg onload=alert(1)>', "<object data=x></object>",
])
def test_active_content_is_removed(dirty):
    html = sanitize_email_html(dirty).lower()

    for bad in ("<script", "onerror", "onclick", "<iframe", "<style", "javascript:", "<form", "<svg", "<object", "evil.example"):
        assert bad not in html


def test_every_link_becomes_the_tracked_link():
    html = sanitize_email_html('<a href="https://phish-me.example/login">Click</a> and <a href="mailto:a@b.c">mail</a>')

    assert html.count('href="{{.URL}}"') == 2 and "phish-me" not in html and "mailto" not in html


def test_a_button_keeps_its_look_but_still_points_at_the_tracked_link():
    html = sanitize_email_html('<a href="https://x.example" data-button="1" style="color:red">Open</a>')

    assert 'data-button="1"' in html and "background:#0b5fff" in html and "color:red" not in html and "x.example" not in html


def test_only_library_images_survive_and_get_their_public_address(settings):
    settings.EMAIL_IMAGE_BASE_URL = "https://send.example/i/"
    image = EmailImage.objects.create(stored_name="a" * 32 + ".png", original_name="l.png", size=1)

    html = sanitize_email_html(
        f'<img data-image-id="{image.pk}" src="https://tracker.example/x.png"><img src="data:image/png;base64,AAAA">'
        '<img src="https://tracker.example/y.png"><img data-image-id="99999">'
    )

    assert html.count("<img") == 1 and f"https://send.example/i/{image.stored_name}" in html and "tracker.example" not in html


def test_merge_fields_are_kept_and_any_other_template_syntax_is_defused():
    html = sanitize_email_html("<p>Hi {{.FirstName}} {{ .LastName }} {{.URL}} {{template \"x\"}} {{range .}}</p>")

    assert "{{.FirstName}}" in html and "{{.LastName}}" in html
    assert "{{template" not in html and "{{range" not in html and "{ {template" in html


def test_unbalanced_and_junk_markup_still_produces_balanced_html():
    html = sanitize_email_html("<p><b>unclosed <i>text</p></u>plain")

    assert html.count("<b>") == html.count("</b>") and html.count("<i>") == html.count("</i>")


def test_round_trip_through_the_editor_keeps_library_images(settings):
    settings.EMAIL_IMAGE_BASE_URL = "https://send.example/i/"
    image = EmailImage.objects.create(stored_name="b" * 32 + ".png", original_name="l.png", size=1)
    stored = sanitize_email_html(f'<p>x</p><img data-image-id="{image.pk}" alt="l">')

    editor = to_editor_html(stored)

    assert f'data-image-id="{image.pk}"' in editor and "send.example" not in editor and "style=" not in editor
    assert sanitize_email_html(editor) == stored


# --- campaign form -----------------------------------------------------------------------


def login(client, django_user_model, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="u", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


def content():
    EmailDraft.objects.create(name="E1", subject="S", body_html="<p>x</p>")
    LandingDraft.objects.create(name="L1")


def test_a_campaign_is_made_from_dropdown_choices(client, django_user_model):
    login(client, django_user_model)
    content()
    dept = DepartmentFactory()

    client.post(reverse("manage:campaign-new"), {"name": "C", "email": "E1", "landing_page": "L1", "audience": f"dept:{dept.pk}"})

    campaign = Campaign.objects.get()
    assert (campaign.template_name, campaign.landing_page_name, campaign.target_department) == ("E1", "L1", dept)
    assert campaign.landing_page_url and campaign.status == Campaign.Status.DRAFT


def test_everyone_and_smart_group_audiences_are_saved(client, django_user_model):
    login(client, django_user_model)
    content()
    group = SmartGroup.objects.create(name="Repeat", rule=SmartGroup.Rule.REPEAT_CLICKERS)

    client.post(reverse("manage:campaign-new"), {"name": "A", "email": "E1", "landing_page": "L1", "audience": "all"})
    client.post(reverse("manage:campaign-new"), {"name": "B", "email": "E1", "landing_page": "L1", "audience": f"smart:{group.pk}"})

    everyone, smart = Campaign.objects.get(name="A"), Campaign.objects.get(name="B")
    assert everyone.target_smart_group.rule == SmartGroup.Rule.ALL and smart.target_smart_group == group


def test_an_email_that_is_not_in_the_library_is_rejected(client, django_user_model):
    login(client, django_user_model)
    content()

    response = client.post(reverse("manage:campaign-new"), {"name": "C", "email": "Nope", "landing_page": "L1", "audience": "all"})

    assert response.status_code == 200 and not Campaign.objects.exists()


def test_a_department_manager_can_only_pick_their_own_departments(client, django_user_model):
    user = login(client, django_user_model, "Department Manager")
    content()
    mine, other = DepartmentFactory(), DepartmentFactory()
    mine.managers.add(user)
    campaign = Campaign.objects.create(name="C", template_name="E1", landing_page_name="L1", landing_page_url="http://x", target_department=mine)
    url = reverse("manage:campaign-edit", args=[campaign.pk])

    for audience in (f"dept:{other.pk}", "all"):
        client.post(url, {"name": "C", "email": "E1", "landing_page": "L1", "audience": audience})
        campaign.refresh_from_db()
        assert campaign.target_department == mine and campaign.target_smart_group is None  # rejected

    page = client.get(url).content.decode()
    assert f'value="dept:{mine.pk}"' in page and f'value="dept:{other.pk}"' not in page and 'value="all"' not in page


def test_editing_shows_the_current_choices_and_resets_approval_when_content_changes(client, django_user_model):
    login(client, django_user_model)
    content()
    dept = DepartmentFactory()
    campaign = Campaign.objects.create(name="C", template_name="E1", landing_page_name="L1", landing_page_url="http://x",
                                       target_department=dept, status=Campaign.Status.APPROVED)
    EmailDraft.objects.create(name="E2", subject="S", body_html="<p>y</p>")

    page = client.get(reverse("manage:campaign-edit", args=[campaign.pk]))
    assert b'value="E1" selected' in page.content

    client.post(reverse("manage:campaign-edit", args=[campaign.pk]), {"name": "C", "email": "E2", "landing_page": "L1", "audience": f"dept:{dept.pk}"})
    campaign.refresh_from_db()
    assert campaign.template_name == "E2" and campaign.status == Campaign.Status.DRAFT


# --- "also send a copy to" ---------------------------------------------------------------


def test_copy_addresses_are_validated_deduplicated_and_saved(client, django_user_model):
    login(client, django_user_model)
    content()

    client.post(reverse("manage:campaign-new"), {
        "name": "C", "email": "E1", "landing_page": "L1", "audience": "all",
        "copy_to": "sec@corp.example; Sec@corp.example, boss@corp.example",
    })

    assert Campaign.objects.get().copy_addresses() == ["sec@corp.example", "boss@corp.example"]


def test_a_bad_address_or_an_employee_is_refused_as_a_copy_recipient(client, django_user_model):
    from apps.employees.tests.factories import EmployeeFactory

    login(client, django_user_model)
    content()
    EmployeeFactory(email="worker@corp.example")

    for bad in ("not-an-address", "worker@corp.example"):
        response = client.post(reverse("manage:campaign-new"), {
            "name": "C", "email": "E1", "landing_page": "L1", "audience": "all", "copy_to": bad,
        })
        assert response.status_code == 200 and not Campaign.objects.exists()


def test_launch_sends_a_copy_to_non_employees_and_never_to_an_exempt_employee():
    from apps.campaigns.services import launch_campaign
    from apps.employees.tests.factories import EmployeeFactory
    from apps.engine.tests.fakes import FakePhishingEngineClient

    dept = DepartmentFactory()
    EmployeeFactory(department=dept, email="target@corp.example")
    EmployeeFactory(department=dept, email="exempt@corp.example", is_exempt=True)
    campaign = Campaign.objects.create(
        name="C", template_name="E1", landing_page_name="L1", landing_page_url="http://x", target_department=dept,
        status=Campaign.Status.APPROVED, copy_to="sec@corp.example, exempt@corp.example, target@corp.example",
    )
    engine = FakePhishingEngineClient()

    launch_campaign(campaign, actor=None, client=engine)

    emails = sorted(c.email for group in engine.synced_groups.values() for c in group)
    assert emails == ["sec@corp.example", "target@corp.example"]  # copy added once; exempt employee never emailed


def test_choosing_everyone_twice_reuses_one_group_even_if_duplicates_exist(client, django_user_model):
    login(client, django_user_model)
    content()
    SmartGroup.objects.create(name="Everyone", rule=SmartGroup.Rule.ALL)
    SmartGroup.objects.create(name="Everyone 2", rule=SmartGroup.Rule.ALL)

    response = client.post(reverse("manage:campaign-new"), {"name": "C", "email": "E1", "landing_page": "L1", "audience": "all"})

    assert response.status_code == 302 and Campaign.objects.get().target_smart_group.rule == SmartGroup.Rule.ALL
