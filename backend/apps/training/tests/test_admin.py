import pytest
from django.contrib.auth.models import Group

from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand

pytestmark = pytest.mark.django_db

TRAINING_MODELS = ("trainingmodule", "quiz", "quizquestion", "quizchoice", "trainingassignment", "quizattempt")


def test_security_admin_can_manage_training_models():
    """
    Regression test: Phase 2 added six training models but setup_groups
    wasn't updated to grant permissions on them, so a non-superuser Admin
    couldn't touch training data in the admin at all. Found on review.
    """
    SetupGroupsCommand().handle()
    group = Group.objects.get(name="Security Admin")
    codenames = set(group.permissions.values_list("codename", flat=True))

    for model in TRAINING_MODELS:
        for action in ("add", "change", "delete", "view"):
            assert f"{action}_{model}" in codenames, f"Security Admin missing {action}_{model}"


def test_training_manager_can_manage_training_models():
    SetupGroupsCommand().handle()
    group = Group.objects.get(name="Training Manager")
    codenames = set(group.permissions.values_list("codename", flat=True))

    for model in TRAINING_MODELS:
        for action in ("add", "change", "delete", "view"):
            assert f"{action}_{model}" in codenames, f"Training Manager missing {action}_{model}"

    # Training Manager should not have campaign-launching power.
    assert "change_campaign" not in codenames
