from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/live/", views.dashboard_live, name="dashboard_live"),
    path("settings/", views.settings_page, name="settings"),
    path("health/", views.health, name="health"),
]
