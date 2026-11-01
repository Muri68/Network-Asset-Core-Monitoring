from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/live/", views.dashboard_live, name="dashboard_live"),
    # Administration (sample data until Phase 8 / later settings)
    path("data/", views.data, name="data"),
    path("data/import/preview/", views.import_preview, name="import_preview"),
    path("settings/", views.settings_page, name="settings"),
]
