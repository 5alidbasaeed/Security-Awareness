import pytest

from apps.events.models import Event
from apps.events.tests.factories import EventFactory

pytestmark = pytest.mark.django_db


def test_event_cannot_be_resaved():
    event = EventFactory()
    event.event_type = Event.EventType.EMAIL_OPENED
    with pytest.raises(TypeError):
        event.save()


def test_event_queryset_update_blocked():
    EventFactory()
    with pytest.raises(TypeError):
        Event.objects.update(event_type=Event.EventType.EMAIL_OPENED)


def test_event_queryset_delete_blocked():
    EventFactory()
    with pytest.raises(TypeError):
        Event.objects.all().delete()
