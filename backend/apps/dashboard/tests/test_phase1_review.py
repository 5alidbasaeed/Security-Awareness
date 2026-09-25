"""Regressions from the 2026-09-25 end-to-end dashboard review (TEST_PLAN.md, phase 1)."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.models import Employee
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring import analytics, program_metrics as pm
from apps.risk_scoring.services import compute_and_store_snapshot
from apps.training.models import TrainingAssignment
from apps.training.tests.factories import TrainingAssignmentFactory

pytestmark = pytest.mark.django_db
E = Event.EventType


def _login(client, django_user_model, group):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=group.replace(" ", ""), password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


def _overdue_pair():
    """One overdue assignment and one overdue-but-waived assignment."""
    past = timezone.now() - timedelta(days=10)
    owed = TrainingAssignmentFactory(due_at=past)
    waived = TrainingAssignmentFactory(due_at=past, waived_at=timezone.now(), waiver_reason="On leave")
    return owed, waived


# --- waived training is out of every dashboard figure -------------------------


def test_training_breakdown_ignores_waived_assignments():
    _overdue_pair()
    breakdown = pm.training_breakdown(TrainingAssignment.objects.all())

    assert sum(m["assigned"] for m in breakdown["modules"]) == 1
    assert sum(m["overdue"] for m in breakdown["modules"]) == 1
    assert sum(breakdown["aging"].values()) == 1
    assert sum(d["overdue"] for d in breakdown["departments"]) == 1


def test_overdue_attention_count_ignores_waived_assignments():
    owed, waived = _overdue_pair()
    assert pm.attention_counts(Employee.objects.all())["overdue_training"] == 1
    annotated = pm.annotate_activity(Employee.objects.filter(pk=waived.employee_id)).get()
    assert not annotated.has_overdue and not annotated.has_outstanding


def test_campaign_follow_up_training_ignores_waived_assignments():
    campaign = CampaignFactory(launched_at=timezone.now())
    for waive in (False, True):
        person = EmployeeFactory()
        click = EventFactory(employee=person, campaign=campaign, event_type=E.LINK_CLICKED)
        TrainingAssignmentFactory(
            employee=person, triggered_by_event=click, due_at=timezone.now() - timedelta(days=1),
            waived_at=timezone.now() if waive else None,
        )
    follow_up = pm.follow_up_training(campaign, Employee.objects.all())
    assert (follow_up["assigned"], follow_up["overdue"]) == (1, 1)


def test_training_page_lists_waived_only_under_waived(client, django_user_model):
    _login(client, django_user_model, "Report Viewer")
    owed, waived = _overdue_pair()
    url = reverse("dashboard:training")

    for status in ("overdue", "outstanding"):
        html = client.get(url, {"status": status}).content.decode()
        assert owed.employee.full_name in html and waived.employee.full_name not in html, status
    html = client.get(url, {"status": "waived"}).content.decode()
    assert waived.employee.full_name in html and owed.employee.full_name not in html
    all_rows = client.get(url, {"status": "all"}).content.decode()
    assert "Waived</span>" in all_rows


# --- offboarded employees are out of today's posture ---------------------------


def _scored(employee, *clicks):
    for campaign in clicks:
        EventFactory(employee=employee, campaign=campaign, event_type=E.LINK_CLICKED)
    compute_and_store_snapshot(employee)


def test_offboarded_employees_leave_current_posture_but_keep_history(client, django_user_model):
    department = DepartmentFactory()
    campaigns = [CampaignFactory(launched_at=timezone.now() - timedelta(days=d)) for d in (5, 6)]
    leaver = EmployeeFactory(department=department, full_name="Leaver Person", is_active=False)
    stayer = EmployeeFactory(department=department, full_name="Stayer Person")
    _scored(leaver, *campaigns)
    _scored(stayer)

    summary = analytics.scope_summary(Employee.objects.all())
    assert summary["employees"] == 1
    assert pm.risk_distribution(Employee.objects.all())["total"] == 1

    _login(client, django_user_model, "Report Viewer")
    overview = client.get(reverse("dashboard:overview")).content.decode()
    assert "Leaver Person" not in overview
    detail = client.get(reverse("dashboard:department-detail", args=[department.pk])).content.decode()
    assert "Leaver Person" not in detail and "Stayer Person" in detail
    # ...but the person's own history is still there for audit.
    assert client.get(reverse("dashboard:employee-detail", args=[leaver.pk])).status_code == 200


# --- Training Manager can open the Training page (and only that) ----------------


def test_training_manager_sees_training_page_but_not_risk_pages(client, django_user_model):
    _login(client, django_user_model, "Training Manager")
    owed, _ = _overdue_pair()

    response = client.get(reverse("dashboard:training"))
    assert response.status_code == 200
    html = response.content.decode()
    assert owed.employee.full_name in html
    assert reverse("dashboard:employee-detail", args=[owed.employee_id]) not in html  # no links into 403s
    assert reverse("dashboard:campaigns") not in html

    for name in ("overview", "campaigns", "employees", "departments"):
        assert client.get(reverse(f"dashboard:{name}")).status_code == 403
    assert reverse("dashboard:training") in client.get(reverse("dashboard:overview")).content.decode()


def test_overview_training_page_and_governance_agree_when_a_leaver_has_open_training(client, django_user_model):
    from apps.core import governance

    past = timezone.now() - timedelta(days=5)
    TrainingAssignmentFactory(due_at=past)
    TrainingAssignmentFactory(due_at=past, employee=EmployeeFactory(is_active=False))
    _login(client, django_user_model, "Report Viewer")

    card = analytics.scope_summary(Employee.objects.all())["training"]
    assert (card["assigned"], card["overdue"]) == (1, 1)
    assert "1 overdue" in client.get(reverse("dashboard:training")).content.decode()
    check = next(c for c in governance.control_health() if c["label"] == "Training is being completed")
    assert "1 overdue" in check["detail"]
