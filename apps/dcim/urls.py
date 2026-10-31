from django.urls import path

from . import views

app_name = "dcim"

urlpatterns = [
    path("vlans/", views.vlans, name="vlans"),
    path("vlans/new/", views.vlan_create, name="vlan_new"),
    path("vlans/<int:pk>/edit/", views.vlan_edit, name="vlan_edit"),
    path("vlans/<int:pk>/delete/", views.vlan_delete, name="vlan_delete"),
    path("connections/", views.connections, name="connections"),
    path("connections/new/", views.connection_create, name="connection_new"),
    path("connections/<int:pk>/edit/", views.connection_edit, name="connection_edit"),
    path("connections/<int:pk>/delete/", views.connection_delete, name="connection_delete"),
    path("connections/<int:pk>/trace/", views.connection_trace, name="connection_trace"),
    path("ports/options/", views.port_options, name="port_options"),
    path("ports/preview/", views.pattern_preview, name="pattern_preview"),
    path("ports/<int:pk>/edit/", views.port_edit, name="port_edit"),
    path("ports/<int:pk>/delete/", views.port_delete, name="port_delete"),
    path("devices/<int:device_pk>/ports/add/", views.ports_bulk_add, name="ports_bulk_add"),
    path("devices/<int:device_pk>/ports/template/", views.ports_apply_template, name="ports_apply_template"),
]
