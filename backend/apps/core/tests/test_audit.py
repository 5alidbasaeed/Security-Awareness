import pytest

from apps.core.audit import log_action
from apps.core.models import AuditLogEntry

pytestmark = pytest.mark.django_db


def test_over_long_target_description_is_truncated_not_a_database_error():
    # e.g. str(TrainingAssignment) = employee name + email + module title can exceed 500 chars.
    entry = log_action(actor=None, action="training_assignment_started", target_description="x" * 700)

    entry.refresh_from_db()
    max_length = AuditLogEntry._meta.get_field("target_description").max_length
    assert len(entry.target_description) == max_length
    assert entry.target_description.endswith("…")
