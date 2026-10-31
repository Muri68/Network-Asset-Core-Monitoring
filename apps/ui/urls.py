from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/live/", views.dashboard_live, name="dashboard_live"),
    path("login/", views.login, name="login"),
    path("login/verify/", views.login_2fa, name="login_2fa"),
    # Monitoring
    path("monitors/", views.monitors, name="monitors"),
    path("monitors/new/", views.monitor_form, name="monitor_new"),
    path("monitors/<int:pk>/", views.monitor_detail, name="monitor_detail"),
    path("incidents/", views.incidents, name="incidents"),
    path("maintenance/", views.maintenance, name="maintenance"),
    # Infrastructure
    path("devices/", views.devices, name="devices"),
    path("devices/new/", views.device_form, name="device_new"),
    path("devices/<int:pk>/", views.device_detail, name="device_detail"),
    path("racks/", views.racks, name="racks"),
    path("racks/<int:pk>/", views.rack_detail, name="rack_detail"),
    path("connections/", views.connections, name="connections"),
    path("connections/<int:pk>/trace/", views.connection_trace, name="connection_trace"),
    path("vlans/", views.vlans, name="vlans"),
    # IPAM
    path("ipam/prefixes/", views.prefixes, name="prefixes"),
    path("ipam/prefixes/<int:pk>/", views.prefix_detail, name="prefix_detail"),
    path("ipam/isps/", views.isps, name="isps"),
    path("ipam/calculator/", views.calculator, name="calculator"),
    # Security
    path("vault/", views.vault, name="vault"),
    path("vault/<int:pk>/", views.credential_detail, name="credential_detail"),
    path("vault/<int:pk>/reveal/", views.credential_reveal, name="credential_reveal"),
    path("vault/log/", views.reveal_log, name="reveal_log"),
    path("audit/", views.audit, name="audit"),
    # Administration
    path("users/", views.users, name="users"),
    path("profile/", views.profile, name="profile"),
    path("data/", views.data, name="data"),
    path("data/import/preview/", views.import_preview, name="import_preview"),
    path("settings/", views.settings_page, name="settings"),
]
