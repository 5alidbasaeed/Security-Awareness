"""
Granular RBAC per the plan doc's Phase 3 scope: Security Admin, Campaign
Manager, Training Manager, Report Viewer, Department Manager. Replaces the
Phase 1 two-group (Admin/Viewer) setup — this command renames those groups
in place rather than leaving orphaned ones behind.

Department Manager's department-scoping (only their own department's
employees/campaigns) is NOT expressed here — Django's built-in permission
framework is model-level only. That's enforced in apps/employees/admin.py
and apps/campaigns/admin.py via get_queryset()/has_change_permission()
overrides keyed off Department.managers. This command only grants the
model-level "can change Employee/Campaign at all" permission; the
row-level "which ones" is admin-code, not a Django Permission.

MANAGED_MODELS must be updated whenever a new app/model is added — see the
code-review-checklist skill; this was missed once already for Phase 2's
training app.
"""

from functools import reduce
from operator import or_

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Q

EMPLOYEE_MODELS = [("employees", "employee"), ("employees", "department")]
CAMPAIGN_MODELS = [("campaigns", "campaign")]
EVENT_MODELS = [("events", "event")]
CORE_MODELS = [("core", "auditlogentry")]
TRAINING_MODELS = [
    ("training", "trainingmodule"),
    ("training", "quiz"),
    ("training", "quizquestion"),
    ("training", "quizchoice"),
    ("training", "trainingassignment"),
    ("training", "quizattempt"),
]
RISK_MODELS = [("risk_scoring", "riskscoresnapshot")]  # computed, read-only in the admin
MANAGED_MODELS = EMPLOYEE_MODELS + CAMPAIGN_MODELS + EVENT_MODELS + CORE_MODELS + TRAINING_MODELS + RISK_MODELS


def _perms_for(models, codename_prefixes=("add_", "change_", "delete_", "view_")):
    model_filter = reduce(or_, (Q(content_type__app_label=a, content_type__model=m) for a, m in models))
    perms = Permission.objects.filter(model_filter)
    if codename_prefixes:
        prefix_filter = reduce(or_, (Q(codename__startswith=p) for p in codename_prefixes))
        perms = perms.filter(prefix_filter)
    return perms


class Command(BaseCommand):
    help = "Creates/updates the five Phase 3 RBAC groups with their model permissions."

    def handle(self, *args, **options):
        # Migrate Phase 1's two-group names in place rather than leaving them orphaned.
        Group.objects.filter(name="Admin").update(name="Security Admin")
        Group.objects.filter(name="Viewer").update(name="Report Viewer")

        security_admin, _ = Group.objects.get_or_create(name="Security Admin")
        campaign_manager, _ = Group.objects.get_or_create(name="Campaign Manager")
        training_manager, _ = Group.objects.get_or_create(name="Training Manager")
        report_viewer, _ = Group.objects.get_or_create(name="Report Viewer")
        department_manager, _ = Group.objects.get_or_create(name="Department Manager")

        # Security Admin: everything, including the custom approve_campaign permission.
        security_admin_perms = list(_perms_for(MANAGED_MODELS)) + list(
            Permission.objects.filter(codename="approve_campaign")
        )
        security_admin.permissions.set(security_admin_perms)

        # Campaign Manager: manage campaigns (not delete, not approve), view targets/results.
        campaign_manager_perms = list(_perms_for(CAMPAIGN_MODELS, ("add_", "change_", "view_"))) + list(
            _perms_for(EMPLOYEE_MODELS + EVENT_MODELS + RISK_MODELS, ("view_",))
        )
        campaign_manager.permissions.set(campaign_manager_perms)

        # Training Manager: full control of training content/assignments, needs to see who employees are.
        training_manager_perms = list(_perms_for(TRAINING_MODELS)) + list(_perms_for(EMPLOYEE_MODELS, ("view_",)))
        training_manager.permissions.set(training_manager_perms)

        # Report Viewer: view-only, everywhere.
        report_viewer.permissions.set(_perms_for(MANAGED_MODELS, ("view_",)))

        # Department Manager: change employees/campaigns (row-scoping is admin-code, see module docstring).
        # Department itself is view-only: change_department would let them add
        # themselves to any department's managers and escape their own scope.
        department_manager_perms = list(
            _perms_for([("employees", "employee")] + CAMPAIGN_MODELS, ("change_", "view_"))
        ) + list(_perms_for([("employees", "department")] + RISK_MODELS, ("view_",)))
        department_manager.permissions.set(department_manager_perms)

        self.stdout.write(
            self.style.SUCCESS(
                "Security Admin: {sa} | Campaign Manager: {cm} | Training Manager: {tm} | "
                "Report Viewer: {rv} | Department Manager: {dm} permissions".format(
                    sa=security_admin.permissions.count(),
                    cm=campaign_manager.permissions.count(),
                    tm=training_manager.permissions.count(),
                    rv=report_viewer.permissions.count(),
                    dm=department_manager.permissions.count(),
                )
            )
        )
