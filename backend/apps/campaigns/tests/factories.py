import factory

from apps.campaigns.models import Campaign
from apps.employees.tests.factories import DepartmentFactory


class CampaignFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Campaign

    name = factory.Sequence(lambda n: f"Campaign {n}")
    template_name = "Q1 Security Awareness"
    landing_page_url = "https://phish.example.com/landing"
    target_department = factory.SubFactory(DepartmentFactory)
