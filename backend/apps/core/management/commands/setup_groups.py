"""
Basic RBAC per CLAUDE.md/plan doc: "admin vs. viewer, via Django's built-in
permissions" — not a custom RBAC system yet, that's Phase 3's granular
RBAC (Security Admin, Campaign Manager, Training Manager, Report Viewer,
Department Manager). Idempotent — safe to re-run after adding new models.
"""

from functools import reduce
from operator import or_

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Q

PHASE_1_MODELS = [
    ("employees", "employee"),
    ("employees", "department"),
    ("campaigns", "campaign"),
    ("events", "event"),
    ("core", "auditlogentry"),
]


class Command(BaseCommand):
    help = "Creates/updates the Admin and Viewer groups with Phase 1 model permissions."

    def handle(self, *args, **options):
        admin_group, _ = Group.objects.get_or_create(name="Admin")
        viewer_group, _ = Group.objects.get_or_create(name="Viewer")

        model_filter = reduce(
            or_,
            (Q(content_type__app_label=app_label, content_type__model=model) for app_label, model in PHASE_1_MODELS),
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
