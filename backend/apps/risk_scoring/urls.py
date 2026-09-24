from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("departments/", views.departments, name="departments"),
    path("departments/<int:department_id>/trend/", views.department_trend, name="department-trend"),
    path("campaigns/<int:campaign_id>/", views.campaign, name="campaign"),
    path("employees/<int:employee_id>/history/", views.employee_history, name="employee-history"),
]
