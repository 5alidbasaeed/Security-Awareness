from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.home, name="home"),
    path("sign-in/", views.login, name="login"),
    path("enter/<str:token>/", views.enter, name="enter"),
    path("sign-out/", views.logout, name="logout"),
    path("training/<int:pk>/", views.assignment, name="assignment"),
    path("training/<int:pk>/quiz/", views.submit_quiz, name="submit-quiz"),
    path("training/<int:pk>/certificate/", views.certificate, name="certificate"),
    path("report/", views.report_email, name="report"),
    path("learn/", views.learn, name="learn"),
]
