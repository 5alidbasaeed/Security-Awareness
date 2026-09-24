"""Governance pages of the admin console: audit log, access review, exemptions, assignments, program overview."""

from datetime import date, datetime, timedelta
from datetime import timezone as tz

import pytest
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.campaigns.audience import _base
from apps.campaigns.models import Campaign
from apps.core import governance
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.core.models import AuditLogEntry
from apps.employees.models import Employee
from apps.employees.tasks import lapse_expired_exemptions
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.reporting.data import ReportData, ReportScope
from apps.risk_scoring import analytics
from apps.training.models import TrainingAssignment, TrainingExtension
from apps.training.tasks import send_training_reminders
from apps.training.tests.factories import TrainingAssignmentFactory, TrainingModuleFactory

pytestmark = pytest.mark.django_db


def login(client, django_user_model, group="Security Admin", username=None, **extra):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username=username or f"u-{group}", password="x", is_staff=True, **extra)
    user.groups.add(Group.objects.get(name=group))
    client.force_login(user)
    return user


def actions():
    return list(AuditLogEntry.objects.values_list("action", flat=True))


# --- access -------------------------------------------------------------------------------


PAGES = ["governance", "audit-log", "users", "exemptions", "assignments", "assignment-new"]


@pytest.mark.parametrize("name", PAGES)
def test_every_governance_page_renders_for_a_security_admin(client, django_user_model, name):
    login(client, django_user_model)
    assert client.get(reverse(f"manage:{name}")).status_code == 200


def test_report_viewer_reads_the_evidence_but_cannot_change_anything(client, django_user_model):
    login(client, django_user_model, "Report Viewer")
    for name in ("governance", "audit-log", "exemptions", "assignments"):
        assert client.get(reverse(f"manage:{name}")).status_code == 200
    # access management, exports and the write pages are closed
    assert client.get(reverse("manage:users")).status_code == 403
    assert client.get(reverse("manage:assignment-new")).status_code == 403
    assert client.post(reverse("manage:audit-log-export")).status_code == 403


def test_department_manager_and_campaign_manager_are_kept_out_of_org_wide_governance(client, django_user_model):
    department = DepartmentFactory()
    manager = login(client, django_user_model, "Department Manager")
    department.managers.add(manager)
    for name in ("governance", "audit-log", "users", "assignments"):
        assert client.get(reverse(f"manage:{name}")).status_code == 403
    login(client, django_user_model, "Campaign Manager")
    assert client.get(reverse("manage:users")).status_code == 403
    assert client.get(reverse("manage:audit-log")).status_code == 403


def test_only_security_admin_holds_the_access_permission(django_user_model):
    SetupGroupsCommand().handle()
    holders = [g.name for g in Group.objects.all() if g.permissions.filter(codename="manage_user_access").exists()]
    assert holders == ["Security Admin"]


def test_sidebar_shows_governance_links_by_permission(client, django_user_model):
    login(client, django_user_model, "Report Viewer")
    html = client.get(reverse("manage:index")).content.decode()
    assert "Audit log" in html and "Exemptions" in html and "Users &amp; access" not in html


# --- audit log ----------------------------------------------------------------------------


def test_audit_log_filters_and_exports_safely(client, django_user_model):
    admin = login(client, django_user_model)
    AuditLogEntry.objects.create(actor=admin, action="campaign_launched", target_description="=HYPERLINK(evil)", metadata={"n": 3})
    AuditLogEntry.objects.create(actor=None, action="training_policy_enforced", target_description="Annual")

    page = client.get(reverse("manage:audit-log"), {"action": "campaign_launched"}).content.decode()
    assert "HYPERLINK" in page and "Annual" not in page
    assert "Annual" in client.get(reverse("manage:audit-log"), {"actor": "system"}).content.decode()

    response = client.post(reverse("manage:audit-log-export"), {"action": "campaign_launched"})
    text = response.content.decode("utf-8-sig")
    assert response["Content-Type"].startswith("text/csv")
    assert "'=HYPERLINK(evil)" in text  # formula injection neutralised
    assert "Annual" not in text
    assert "audit_log_exported" in actions()


def test_a_mistyped_date_is_ignored_not_a_crash(client, django_user_model):
    login(client, django_user_model)
    assert client.get(reverse("manage:audit-log"), {"from": "31/12/2026", "to": "nonsense"}).status_code == 200


# --- users and access ---------------------------------------------------------------------


def test_security_admin_changes_roles_and_it_is_audited(client, django_user_model):
    login(client, django_user_model)
    target = django_user_model.objects.create_user(username="carol", password="x", is_staff=True)
    trainer = Group.objects.get(name="Training Manager")
    response = client.post(reverse("manage:user-edit", args=[target.pk]), {
        "is_active": "on", "is_staff": "on", "roles": [trainer.pk], "reason": "Joined the training team",
    })
    assert response.status_code == 302
    assert list(target.groups.values_list("name", flat=True)) == ["Training Manager"]
    entry = AuditLogEntry.objects.get(action="user_access_changed")
    assert entry.metadata["after"]["roles"] == ["Training Manager"] and entry.metadata["reason"] == "Joined the training team"


def test_other_groups_are_preserved_when_console_roles_change(client, django_user_model):
    login(client, django_user_model)
    target = django_user_model.objects.create_user(username="dan", password="x", is_staff=True)
    target.groups.add(Group.objects.create(name="Some other group"))
    client.post(reverse("manage:user-edit", args=[target.pk]), {
        "is_active": "on", "is_staff": "on", "roles": [Group.objects.get(name="Report Viewer").pk], "reason": "x",
    })
    assert set(target.groups.values_list("name", flat=True)) == {"Some other group", "Report Viewer"}


def test_a_reason_is_required_and_nothing_changes_without_one(client, django_user_model):
    login(client, django_user_model)
    target = django_user_model.objects.create_user(username="erin", password="x", is_staff=True)
    client.post(reverse("manage:user-edit", args=[target.pk]), {"is_active": "on", "is_staff": "on", "reason": ""})
    assert "user_access_changed" not in actions()


def test_department_manager_role_needs_departments(client, django_user_model):
    login(client, django_user_model)
    target = django_user_model.objects.create_user(username="fay", password="x", is_staff=True)
    role = Group.objects.get(name="Department Manager")
    bad = client.post(reverse("manage:user-edit", args=[target.pk]), {"is_active": "on", "is_staff": "on", "roles": [role.pk], "reason": "x"})
    assert bad.status_code == 200 and not target.groups.exists()
    department = DepartmentFactory()
    client.post(reverse("manage:user-edit", args=[target.pk]), {
        "is_active": "on", "is_staff": "on", "roles": [role.pk], "departments": [department.pk], "reason": "x",
    })
    assert list(department.managers.all()) == [target]


def test_you_cannot_change_your_own_access_or_a_superuser(client, django_user_model):
    me = login(client, django_user_model)
    root = django_user_model.objects.create_superuser(username="root", password="x")
    assert client.get(reverse("manage:user-edit", args=[me.pk])).status_code == 302
    assert client.get(reverse("manage:user-edit", args=[root.pk])).status_code == 302


def test_the_last_security_admin_cannot_be_removed(client, django_user_model):
    # An actor who can manage access but is not themselves a Security Admin (e.g. a custom "access officer" role).
    SetupGroupsCommand().handle()
    officer_group = Group.objects.create(name="Access Officer")
    officer_group.permissions.set(
        Permission.objects.filter(codename__in=["manage_user_access", "view_auditlogentry"]))
    officer = django_user_model.objects.create_user(username="officer", password="x", is_staff=True)
    officer.groups.add(officer_group)
    client.force_login(officer)

    only_admin = django_user_model.objects.create_user(username="only-admin", password="x", is_staff=True)
    only_admin.groups.add(Group.objects.get(name="Security Admin"))
    bad = client.post(reverse("manage:user-edit", args=[only_admin.pk]), {"is_active": "on", "is_staff": "on", "reason": "x"})
    assert bad.status_code == 200 and "no active Security Admin" in bad.content.decode()
    assert only_admin.groups.filter(name="Security Admin").exists()

    # With a second Security Admin in place the same change is allowed.
    second = django_user_model.objects.create_user(username="second-admin", password="x", is_staff=True)
    second.groups.add(Group.objects.get(name="Security Admin"))
    ok = client.post(reverse("manage:user-edit", args=[only_admin.pk]), {"is_active": "on", "is_staff": "on", "reason": "moved on"})
    assert ok.status_code == 302 and not only_admin.groups.exists()


def test_deactivating_an_account_blocks_sign_in(client, django_user_model):
    login(client, django_user_model)
    target = django_user_model.objects.create_user(username="gus", password="pw", is_staff=True)
    client.post(reverse("manage:user-edit", args=[target.pk]), {"is_staff": "on", "reason": "left the company"})
    target.refresh_from_db()
    assert target.is_active is False
    assert not Client().login(username="gus", password="pw")


def test_access_review_export_and_attestation(client, django_user_model):
    login(client, django_user_model)
    django_user_model.objects.create_user(username="old", password="x", is_staff=True)
    django_user_model.objects.filter(username="old").update(date_joined=timezone.now() - timedelta(days=200))
    text = client.post(reverse("manage:access-review-export")).content.decode("utf-8-sig")
    assert "username" in text and "old" in text and "True" in text  # dormant flag
    assert "access_review_exported" in actions()

    client.post(reverse("manage:access-review-complete"), {"note": "with the CISO"})
    entry = AuditLogEntry.objects.get(action="access_review_completed")
    assert entry.metadata["note"] == "with the CISO" and entry.metadata["dormant"] == 1
    assert "Up to date" in client.get(reverse("manage:users")).content.decode()


# --- exemptions ---------------------------------------------------------------------------


def _employee_form(**extra):
    data = {"full_name": "Pat", "email": "pat@example.com", "is_exempt": "on"}
    data.update(extra)
    return data


def test_an_exemption_needs_a_reason(client, django_user_model):
    login(client, django_user_model)
    bad = client.post(reverse("manage:employee-new"), _employee_form())
    assert bad.status_code == 200 and not Employee.objects.exists()
    good = client.post(reverse("manage:employee-new"), _employee_form(exempt_reason="Legal hold", exempt_until="2999-01-01"))
    assert good.status_code == 302
    pat = Employee.objects.get()
    assert pat.exempt_reason == "Legal hold" and pat.exempt_set_at is not None and pat.exempt_set_by.username == "u-Security Admin"


def test_an_exemption_end_date_cannot_be_in_the_past(client, django_user_model):
    login(client, django_user_model)
    bad = client.post(reverse("manage:employee-new"), _employee_form(exempt_reason="Leave", exempt_until="2001-01-01"))
    assert bad.status_code == 200 and not Employee.objects.exists()


def test_removing_an_exemption_clears_its_justification(client, django_user_model):
    login(client, django_user_model)
    person = EmployeeFactory(is_exempt=True, exempt_reason="Leave", exempt_until=date(2999, 1, 1))
    client.post(reverse("manage:employee-edit", args=[person.pk]), {"full_name": person.full_name, "email": person.email})
    person.refresh_from_db()
    assert not person.is_exempt and person.exempt_reason == "" and person.exempt_until is None and person.exempt_set_at is None


def test_department_manager_cannot_alter_or_be_blocked_by_exemptions(client, django_user_model):
    department = DepartmentFactory()
    manager = login(client, django_user_model, "Department Manager")
    department.managers.add(manager)
    legacy = EmployeeFactory(department=department, is_exempt=True)  # imported with no reason
    response = client.post(reverse("manage:employee-edit", args=[legacy.pk]), {
        "full_name": "Renamed", "email": legacy.email, "department": department.pk,
        "is_exempt": "", "exempt_reason": "sneaky", "exempt_until": "",
    })
    legacy.refresh_from_db()
    assert response.status_code == 302 and legacy.full_name == "Renamed"  # their edit went through
    assert legacy.is_exempt and legacy.exempt_reason == ""  # the exemption was not theirs to change


def test_register_flags_missing_reasons_and_end_dates(client, django_user_model):
    login(client, django_user_model)
    EmployeeFactory(full_name="Bare Exempt", is_exempt=True)
    EmployeeFactory(full_name="Proper Exempt", is_exempt=True, exempt_reason="Maternity leave", exempt_until=date(2999, 1, 1))
    page = client.get(reverse("manage:exemptions")).content.decode()
    assert "No reason recorded" in page and "No end date" in page and "Maternity leave" in page
    attention = client.get(reverse("manage:exemptions"), {"show": "attention"}).content.decode()
    assert "Bare Exempt" in attention and "Proper Exempt" not in attention


def test_ending_an_exemption_from_the_register(client, django_user_model):
    login(client, django_user_model)
    person = EmployeeFactory(is_exempt=True, exempt_reason="Leave")
    client.post(reverse("manage:exemption-end", args=[person.pk]))
    person.refresh_from_db()
    assert not person.is_exempt and person.exempt_reason == ""
    assert "exemption_ended" in actions()


def test_department_manager_cannot_end_exemptions(client, django_user_model):
    department = DepartmentFactory()
    manager = login(client, django_user_model, "Department Manager")
    department.managers.add(manager)
    person = EmployeeFactory(department=department, is_exempt=True, exempt_reason="Leave")
    assert client.post(reverse("manage:exemption-end", args=[person.pk])).status_code == 403
    person.refresh_from_db()
    assert person.is_exempt


def test_an_expired_exemption_protects_nobody_even_before_the_job_runs():
    yesterday = timezone.localdate() - timedelta(days=1)
    expired = EmployeeFactory(is_exempt=True, exempt_reason="Leave")
    Employee.objects.filter(pk=expired.pk).update(exempt_until=yesterday)
    current = EmployeeFactory(is_exempt=True, exempt_reason="Leave", exempt_until=timezone.localdate() + timedelta(days=5))
    open_ended = EmployeeFactory(is_exempt=True, exempt_reason="Legal hold")
    eligible = set(_base(None).values_list("pk", flat=True))
    assert expired.pk in eligible and current.pk not in eligible and open_ended.pk not in eligible


def test_the_lapse_job_ends_expired_exemptions_and_audits_them():
    expired = EmployeeFactory(is_exempt=True, exempt_reason="Leave")
    Employee.objects.filter(pk=expired.pk).update(exempt_until=timezone.localdate() - timedelta(days=1))
    keep = EmployeeFactory(is_exempt=True, exempt_reason="Leave", exempt_until=timezone.localdate() + timedelta(days=30))
    assert lapse_expired_exemptions() == 1
    expired.refresh_from_db()
    keep.refresh_from_db()
    assert not expired.is_exempt and expired.exempt_reason == "" and keep.is_exempt
    entry = AuditLogEntry.objects.get(action="exemption_lapsed")
    assert entry.metadata["previous_reason"] == "Leave" and entry.actor is None


# --- training assignments -----------------------------------------------------------------


def test_extending_a_due_date_records_who_why_and_when(client, django_user_model):
    admin = login(client, django_user_model)
    assignment = TrainingAssignmentFactory()
    new_date = timezone.localdate() + timedelta(days=30)
    response = client.post(reverse("manage:assignment-extend", args=[assignment.pk]), {"new_due_date": new_date.isoformat(), "reason": "On leave"})
    assert response.status_code == 302
    assignment.refresh_from_db()
    assert timezone.localtime(assignment.due_at).date() == new_date
    extension = TrainingExtension.objects.get()
    assert extension.reason == "On leave" and extension.extended_by == admin and extension.previous_due_at is not None
    assert "training_due_date_extended" in actions()


def test_an_extension_must_be_later_and_justified(client, django_user_model):
    login(client, django_user_model)
    assignment = TrainingAssignmentFactory()
    past = (timezone.localdate() - timedelta(days=1)).isoformat()
    assert client.post(reverse("manage:assignment-extend", args=[assignment.pk]), {"new_due_date": past, "reason": "x"}).status_code == 200
    later = (timezone.localdate() + timedelta(days=60)).isoformat()
    assert client.post(reverse("manage:assignment-extend", args=[assignment.pk]), {"new_due_date": later, "reason": ""}).status_code == 200
    assert not TrainingExtension.objects.exists()


def test_waiving_takes_it_out_of_the_numbers_but_never_counts_it_as_done(client, django_user_model):
    login(client, django_user_model)
    person = EmployeeFactory()
    module = TrainingModuleFactory()
    waived = TrainingAssignmentFactory(employee=person, module=module)
    done = TrainingAssignmentFactory(module=module)
    TrainingAssignment.objects.filter(pk=done.pk).update(completed_at=timezone.now())
    TrainingAssignment.objects.filter(pk=waived.pk).update(due_at=timezone.now() - timedelta(days=3))
    assert analytics.training_compliance(TrainingAssignment.objects.all())["overdue"] == 1

    client.post(reverse("manage:assignment-waive", args=[waived.pk]), {"reason": "Long-term leave"})
    waived.refresh_from_db()
    assert waived.waived_at and waived.waived_by and waived.waiver_reason == "Long-term leave" and waived.completed_at is None
    stats = analytics.training_compliance(TrainingAssignment.objects.all())
    assert stats["assigned"] == 1 and stats["overdue"] == 0 and stats["waived"] == 1 and stats["completion_rate_percent"] == 100.0
    assert "training_waived" in actions()


def test_a_waiver_needs_a_reason_and_cannot_be_repeated(client, django_user_model):
    login(client, django_user_model)
    assignment = TrainingAssignmentFactory()
    assert client.post(reverse("manage:assignment-waive", args=[assignment.pk]), {"reason": ""}).status_code == 200
    client.post(reverse("manage:assignment-waive", args=[assignment.pk]), {"reason": "ok"})
    assert client.get(reverse("manage:assignment-waive", args=[assignment.pk])).status_code == 302  # already waived


def test_completed_assignments_cannot_be_extended_or_waived(client, django_user_model):
    login(client, django_user_model)
    assignment = TrainingAssignmentFactory()
    TrainingAssignment.objects.filter(pk=assignment.pk).update(completed_at=timezone.now())
    assert client.get(reverse("manage:assignment-extend", args=[assignment.pk])).status_code == 302
    assert client.get(reverse("manage:assignment-waive", args=[assignment.pk])).status_code == 302


def test_a_waived_assignment_is_not_chased_by_reminders():
    assignment = TrainingAssignmentFactory()
    TrainingAssignment.objects.filter(pk=assignment.pk).update(
        assigned_at=timezone.now() - timedelta(days=10), waived_at=timezone.now(), waiver_reason="Leave")
    send_training_reminders()
    assert mail.outbox == []


def test_manual_assignment_by_department_skips_people_who_already_have_it(client, django_user_model):
    admin = login(client, django_user_model)
    department = DepartmentFactory()
    a, b = EmployeeFactory(department=department), EmployeeFactory(department=department)
    EmployeeFactory(department=department, is_active=False)
    module = TrainingModuleFactory()
    TrainingAssignmentFactory(employee=a, module=module)
    response = client.post(reverse("manage:assignment-new"), {"module": module.pk, "department": department.pk, "due_days": 7})
    assert response.status_code == 302
    assert TrainingAssignment.objects.filter(module=module).count() == 2  # a's existing one + b's new one
    assert TrainingAssignment.objects.filter(employee=b, module=module).exists()
    assert AuditLogEntry.objects.get(action="training_assigned_manually").metadata["skipped_already_open"] == 1
    assert admin  # actor recorded


def test_manual_assignment_needs_exactly_one_target(client, django_user_model):
    login(client, django_user_model)
    module = TrainingModuleFactory()
    person = EmployeeFactory()
    both = client.post(reverse("manage:assignment-new"), {"module": module.pk, "department": DepartmentFactory().pk, "email": person.email, "due_days": 7})
    neither = client.post(reverse("manage:assignment-new"), {"module": module.pk, "due_days": 7})
    assert both.status_code == 200 and neither.status_code == 200 and not TrainingAssignment.objects.exists()
    one = client.post(reverse("manage:assignment-new"), {"module": module.pk, "email": person.email.upper(), "due_days": 7})
    assert one.status_code == 302 and TrainingAssignment.objects.filter(employee=person).exists()


def test_department_manager_has_no_assignment_powers(client, django_user_model):
    department = DepartmentFactory()
    manager = login(client, django_user_model, "Department Manager")
    department.managers.add(manager)
    assignment = TrainingAssignmentFactory(employee=EmployeeFactory(department=department))
    assert client.get(reverse("manage:assignment-waive", args=[assignment.pk])).status_code == 403
    assert client.get(reverse("manage:assignment-extend", args=[assignment.pk])).status_code == 403


def test_the_assignments_page_filters_by_status(client, django_user_model):
    login(client, django_user_model)
    open_ = TrainingAssignmentFactory(employee=EmployeeFactory(full_name="Owes Training"))
    done = TrainingAssignmentFactory(employee=EmployeeFactory(full_name="Finished Training"))
    TrainingAssignment.objects.filter(pk=done.pk).update(completed_at=timezone.now())
    page = client.get(reverse("manage:assignments")).content.decode()
    assert "Owes Training" in page and "Finished Training" not in page
    assert "Finished Training" in client.get(reverse("manage:assignments"), {"status": "completed"}).content.decode()
    assert open_


# --- reports stay reproducible ------------------------------------------------------------


def _report(as_of):
    scope = ReportScope(employees=Employee.objects.all(), campaigns=Campaign.objects.all(),
                        assignments=TrainingAssignment.objects.all(), departments=None, label="All", is_scoped=False)
    return ReportData(scope, date(2026, 1, 1), as_of.date(), now=as_of)


def test_a_later_extension_or_waiver_does_not_rewrite_an_earlier_report():
    def at(m, d):
        return datetime(2026, m, d, 12, tzinfo=tz.utc)

    assignment = TrainingAssignmentFactory()
    TrainingAssignment.objects.filter(pk=assignment.pk).update(assigned_at=at(3, 1), due_at=at(5, 1))
    assignment.refresh_from_db()
    extension = TrainingExtension.objects.create(
        assignment=assignment, previous_due_at=at(3, 15), new_due_at=at(5, 1), reason="Leave")
    TrainingExtension.objects.filter(pk=extension.pk).update(extended_at=at(3, 20))
    TrainingAssignment.objects.filter(pk=assignment.pk).update(waived_at=at(6, 1))

    before_extension = _report(at(3, 18)).training_rows[0]   # due 15 Mar had passed, nothing extended yet
    after_extension = _report(at(4, 1)).training_rows[0]     # extended to 1 May
    after_waiver = _report(at(6, 15))
    assert before_extension["status"] == "Overdue" and before_extension["due"] == "2026-03-15"
    assert after_extension["status"] == "Assigned" and after_extension["due"] == "2026-05-01"
    assert after_waiver.training_rows[0]["status"] == "Waived"
    assert after_waiver.training_totals["assigned"] == 0 and after_waiver.training_totals["waived"] == 1


# --- program overview ---------------------------------------------------------------------


def test_governance_page_shows_health_frameworks_and_policy(client, django_user_model):
    login(client, django_user_model)
    EmployeeFactory(is_exempt=True)  # exempt with no reason and no end date
    page = client.get(reverse("manage:governance")).content.decode()
    assert "Control health" in page and "ISO/IEC 27001:2022" in page and "Policy settings in force" in page
    assert "No reason recorded" not in page  # that detail lives on the register
    assert "1 exempt employee(s) have no reason recorded." in page
    assert "does not certify compliance" in page


def test_control_health_reports_honestly_on_an_empty_program():
    checks = {c["label"]: c for c in governance.control_health()}
    assert checks["Simulations are being run"]["status"] == "attention"
    assert checks["Admin sign-in uses MFA"]["status"] == "attention"  # the accepted risk is never hidden
    assert checks["Access is reviewed periodically"]["status"] == "attention"
    summary = governance.summarise(list(checks.values()))
    assert summary["attention"] >= 3
