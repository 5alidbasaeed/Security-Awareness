from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("gophish", views.gophish_webhook, name="gophish-webhook"),
]
