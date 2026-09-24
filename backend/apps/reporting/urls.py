from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.reports, name="index"),
    path("<int:report_id>/download/", views.download, name="download"),
]
