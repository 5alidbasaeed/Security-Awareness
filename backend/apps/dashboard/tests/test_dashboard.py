import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.tests.factories import make_sample_data
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring import analytics
from apps.risk_scoring.services import compute_and_store_snapshot

pytestmark = pytest.mark.django_db

NAMES = ["overview", "campaigns", "employees", "departments", "training"]


def _login(client, django_user_model, group="Report Viewer", username="u"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


# --- access ------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_anonymous_users_go_to_the_dashboard_login(client, name):
    response = client.get(reverse(f"dashboard:{name}"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("dashboard:login"))


@pytest.mark.parametrize("group", ["Training Manager"])
def test_staff_without_risk_permission_get_a_friendly_403(client, django_user_model, group):
    _login(client, django_user_model, group)

    response = client.get(reverse("dashboard:overview"))

    assert response.status_code == 403
    assert b"don" in response.content and b"have access" in response.content


def test_login_page_renders_and_signs_in(client, django_user_model):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="ana", password="s3cret-pass", is_staff=True)
    user.groups.add(Group.objects.get(name="Report Viewer"))

    assert client.get(reverse("dashboard:login")).status_code == 200
    response = client.post(reverse("dashboard:login"), {"username": "ana", "password": "s3cret-pass"})

    assert response.status_code == 302
    assert response["Location"] == reverse("dashboard:overview")


def test_logout_requires_post_and_signs_out(client, django_user_model):
    _login(client, django_user_model)

    assert client.get(reverse("dashboard:logout")).status_code == 405
    assert client.post(reverse("dashboard:logout")).status_code == 302
    assert client.get(reverse("dashboard:overview")).status_code == 302


# --- every page renders, empty and populated ---------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_every_page_renders_with_no_data(client, django_user_model, name):
    _login(client, django_user_model)

    assert client.get(reverse(f"dashboard:{name}")).status_code == 200


@pytest.mark.parametrize("group", ["Security Admin", "Report Viewer", "Campaign Manager", "Department Manager"])
def test_every_page_renders_with_data_for_each_role(client, django_user_model, group):
    data = make_sample_data()
    user = _login(client, django_user_model, group)
    data["department"].managers.add(user)
    employee, campaign = data["objects"][1], data["objects"][2]
    urls = [reverse(f"dashboard:{n}") for n in NAMES] + [
        reverse("dashboard:employee-detail", args=[employee.pk]),
        reverse("dashboard:campaign-detail", args=[campaign.pk]),
        reverse("dashboard:department-detail", args=[data["department"].pk]),
    ]

    for url in urls:
        assert client.get(url).status_code == 200, url


def test_overview_shows_the_real_numbers(client, django_user_model):
    department = DepartmentFactory(name="Finance")
    a, b = EmployeeFactory(department=department, full_name="Ada Lovelace"), EmployeeFactory(department=department)
    EventFactory(employee=a, event_type=Event.EventType.CREDENTIAL_ATTEMPT)
    compute_and_store_snapshot(a)
    compute_and_store_snapshot(b)
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:overview")).content.decode()

    assert "25.0" in body  # (50 + 0) / 2
    assert "Ada Lovelace" in body and "Finance" in body


def test_risk_badges_always_carry_a_text_label(client, django_user_model):
    employee = EmployeeFactory(department=DepartmentFactory())
    EventFactory(employee=employee, event_type=Event.EventType.CREDENTIAL_ATTEMPT)
    compute_and_store_snapshot(employee)
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:employees")).content.decode()

    assert "badge--medium" in body and ">Medium<" in body  # 50 is medium: colour never stands alone


def test_risk_level_boundaries_come_from_the_backend_constants():
    assert analytics.risk_level(None) == "unscored"
    assert analytics.risk_level(analytics.MEDIUM_RISK_THRESHOLD - 0.01) == "low"
    assert analytics.risk_level(analytics.MEDIUM_RISK_THRESHOLD) == "medium"
    assert analytics.risk_level(analytics.HIGH_RISK_THRESHOLD) == "high"


# --- scoping -----------------------------------------------------------------


def test_department_manager_only_sees_their_own_people_everywhere(client, django_user_model):
    mine, other = DepartmentFactory(name="Mine"), DepartmentFactory(name="Theirs")
    user = _login(client, django_user_model, "Department Manager")
    mine.managers.add(user)
    my_person = EmployeeFactory(department=mine, full_name="Visible Person")
    their_person = EmployeeFactory(department=other, full_name="Hidden Person")
    their_campaign = CampaignFactory(target_department=other, name="Hidden Campaign")
    compute_and_store_snapshot(my_person)
    compute_and_store_snapshot(their_person)

    for name in ("overview", "employees", "departments", "campaigns", "training"):
        body = client.get(reverse(f"dashboard:{name}")).content.decode()
        assert "Hidden Person" not in body and "Hidden Campaign" not in body and "Theirs" not in body, name

    assert "Visible Person" in client.get(reverse("dashboard:employees")).content.decode()
    assert client.get(reverse("dashboard:employee-detail", args=[their_person.pk])).status_code == 404
    assert client.get(reverse("dashboard:department-detail", args=[other.pk])).status_code == 404
    assert client.get(reverse("dashboard:campaign-detail", args=[their_campaign.pk])).status_code == 404


# --- HTMX, sorting, search ---------------------------------------------------


def test_htmx_requests_get_only_the_table_fragment(client, django_user_model):
    EmployeeFactory(full_name="Grace Hopper")
    _login(client, django_user_model)

    full = client.get(reverse("dashboard:employees")).content.decode()
    partial = client.get(reverse("dashboard:employees"), HTTP_HX_REQUEST="true").content.decode()

    assert "<html" in full and 'id="employee-results"' in full
    assert "<html" not in partial and 'id="employee-results"' in partial and "Grace Hopper" in partial


def test_employee_search_and_department_filter(client, django_user_model):
    dept = DepartmentFactory()
    EmployeeFactory(full_name="Grace Hopper", department=dept)
    EmployeeFactory(full_name="Alan Turing")
    _login(client, django_user_model)
    url = reverse("dashboard:employees")

    assert "Alan Turing" not in client.get(url, {"q": "grace"}).content.decode()
    assert "Grace Hopper" not in client.get(url, {"department": "999999"}).content.decode()
    assert "Grace Hopper" in client.get(url, {"department": str(dept.pk)}).content.decode()


def test_employees_sort_by_score_puts_unscored_last_and_is_safe_with_junk(client, django_user_model):
    low, high = EmployeeFactory(full_name="Low Risk"), EmployeeFactory(full_name="High Risk")
    EventFactory(employee=high, event_type=Event.EventType.CREDENTIAL_ATTEMPT)
    compute_and_store_snapshot(high)
    compute_and_store_snapshot(low)
    EmployeeFactory(full_name="Never Scored")
    _login(client, django_user_model)
    url = reverse("dashboard:employees")

    body = client.get(url, {"sort": "-score"}).content.decode()
    assert body.index("High Risk") < body.index("Low Risk") < body.index("Never Scored")
    ascending = client.get(url, {"sort": "score"}).content.decode()
    assert ascending.index("Low Risk") < ascending.index("High Risk") < ascending.index("Never Scored")
    for junk in ("bogus", "-", "name;drop", ""):
        assert client.get(url, {"sort": junk}).status_code == 200
    assert client.get(url, {"page": "abc"}).status_code == 200


def test_sortable_headers_expose_aria_sort(client, django_user_model):
    EmployeeFactory()
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:employees"), {"sort": "name"}).content.decode()

    assert 'aria-sort="ascending"' in body


def test_training_status_filter(client, django_user_model):
    from apps.training.tests.factories import TrainingAssignmentFactory
    from django.utils import timezone

    done = TrainingAssignmentFactory(completed_at=timezone.now(), employee=EmployeeFactory(full_name="Done Person"))
    open_ = TrainingAssignmentFactory(employee=EmployeeFactory(full_name="Open Person"))
    _login(client, django_user_model)
    url = reverse("dashboard:training")

    assert "Open Person" in client.get(url).content.decode()  # default: outstanding
    assert "Done Person" not in client.get(url).content.decode()
    assert "Done Person" in client.get(url, {"status": "completed"}).content.decode()
    both = client.get(url, {"status": "all"}).content.decode()
    assert "Done Person" in both and "Open Person" in both
    assert done and open_


# --- hardening ---------------------------------------------------------------


def test_dashboard_pages_send_a_strict_csp_and_are_never_cached(client, django_user_model):
    _login(client, django_user_model)

    response = client.get(reverse("dashboard:overview"))

    csp = response["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert "no-store" in response["Cache-Control"]


def test_admin_keeps_working_without_the_dashboard_csp(client, django_user_model):
    user = django_user_model.objects.create_superuser("root", "r@example.com", "x")
    client.force_login(user)

    assert "Content-Security-Policy" not in client.get(reverse("admin:index"))


def test_templates_contain_no_inline_styles_or_scripts():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "templates"
    offenders = []
    for path in root.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if " style=" in text or "onclick=" in text or "<script>" in text:
            offenders.append(path.name)
    assert not offenders


def test_htmx_and_chartjs_are_self_hosted(client, django_user_model):
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:overview")).content.decode()

    assert "http://" not in body and "https://" not in body


def test_empty_sort_param_still_uses_the_default_score_order(client, django_user_model):
    quiet, risky = EmployeeFactory(full_name="Aaa Quiet"), EmployeeFactory(full_name="Zzz Risky")
    EventFactory(employee=risky, event_type=Event.EventType.CREDENTIAL_ATTEMPT)
    compute_and_store_snapshot(risky)
    compute_and_store_snapshot(quiet)
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:employees"), {"q": "", "sort": ""}).content.decode()

    assert body.index("Zzz Risky") < body.index("Aaa Quiet")  # score order, not alphabetical


def test_sort_links_do_not_inherit_the_search_forms_include(client, django_user_model):
    """Regression: hx-include on the <form> made every sort click send the query twice with an empty sort=."""
    EmployeeFactory()
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:employees")).content.decode()

    form_tag = body[body.index("<form method=\"get\""):]
    form_tag = form_tag[: form_tag.index(">") + 1]
    assert "hx-include" not in form_tag
    assert body.count('hx-include="closest form"') == 2  # only the search box and the department select
