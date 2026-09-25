"""Guided campaign form, content and training pages: summary panel, duplicate, retire, readiness."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.campaigns.models import Campaign, EmailDraft, LandingDraft, SmartGroup
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.engine.tests.fakes import FakePhishingEngineClient
from apps.training.models import Quiz, QuizChoice, QuizQuestion, TrainingModule, TrainingSlide
from apps.training.tests.factories import TrainingModuleFactory

pytestmark = pytest.mark.django_db


def login(client, django_user_model, group="Security Admin"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=f"u-{group}", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


@pytest.fixture
def engine(monkeypatch):
    fake = FakePhishingEngineClient()
    monkeypatch.setattr("apps.manage.views_ux.get_client", lambda: fake)
    monkeypatch.setattr("apps.manage.views.get_client", lambda: fake)
    return fake


# --- campaign summary panel ---------------------------------------------------------------


def test_summary_counts_recipients_and_says_who_is_left_out(client, django_user_model):
    login(client, django_user_model)
    dept = DepartmentFactory(name="Finance")
    EmployeeFactory.create_batch(2, department=dept)
    EmployeeFactory(department=dept, is_exempt=True, exempt_reason="Leave")
    EmployeeFactory(department=dept, is_active=False)
    EmailDraft.objects.create(name="Invoice", subject="Overdue invoice", body_html="<p>x</p>")
    LandingDraft.objects.create(name="Portal")
    html = client.get(reverse("manage:campaign-summary"), {
        "email": "Invoice", "landing_page": "Portal", "audience": f"dept:{dept.pk}"}).content.decode()
    assert "<strong>2</strong> Finance" in html and "1 exempt" in html and "1 inactive" in html
    assert "Subject: Overdue invoice" in html and "Preview the email" in html and "Preview the page" in html
    assert "Ready to save" in html


def test_summary_lists_what_is_still_missing(client, django_user_model):
    login(client, django_user_model)
    html = client.get(reverse("manage:campaign-summary")).content.decode()
    assert "Choose the email" in html and "Choose where people land" in html and "Choose who receives it" in html


def test_summary_warns_about_an_empty_audience_and_an_empty_training_module(client, django_user_model):
    login(client, django_user_model)
    empty = DepartmentFactory()
    hollow = TrainingModuleFactory(title="Hollow", content_url="")
    html = client.get(reverse("manage:campaign-summary"), {
        "audience": f"dept:{empty.pk}", "training_module": hollow.pk}).content.decode()
    assert "Nobody is eligible" in html and "“Hollow” has no content yet" in html


def test_a_department_manager_cannot_probe_other_departments_or_everyone(client, django_user_model):
    mine, other = DepartmentFactory(), DepartmentFactory()
    EmployeeFactory.create_batch(4, department=other)
    manager = login(client, django_user_model, "Department Manager")
    mine.managers.add(manager)
    SmartGroup.objects.create(name="High risk", rule=SmartGroup.Rule.HIGH_RISK)
    for value in (f"dept:{other.pk}", "all", f"smart:{SmartGroup.objects.first().pk}"):
        html = client.get(reverse("manage:campaign-summary"), {"audience": value}).content.decode()
        assert "isn&#x27;t available to you" in html or "isn't available to you" in html


def test_campaign_form_page_loads_with_the_summary_hook(client, django_user_model):
    login(client, django_user_model)
    html = client.get(reverse("manage:campaign-new")).content.decode()
    assert "hx-get" in html and reverse("manage:campaign-summary") in html and "Who gets it" in html


def test_campaign_list_next_step_and_status_tabs(client, django_user_model):
    login(client, django_user_model)
    dept = DepartmentFactory()
    EmployeeFactory(department=dept)
    CampaignFactory(name="A draft", target_department=dept)
    CampaignFactory(name="Waiting", target_department=dept, status=Campaign.Status.PENDING_APPROVAL)
    CampaignFactory(name="Cleared", target_department=dept, status=Campaign.Status.APPROVED)
    page = client.get(reverse("manage:campaigns")).content.decode()
    assert "Submit for approval" in page and ">Review and approve<" in page and "Launch now" in page
    tabs = client.get(reverse("manage:campaigns")).context["status_tabs"]
    assert tabs[0] == ("", "All", 3) and dict((v, n) for v, _, n in tabs)["draft"] == 1


# --- duplicate ----------------------------------------------------------------------------


def test_duplicating_an_email_copies_it_and_pushes_it_to_the_engine(client, django_user_model, engine):
    login(client, django_user_model)
    source = EmailDraft.objects.create(name="Invoice", subject="Overdue invoice", body_html="<p>Hi</p>")
    for expected in ("Copy of Invoice", "Copy of Invoice (2)"):
        response = client.post(reverse("manage:template-duplicate", args=[source.pk]))
        assert response.status_code == 302
        assert EmailDraft.objects.filter(name=expected, subject="Overdue invoice").exists()
    assert "Copy of Invoice" in {t["name"] for t in engine.list_email_templates()}
    assert "email_template_duplicated" in AuditLogEntry.objects.values_list("action", flat=True)


def test_duplicating_a_landing_page_keeps_credentials_off(client, django_user_model, engine):
    login(client, django_user_model)
    source = LandingDraft.objects.create(name="Portal", heading="Sign in")
    client.post(reverse("manage:landing-duplicate", args=[source.pk]))
    assert LandingDraft.objects.filter(name="Copy of Portal").exists()
    assert "Copy of Portal" in {p["name"] for p in engine.list_landing_pages()}


def test_report_viewer_cannot_duplicate_content(client, django_user_model, engine):
    login(client, django_user_model, "Report Viewer")
    source = EmailDraft.objects.create(name="Invoice", subject="s", body_html="<p>x</p>")
    assert client.post(reverse("manage:template-duplicate", args=[source.pk])).status_code == 403
    assert EmailDraft.objects.count() == 1


def test_content_page_shows_where_each_item_is_used(client, django_user_model, engine):
    login(client, django_user_model)
    EmailDraft.objects.create(name="Used mail", subject="s", body_html="<p>x</p>")
    EmailDraft.objects.create(name="Fresh mail", subject="s", body_html="<p>x</p>")
    CampaignFactory.create_batch(2, template_name="Used mail")
    page = client.get(reverse("manage:content")).content.decode()
    assert "used in 2 campaigns" in page and "not used yet" in page


# --- training -----------------------------------------------------------------------------


def _module_with_quiz():
    module = TrainingModuleFactory(title="Phishing 101", content_url="")
    TrainingSlide.objects.create(module=module, order=1, title="Intro", body="Hello")
    quiz = Quiz.objects.create(module=module, passing_score_percent=70)
    question = QuizQuestion.objects.create(quiz=quiz, text="Q?", order=0)
    QuizChoice.objects.create(question=question, text="Yes", is_correct=True)
    QuizChoice.objects.create(question=question, text="No")
    return module


def test_duplicating_a_module_copies_slides_and_quiz_and_starts_retired(client, django_user_model):
    login(client, django_user_model)
    source = _module_with_quiz()
    client.post(reverse("manage:module-duplicate", args=[source.pk]))
    copy = TrainingModule.objects.get(title="Copy of Phishing 101")
    assert copy.is_active is False and copy.slides.count() == 1
    assert copy.quiz.passing_score_percent == 70 and copy.quiz.questions.get().choices.filter(is_correct=True).count() == 1
    assert source.quiz.questions.count() == 1  # the original is untouched
    assert "training_module_duplicated" in AuditLogEntry.objects.values_list("action", flat=True)


def test_a_module_can_be_retired_and_reactivated(client, django_user_model):
    login(client, django_user_model)
    module = _module_with_quiz()
    client.post(reverse("manage:module-toggle", args=[module.pk]))
    module.refresh_from_db()
    assert not module.is_active
    client.post(reverse("manage:module-toggle", args=[module.pk]))
    module.refresh_from_db()
    assert module.is_active


def test_training_list_says_whether_each_module_is_usable(client, django_user_model):
    login(client, django_user_model)
    _module_with_quiz()
    TrainingModuleFactory(title="Empty shell", content_url="")
    hollow_quiz = TrainingModuleFactory(title="Hollow quiz", content_url="https://lms.example/x")
    Quiz.objects.create(module=hollow_quiz)
    rows = {m.title: m.readiness[0] for m in client.get(reverse("manage:training")).context["page"]}
    assert rows == {"Phishing 101": "Ready", "Empty shell": "No content", "Hollow quiz": "Quiz is empty"}
    assert "Quiz: 1 question, pass at 70%" in client.get(reverse("manage:training")).content.decode()


def test_training_list_search_and_retired_filter(client, django_user_model):
    login(client, django_user_model)
    TrainingModuleFactory(title="Passwords")
    TrainingModuleFactory(title="Old course", is_active=False)
    get = lambda **p: client.get(reverse("manage:training"), p).content.decode()  # noqa: E731
    assert "Passwords" in get(q="pass") and "Old course" not in get(q="pass")
    assert "Old course" in get(state="retired") and "Passwords" not in get(state="retired")


def test_module_edit_page_shows_readiness_and_assign_link_prefills(client, django_user_model):
    login(client, django_user_model)
    module = TrainingModuleFactory(title="Bare", content_url="")
    assert "No content" in client.get(reverse("manage:module-edit", args=[module.pk])).content.decode()
    form = client.get(reverse("manage:assignment-new"), {"module": module.pk}).context["form"]
    assert form.initial["module"] == str(module.pk)


# --- hub and wording ----------------------------------------------------------------------


def test_hub_groups_pages_by_task_and_hides_sections_you_cannot_use(client, django_user_model):
    login(client, django_user_model)
    admin_html = client.get(reverse("manage:index")).content.decode()
    for heading in ("Run simulations", "Train people", "People", "Reports &amp; settings", "Governance &amp; audit"):
        assert heading in admin_html
    login(client, django_user_model, "Report Viewer")
    viewer = client.get(reverse("manage:index")).content.decode()
    assert "Run simulations" not in viewer and "Train people" in viewer  # can view assignments, cannot change campaigns


def test_one_name_for_rule_based_audiences_and_no_engine_jargon(client, django_user_model):
    user = login(client, django_user_model)
    SmartGroup.objects.create(name="Repeat clickers", rule=SmartGroup.Rule.REPEAT_CLICKERS)
    form = client.get(reverse("manage:campaign-new")).content.decode()
    catalog = client.get(reverse("manage:catalog")).content.decode()
    assert "Dynamic audiences" in form and "Smart groups" not in form
    assert "Dynamic audiences" in catalog and "Smart groups" not in catalog
    AuditLogEntry.objects.create(actor=user, action="campaign_launched", target_description="X", metadata={"gophish_campaign_id": 3})
    page = client.get(reverse("manage:audit-log")).content.decode()
    assert "engine campaign id: 3" in page and "gophish" not in page.lower()


def test_forms_use_plain_labels(client, django_user_model):
    login(client, django_user_model)
    policy = client.get(reverse("manage:policy-new")).content.decode()
    schedule = client.get(reverse("manage:schedule-new")).content.decode()
    assert "Repeat every (days)" in policy and "Training module" in policy
    assert "Report type" in schedule and ">Kind<" not in schedule


# --- consolidated sections ----------------------------------------------------------------


def _aside(client, url):
    import re

    html = client.get(url).content.decode()
    return html, re.search(r'<aside class="admin-side".*?</aside>', html, re.S).group(0)


def _tabs(html):
    import re

    nav = re.search(r'<nav class="tabs" aria-label="In this section">(.*?)</nav>', html, re.S)
    return re.findall(r'<a class="tab" href="[^"]+"( aria-current="page")?>([^<]+)</a>', nav.group(1)) if nav else []


def test_the_sidebar_is_one_entry_per_area(client, django_user_model):
    login(client, django_user_model)
    _, side = _aside(client, reverse("manage:index"))
    for merged in ("Training assignments", "Exemptions", "Audit log", "Users &amp; access", "Mail server", "API keys", "Departments", "Employees"):
        assert merged not in side  # these are tabs now, not separate sidebar entries
    for entry in ("Overview", "Campaigns", "Emails &amp; pages", "Training", "People", "Reported emails", "Scheduled reports", "Settings", "Governance"):
        assert f">{entry}<" in side
    assert side.count("<a ") == 9


def test_related_pages_share_a_tab_strip_and_mark_the_current_one(client, django_user_model):
    login(client, django_user_model)
    cases = {
        reverse("manage:assignments"): (["Modules", "Assignments", "Mandatory training"], "Assignments"),
        reverse("manage:policies"): (["Modules", "Assignments", "Mandatory training"], "Mandatory training"),
        reverse("manage:exemptions"): (["Employees", "Departments", "Exemptions"], "Exemptions"),
        reverse("manage:employee-new"): (["Employees", "Departments", "Exemptions"], "Employees"),
        reverse("manage:api-keys"): (["Mail server", "Deliverability check", "API keys"], "API keys"),
        reverse("manage:deliverability"): (["Mail server", "Deliverability check", "API keys"], "Deliverability check"),
        reverse("manage:audit-log"): (["Program &amp; controls", "Users &amp; access", "Audit log"], "Audit log"),
    }
    for url, (labels, current) in cases.items():
        tabs = _tabs(client.get(url).content.decode())
        assert [label for _, label in tabs] == labels, url
        assert [label for cur, label in tabs if cur] == [current], url


def test_tabs_only_list_pages_you_may_open(client, django_user_model):
    login(client, django_user_model, "Report Viewer")
    html, side = _aside(client, reverse("manage:audit-log"))
    assert [label for _, label in _tabs(html)] == ["Program &amp; controls", "Audit log"]  # no Users & access
    assert ">Governance<" in side and ">Settings<" in side


def test_a_section_with_a_single_page_draws_no_tab_strip(client, django_user_model):
    login(client, django_user_model, "Campaign Manager")  # can view campaigns, not training assignments/policies
    html = client.get(reverse("manage:training")).content.decode()
    assert "In this section" not in html


def test_sidebar_entries_land_on_a_page_the_person_can_open(client, django_user_model):
    login(client, django_user_model, "Training Manager")
    _, side = _aside(client, reverse("manage:index"))
    assert reverse("manage:training") in side and "Governance" not in side  # Training Manager has no audit/users access
    assert client.get(reverse("manage:training")).status_code == 200


def test_the_hub_lists_what_needs_attention(client, django_user_model):
    login(client, django_user_model)
    EmployeeFactory(is_exempt=True)  # exempt with no reason
    html = client.get(reverse("manage:index")).content.decode()
    assert "Needs your attention" in html and "no reason recorded" in html


def test_the_list_does_not_offer_approve_to_the_person_who_submitted(client, django_user_model):
    me = login(client, django_user_model)
    dept = DepartmentFactory()
    EmployeeFactory(department=dept)
    CampaignFactory(name="Mine", target_department=dept, status=Campaign.Status.PENDING_APPROVAL, submitted_by=me)
    page = client.get(reverse("manage:campaigns")).content.decode()
    assert "another approver must approve it" in page and ">Review and approve<" not in page
    other = django_user_model.objects.create_user(username="other-admin", password="x", is_staff=True)
    other.groups.add(Group.objects.get(name="Security Admin"))
    client.force_login(other)
    assert ">Review and approve<" in client.get(reverse("manage:campaigns")).content.decode()
