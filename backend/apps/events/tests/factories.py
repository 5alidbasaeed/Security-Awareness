import factory
from django.utils import timezone

from apps.campaigns.tests.factories import CampaignFactory
from apps.employees.tests.factories import EmployeeFactory
from apps.events.models import Event


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    event_type = Event.EventType.LINK_CLICKED
    employee = factory.SubFactory(EmployeeFactory)
    campaign = factory.SubFactory(CampaignFactory)
    source = Event.Source.MANUAL
    occurred_at = factory.LazyFunction(timezone.now)
