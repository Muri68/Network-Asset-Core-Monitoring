from django.urls import path

from . import views

app_name = "monitoring"

urlpatterns = [
    path("monitors/", views.monitors, name="monitors"),
    path("monitors/new/", views.monitor_create, name="monitor_new"),
    path("monitors/<int:pk>/", views.monitor_detail, name="monitor_detail"),
    path("monitors/<int:pk>/edit/", views.monitor_edit, name="monitor_edit"),
    path("monitors/<int:pk>/delete/", views.monitor_delete, name="monitor_delete"),
    path("monitors/<int:pk>/<str:action>/", views.monitor_action, name="monitor_action"),
    path("incidents/", views.incidents, name="incidents"),
    path("incidents/<int:pk>/acknowledge/", views.incident_ack, name="incident_ack"),
    path("maintenance/", views.maintenance, name="maintenance"),
    path("maintenance/new/", views.maintenance_create, name="maintenance_new"),
    path("maintenance/<int:pk>/edit/", views.maintenance_edit, name="maintenance_edit"),
    path("maintenance/<int:pk>/end/", views.maintenance_end, name="maintenance_end"),
    path("maintenance/<int:pk>/delete/", views.maintenance_delete, name="maintenance_delete"),
]
