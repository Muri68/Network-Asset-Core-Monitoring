from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/live/", views.dashboard_live, name="dashboard_live"),
    # Monitoring
    path("monitors/", views.monitors, name="monitors"),
    path("monitors/new/", views.monitor_form, name="monitor_new"),
    path("monitors/<int:pk>/", views.monitor_detail, name="monitor_detail"),
    path("incidents/", views.incidents, name="incidents"),
    path("maintenance/", views.maintenance, name="maintenance"),
    # Infrastructure
    # IPAM
    # Security
    path("vault/", views.vault, name="vault"),
    path("vault/<int:pk>/", views.credential_detail, name="credential_detail"),
    path("vault/<int:pk>/reveal/", views.credential_reveal, name="credential_reveal"),
    path("vault/log/", views.reveal_log, name="reveal_log"),
    # Administration
    path("data/", views.data, name="data"),
    path("data/import/preview/", views.import_preview, name="import_preview"),
    path("settings/", views.settings_page, name="settings"),
]
