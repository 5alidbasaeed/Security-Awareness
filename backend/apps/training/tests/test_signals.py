import pytest

from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.training.models import TrainingAssignment
from apps.training.tests.factories import TrainingModuleFactory

pytestmark = pytest.mark.django_db


def test_credential_attempt_auto_assigns_training():
    module = TrainingModuleFactory()
    campaign = CampaignFactory(training_module=module)
    employee = EmployeeFactory()

    EventFactory(event_type=Event.EventType.CREDENTIAL_ATTEMPT, employee=employee, campaign=campaign)

    assignment = TrainingAssignment.objects.get()
    assert assignment.employee == employee
    assert assignment.module == module
    assert assignment.completed_at is None


def test_email_opened_does_not_assign_training():
    module = TrainingModuleFactory()
    campaign = CampaignFactory(training_module=module)
    employee = EmployeeFactory()

    EventFactory(event_type=Event.EventType.EMAIL_OPENED, employee=employee, campaign=campaign)

    assert TrainingAssignment.objects.count() == 0


def test_campaign_without_training_module_assigns_nothing():
    campaign = CampaignFactory(training_module=None)
    employee = EmployeeFactory()

    EventFactory(event_type=Event.EventType.LINK_CLICKED, employee=employee, campaign=campaign)

    assert TrainingAssignment.objects.count() == 0


def test_repeat_failure_does_not_duplicate_outstanding_assignment():
    module = TrainingModuleFactory()
    campaign = CampaignFactory(training_module=module)
    employee = EmployeeFactory()

    EventFactory(event_type=Event.EventType.LINK_CLICKED, employee=employee, campaign=campaign)
    EventFactory(event_type=Event.EventType.CREDENTIAL_ATTEMPT, employee=employee, campaign=campaign)

    assert TrainingAssignment.objects.filter(employee=employee, module=module).count() == 1
