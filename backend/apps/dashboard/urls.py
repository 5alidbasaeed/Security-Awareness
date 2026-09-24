from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("campaigns/", views.campaigns, name="campaigns"),
    path("campaigns/<int:campaign_id>/", views.campaign_detail, name="campaign-detail"),
    path("employees/", views.employees, name="employees"),
    path("employees/<int:employee_id>/", views.employee_detail, name="employee-detail"),
    path("departments/", views.departments, name="departments"),
    path("departments/<int:department_id>/", views.department_detail, name="department-detail"),
    path("training/", views.training, name="training"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="dashboard/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
