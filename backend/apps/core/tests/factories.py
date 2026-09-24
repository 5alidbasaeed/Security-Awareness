from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import DepartmentFactory, EmployeeFactory
from apps.events.models import Event
from apps.events.tests.factories import EventFactory
from apps.risk_scoring.services import compute_and_store_snapshot
from apps.training.tests.factories import QuizChoiceFactory, TrainingAssignmentFactory


def make_sample_data() -> dict:
    """One row of everything, so admin pages have something to render."""
    department = DepartmentFactory()
    employee = EmployeeFactory(department=department)
    campaign = CampaignFactory(target_department=department)
    event = EventFactory(employee=employee, campaign=campaign, event_type=Event.EventType.LINK_CLICKED)
    choice = QuizChoiceFactory()
    assignment = TrainingAssignmentFactory(employee=employee)
    snapshot = compute_and_store_snapshot(employee)
    return {
        "department": department,
        "objects": [department, employee, campaign, event, choice.question.quiz.module, choice.question.quiz,
                    choice.question, assignment, snapshot],
    }
