from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("v1/whoami", views.whoami, name="whoami"),
    path("v1/training-modules", views.training_modules, name="training-modules"),
    path("v1/email-templates", views.email_templates, name="email-templates"),
    path("v1/landing-pages", views.landing_pages, name="landing-pages"),
    path("v1/campaigns", views.campaigns, name="campaigns"),
]
