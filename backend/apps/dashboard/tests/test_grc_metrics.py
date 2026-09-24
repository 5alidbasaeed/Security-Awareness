"""GRC program metrics: coverage, response speed, resilience, repeat failures, targets, training ageing, and the pages that show them."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.models import Employee
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring import program_metrics as pm
from apps.risk_scoring.services import compute_and_store_snapshot
from apps.training.models import QuizAttempt, TrainingAssignment
from apps.training.tests.factories import TrainingAssignmentFactory, TrainingModuleFactory

pytestmark = pytest.mark.django_db

E = Event.EventType


def _campaign(department=None, days_ago=10, **kwargs):
    return CampaignFactory(target_department=department, launched_at=timezone.now() - timedelta(days=days_ago), **kwargs)


def _event(employee, campaign, kind, minutes_after=0):
    base = campaign.launched_at
    return EventFactory(employee=employee, campaign=campaign, event_type=kind, occurred_at=base + timedelta(minutes=minutes_after))


def _sent(employee, campaign):
    return _event(employee, campaign, E.EMAIL_SENT)


def _login(client, django_user_model, group="Report Viewer"):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="grc", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


# --- the unit of analysis ------------------------------------------------------


def test_tally_counts_each_employee_once_per_campaign_and_measures_speed():
    department = DepartmentFactory()
    campaign = _campaign(department)
    clicker, reporter, idle = (EmployeeFactory(department=department) for _ in range(3))
    for person in (clicker, reporter, idle):
        _sent(person, campaign)
    _event(clicker, campaign, E.LINK_CLICKED, minutes_after=2)
    _event(clicker, campaign, E.LINK_CLICKED, minutes_after=9)  # a second click is not a second failure
    _event(clicker, campaign, E.CREDENTIAL_ATTEMPT, minutes_after=4)
    _event(reporter, campaign, E.PHISHING_REPORTED, minutes_after=30)

    result = pm.tally(pm.outcomes(Event.objects.filter(campaign=campaign)).values())

    assert (result["tested"], result["failed"], result["submitted"], result["reported"]) == (3, 1, 1, 1)
    assert result["failure_rate"] == pytest.approx(33.3, abs=0.1)
    assert result["median_seconds_to_fail"] == 120  # first failure was the click at 2 minutes
    assert result["median_seconds_to_report"] == 1800
    assert result["resilience_ratio"] == 1.0


def test_resilience_ratio_is_undefined_when_nobody_failed():
    campaign = _campaign()
    person = EmployeeFactory()
    _sent(person, campaign)
    _event(person, campaign, E.PHISHING_REPORTED, minutes_after=5)

    assert pm.tally(pm.outcomes(Event.objects.all()).values())["resilience_ratio"] is None


def test_email_opened_is_never_read():
    campaign = _campaign()
    person = EmployeeFactory()
    _sent(person, campaign)
    _event(person, campaign, E.EMAIL_OPENED, minutes_after=1)

    (record,) = pm.outcomes(Event.objects.all()).values()

    assert E.EMAIL_OPENED.value not in record


def test_a_clock_skewed_event_never_produces_a_negative_speed():
    campaign = _campaign()
    person = EmployeeFactory()
    _event(person, campaign, E.EMAIL_SENT, minutes_after=10)
    _event(person, campaign, E.LINK_CLICKED, minutes_after=5)  # before "sent"

    assert pm.tally(pm.outcomes(Event.objects.all()).values())["median_seconds_to_fail"] is None


# --- coverage, repeat failures, periods ---------------------------------------


def test_coverage_only_counts_eligible_people_and_the_period_window():
    department = DepartmentFactory()
    recent, old = _campaign(department, days_ago=10), _campaign(department, days_ago=200)
    tested, untested = EmployeeFactory(department=department), EmployeeFactory(department=department)
    exempt = EmployeeFactory(department=department, is_exempt=True)
    left = EmployeeFactory(department=department, is_active=False)
    _sent(tested, recent)
    _sent(untested, old)  # tested, but outside a 90-day window
    _sent(exempt, recent)
    _sent(left, recent)
    period = pm.resolve_period("90")

    org = pm.analyse(Employee.objects.all(), Campaign.objects.all(), period["since"])["org"]

    assert org["eligible"] == 2 and org["covered"] == 1 and org["coverage_percent"] == 50.0


def test_repeat_offenders_need_failures_on_two_different_campaigns():
    department = DepartmentFactory()
    first, second = _campaign(department, days_ago=20), _campaign(department, days_ago=10)
    repeat, once = EmployeeFactory(department=department), EmployeeFactory(department=department)
    for campaign in (first, second):
        _sent(repeat, campaign)
        _event(repeat, campaign, E.LINK_CLICKED, 3)
    _sent(once, first)
    _event(once, first, E.LINK_CLICKED, 3)
    _event(once, first, E.CREDENTIAL_ATTEMPT, 4)  # two failures, but on ONE campaign

    org = pm.analyse(Employee.objects.all(), Campaign.objects.all())["org"]

    assert org["repeat_offenders"] == 1
    assert pm.attention_counts(Employee.objects.all())["repeat"] == 1


def test_unknown_period_falls_back_to_the_default():
    assert pm.resolve_period("banana")["key"] == pm.DEFAULT_PERIOD
    assert pm.resolve_period("all")["since"] is None


def test_small_departments_are_suppressed_like_the_rest_of_the_analytics(settings):
    settings.MIN_REPORTING_COHORT = 5
    department = DepartmentFactory()
    campaign = _campaign(department)
    person = EmployeeFactory(department=department)
    _sent(person, campaign)

    result = pm.analyse(Employee.objects.all(), Campaign.objects.all())

    assert result["org"]["suppressed"] and result["departments"][department.pk]["suppressed"]
    assert result["org"]["failure_rate"] is None


# --- targets -------------------------------------------------------------------


def test_scorecard_grades_each_indicator_against_its_target(settings):
    settings.TARGET_MAX_FAILURE_RATE = 10
    settings.TARGET_MIN_REPORT_RATE = 20
    settings.TARGET_MIN_COVERAGE = 90
    settings.TARGET_MIN_TRAINING_COMPLETION = 95
    org = {"failure_rate": 8.0, "report_rate": 12.0, "coverage_percent": None}

    grades = {row["label"]: row["status"] for row in pm.scorecard(org, 96)}

    assert grades == {"Failure rate": "met", "Report rate": "missed", "Test coverage": "no_data", "Training completion": "met"}


# --- per-campaign and per-employee ---------------------------------------------


def test_campaign_breakdown_reports_speed_departments_and_repeat_failures():
    dept_a, dept_b = DepartmentFactory(name="Alpha"), DepartmentFactory(name="Beta")
    earlier, campaign = _campaign(dept_a, days_ago=30), _campaign(dept_a, days_ago=5)
    a1, a2, b1 = (EmployeeFactory(department=d) for d in (dept_a, dept_a, dept_b))
    for person in (a1, a2, b1):
        _sent(person, campaign)
    _event(a1, campaign, E.LINK_CLICKED, 60)
    _sent(a1, earlier)
    _event(a1, earlier, E.CREDENTIAL_ATTEMPT, 5)  # failed before too -> a repeat failure
    _event(b1, campaign, E.PHISHING_REPORTED, 10)

    result = pm.campaign_breakdown(campaign, Employee.objects.all())

    assert result["repeat_failures"] == 1
    assert [d["name"] for d in result["departments"]][0] == "Alpha"  # highest failure rate first
    assert result["reporters"][0][0] == b1
    assert result["unit"]["no_action"] == 1
    hour = result["timeline"]["labels"].index("1 h")
    assert result["timeline"]["failed"][hour] == pytest.approx(33.3, abs=0.1)


def test_employee_history_lists_each_campaign_with_its_outcome():
    person = EmployeeFactory(department=DepartmentFactory())
    clicked, reported = _campaign(days_ago=20), _campaign(days_ago=5)
    _sent(person, clicked)
    _event(person, clicked, E.LINK_CLICKED, 3)
    _event(person, clicked, E.PHISHING_REPORTED, 8)
    _sent(person, reported)
    _event(person, reported, E.PHISHING_REPORTED, 2)

    newest, oldest = pm.employee_history(person)

    assert (newest["outcome"], newest["also_reported"]) == ("reported", False)
    assert (oldest["outcome"], oldest["also_reported"], oldest["seconds_to_fail"]) == ("clicked", True, 180)


def test_focus_filters_pick_the_right_people():
    department = DepartmentFactory()
    campaign = _campaign(department)
    never = EmployeeFactory(department=department)
    exempt = EmployeeFactory(department=department, is_exempt=True)
    left = EmployeeFactory(department=department, is_active=False)
    tested = EmployeeFactory(department=department)
    _sent(tested, campaign)
    annotated = pm.annotate_activity(Employee.objects.all())

    assert set(pm.apply_focus(annotated, "never_tested")) == {never}
    assert set(pm.apply_focus(annotated, "exempt")) == {exempt}
    assert set(pm.apply_focus(annotated, "inactive")) == {left}


def test_high_risk_untrained_focus_and_level_filter():
    department = DepartmentFactory()
    risky, trained = EmployeeFactory(department=department), EmployeeFactory(department=department)
    for person in (risky, trained):
        for _ in range(2):
            EventFactory(employee=person, event_type=E.CREDENTIAL_ATTEMPT)
        compute_and_store_snapshot(person)
    TrainingAssignmentFactory(employee=trained, completed_at=timezone.now())
    annotated = pm.annotate_activity(Employee.objects.all())

    assert set(pm.apply_focus(annotated, "high_risk_untrained")) == {risky}
    assert set(pm.apply_level(annotated, "high")) == {risky, trained}
    assert not pm.apply_level(annotated, "low").exists()


# --- training ------------------------------------------------------------------


def test_training_breakdown_covers_modules_ageing_and_on_time_rate():
    now = timezone.now()
    module = TrainingModuleFactory(title="Phishing basics")
    department = DepartmentFactory(name="Ops")
    done_on_time = TrainingAssignmentFactory(module=module, employee=EmployeeFactory(department=department))
    done_late = TrainingAssignmentFactory(module=module, employee=EmployeeFactory(department=department))
    TrainingAssignmentFactory(module=module, employee=EmployeeFactory(department=department), due_at=now - timedelta(days=45))
    TrainingAssignmentFactory(module=module, employee=EmployeeFactory(department=department), due_at=now - timedelta(days=3))
    type(done_on_time).objects.filter(pk=done_on_time.pk).update(due_at=now + timedelta(days=5), completed_at=now)
    type(done_late).objects.filter(pk=done_late.pk).update(due_at=now - timedelta(days=5), completed_at=now)
    QuizAttempt.objects.create(assignment=done_on_time, score_percent=90, passed=True)
    QuizAttempt.objects.create(assignment=done_late, score_percent=50, passed=False)

    result = pm.training_breakdown(TrainingAssignment.objects.all(), now=now)

    (row,) = result["modules"]
    assert (row["assigned"], row["completed"], row["overdue"]) == (4, 2, 2)
    assert row["avg_score"] == 70.0 and row["pass_rate"] == 50.0
    assert result["aging"] == {"1-7 days": 1, "8-30 days": 0, "31+ days": 1}
    assert result["on_time_rate_percent"] == 50.0
    assert result["departments"][0]["name"] == "Ops"


# --- pages ---------------------------------------------------------------------


def _seed_program():
    department = DepartmentFactory(name="Finance")
    campaign = _campaign(department, name="Q3 invoice lure")
    people = [EmployeeFactory(department=department, full_name=f"Person {i}") for i in range(4)]
    for person in people:
        _sent(person, campaign)
    _event(people[0], campaign, E.LINK_CLICKED, 3)
    _event(people[1], campaign, E.PHISHING_REPORTED, 12)
    for person in people:
        compute_and_store_snapshot(person)
    return department, campaign, people


def test_overview_shows_the_scorecard_and_attention_list(client, django_user_model):
    _seed_program()
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:overview")).content.decode()

    assert "Programme health against targets" in body
    assert "Failure rate" in body and "25%" in body  # 1 of 4 failed
    assert "Meets target" in body or "Below target" in body  # graded with words, never colour alone
    assert "Needs attention" in body and "Risk distribution" in body


def test_overview_period_selector_narrows_the_window(client, django_user_model):
    department = DepartmentFactory()
    old = _campaign(department, days_ago=300)
    person = EmployeeFactory(department=department)
    _sent(person, old)
    _event(person, old, E.LINK_CLICKED, 1)
    _login(client, django_user_model)

    in_window = client.get(reverse("dashboard:overview"), {"period": "all"}).content.decode()
    out_of_window = client.get(reverse("dashboard:overview"), {"period": "30"}).content.decode()

    assert "100%" in in_window
    assert "No campaigns in this period" in out_of_window


def test_campaign_page_shows_speed_departments_and_follow_up(client, django_user_model):
    department, campaign, people = _seed_program()
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:campaign-detail", args=[campaign.pk])).content.decode()

    assert "How quickly people responded" in body and "Median time to click" in body
    assert "Finance" in body and "Who reported it" in body and people[1].full_name in body
    assert "Follow-up training" in body


def test_campaigns_list_filters_by_name_and_shows_totals(client, django_user_model):
    _seed_program()
    _campaign(DepartmentFactory(), name="Unrelated HR notice")
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:campaigns"), {"q": "invoice"}).content.decode()

    assert "Q3 invoice lure" in body and "Unrelated HR notice" not in body
    assert "Reports per failure" in body and "Median time to click" in body


def test_employees_page_filters_and_shows_new_columns(client, django_user_model):
    department, campaign, people = _seed_program()
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:employees"), {"focus": "never_tested"}).content.decode()
    assert "No employees match" in body  # everyone was tested

    everyone = client.get(reverse("dashboard:employees"), {"sort": "-failed"}).content.decode()
    assert "Last tested" in everyone and "None assigned" in everyone
    assert everyone.index(people[0].full_name) < everyone.index(people[3].full_name)  # the person who failed sorts first


def test_employees_page_ignores_junk_filters(client, django_user_model):
    _seed_program()
    _login(client, django_user_model)

    assert client.get(reverse("dashboard:employees"), {"focus": "drop table", "level": "??"}).status_code == 200


def test_employee_page_shows_simulation_history(client, django_user_model):
    department, campaign, people = _seed_program()
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:employee-detail", args=[people[0].pk])).content.decode()

    assert "Simulation history" in body and "Q3 invoice lure" in body and "Clicked the link" in body


def test_departments_page_compares_with_the_organisation(client, django_user_model):
    _seed_program()
    other = DepartmentFactory(name="Legal")
    EmployeeFactory(department=other)
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:departments"), {"sort": "-failure", "period": "all"}).content.decode()

    assert "Vs organisation" in body and "Organisation" in body and "Coverage" in body
    assert body.index("Finance") < body.index("Legal")  # the department with data sorts above the one without


def test_department_page_lists_campaigns_and_repeat_failures(client, django_user_model):
    department, campaign, people = _seed_program()
    second = _campaign(department, days_ago=3, name="Second wave")
    _sent(people[0], second)
    _event(people[0], second, E.LINK_CLICKED, 1)
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:department-detail", args=[department.pk])).content.decode()

    assert "Campaign results for this department" in body and "Second wave" in body
    assert people[0].full_name in body and "2 campaigns" in body


def test_training_page_filters_by_department_and_module(client, django_user_model):
    module = TrainingModuleFactory(title="Only this module")
    dept_a, dept_b = DepartmentFactory(name="A-team"), DepartmentFactory(name="B-team")
    TrainingAssignmentFactory(module=module, employee=EmployeeFactory(department=dept_a, full_name="Ann in A"))
    TrainingAssignmentFactory(employee=EmployeeFactory(department=dept_b, full_name="Bob in B"))
    _login(client, django_user_model)

    body = client.get(reverse("dashboard:training"), {"department": dept_a.pk, "module": module.pk}).content.decode()

    assert "Ann in A" in body and "Bob in B" not in body
    assert "Overdue ageing" in body and "Completed on time" in body


def test_department_manager_never_sees_other_departments_in_the_new_metrics(client, django_user_model):
    mine, theirs = DepartmentFactory(name="Mine"), DepartmentFactory(name="Theirs")
    user = _login(client, django_user_model, "Department Manager")
    mine.managers.add(user)
    campaign = _campaign(theirs, name="Theirs only")
    outsider = EmployeeFactory(department=theirs, full_name="Outsider")
    _sent(outsider, campaign)
    _event(outsider, campaign, E.CREDENTIAL_ATTEMPT, 1)

    for name in ("overview", "departments", "employees", "training", "campaigns"):
        body = client.get(reverse(f"dashboard:{name}")).content.decode()
        assert "Outsider" not in body and "Theirs" not in body, name
    assert client.get(reverse("dashboard:department-detail", args=[theirs.pk])).status_code == 404
    assert client.get(reverse("dashboard:campaign-detail", args=[campaign.pk])).status_code == 404
