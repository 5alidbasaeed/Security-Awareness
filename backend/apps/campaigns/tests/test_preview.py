from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.http import Http404
from django.test import RequestFactory

from apps.campaigns.admin import CampaignAdmin
from apps.campaigns.models import Campaign
from apps.campaigns.tests.factories import CampaignFactory
from apps.core.management.commands.setup_groups import Command as SetupGroupsCommand
from apps.employees.tests.factories import DepartmentFactory
from apps.engine.tests.fakes import FakePhishingEngineClient

pytestmark = pytest.mark.django_db


def _department_manager(django_user_model, department):
    SetupGroupsCommand().handle()
    user = django_user_model.objects.create_user(username="dm", password="x")
    user.groups.add(Group.objects.get(name="Department Manager"))
    department.managers.add(user)
    return django_user_model.objects.get(pk=user.pk)


def test_preview_returns_landing_page_html(rf: RequestFactory, django_user_model):
    department = DepartmentFactory()
    user = _department_manager(django_user_model, department)
    campaign = CampaignFactory(target_department=department, landing_page_name="Login Page")
    fake = FakePhishingEngineClient()
    fake.landing_pages["Login Page"] = "<html>hello</html>"

    request = rf.get("/")
    request.user = user
    with patch("apps.campaigns.admin.get_client", return_value=fake):
        response = CampaignAdmin(Campaign, admin.site).preview_landing_page_view(request, campaign.pk)

    assert response.content == b"<html>hello</html>"


def test_department_manager_cannot_preview_another_departments_campaign(rf: RequestFactory, django_user_model):
    user = _department_manager(django_user_model, DepartmentFactory())
    other_campaign = CampaignFactory(target_department=DepartmentFactory())

    request = rf.get("/")
    request.user = user
    with pytest.raises(Http404):
        CampaignAdmin(Campaign, admin.site).preview_landing_page_view(request, other_campaign.pk)


def test_missing_landing_page_is_a_404_not_a_500(rf: RequestFactory, django_user_model):
    department = DepartmentFactory()
    user = _department_manager(django_user_model, department)
    campaign = CampaignFactory(target_department=department, landing_page_name="Does Not Exist")

    request = rf.get("/")
    request.user = user
    with patch("apps.campaigns.admin.get_client", return_value=FakePhishingEngineClient()):
        with pytest.raises(Http404):
            CampaignAdmin(Campaign, admin.site).preview_landing_page_view(request, campaign.pk)
