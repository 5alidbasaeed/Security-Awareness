import pytest
from django.contrib.auth.models import Group

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand

pytestmark = pytest.mark.django_db


def test_admin_group_can_manage_training_models():
    """
    Regression test: Phase 2 added six training models but setup_groups
    wasn't updated to grant permissions on them, so a non-superuser Admin
    couldn't touch training data in the admin at all. Found on review.
    """
    SetupGroupsCommand().handle()
    admin_group = Group.objects.get(name="Admin")
    codenames = set(admin_group.permissions.values_list("codename", flat=True))

    for model in ("trainingmodule", "quiz", "quizquestion", "quizchoice", "trainingassignment", "quizattempt"):
        for action in ("add", "change", "delete", "view"):
            assert f"{action}_{model}" in codenames, f"Admin group missing {action}_{model}"
