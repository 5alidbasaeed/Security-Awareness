"""
Basic RBAC per CLAUDE.md/plan doc: "admin vs. viewer, via Django's built-in
permissions" — not a custom RBAC system yet, that's Phase 3's granular
RBAC (Security Admin, Campaign Manager, Training Manager, Report Viewer,
Department Manager). Idempotent — safe to re-run after adding new models.

MANAGED_MODELS must be updated whenever a new app/model is added — it was
missed for the whole training app in Phase 2 (six models with zero
Admin/Viewer permissions until this fix), found on review. There's no
system check that catches a forgotten entry here; re-run this command after
any new model lands, and sanity-check the printed permission counts look
right for what you just added.
"""

from functools import reduce
from operator import or_

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Q

MANAGED_MODELS = [
    ("employees", "employee"),
    ("employees", "department"),
    ("campaigns", "campaign"),
    ("events", "event"),
    ("core", "auditlogentry"),
    ("training", "trainingmodule"),
    ("training", "quiz"),
    ("training", "quizquestion"),
    ("training", "quizchoice"),
    ("training", "trainingassignment"),
    ("training", "quizattempt"),
]


class Command(BaseCommand):
    help = "Creates/updates the Admin and Viewer groups with Phase 1 model permissions."

    def handle(self, *args, **options):
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        viewer_group, _ = Group.objects.get_or_create(name="Viewer")

        model_filter = reduce(
            or_,
            (Q(content_type__app_label=app_label, content_type__model=model) for app_label, model in MANAGED_MODELS),
        )
        admin_perms = Permission.objects.filter(model_filter)
        viewer_perms = admin_perms.filter(codename__startswith="view_")

        admin_group.permissions.set(admin_perms)
        viewer_group.permissions.set(viewer_perms)

        self.stdout.write(
            self.style.SUCCESS(
                f"Admin group: {admin_perms.count()} permissions. Viewer group: {viewer_perms.count()} permissions."
            )
        )
