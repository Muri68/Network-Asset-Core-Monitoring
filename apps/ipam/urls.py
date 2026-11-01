from django.urls import path

from . import views

app_name = "ipam"

urlpatterns = [
    path("ipam/prefixes/", views.prefixes, name="prefixes"),
    path("ipam/prefixes/new/", views.prefix_create, name="prefix_new"),
    path("ipam/prefixes/<int:pk>/", views.prefix_detail, name="prefix_detail"),
    path("ipam/prefixes/<int:pk>/edit/", views.prefix_edit, name="prefix_edit"),
    path("ipam/prefixes/<int:pk>/delete/", views.prefix_delete, name="prefix_delete"),
    path("ipam/ips/new/", views.ip_create, name="ip_new"),
    path("ipam/ips/<int:pk>/edit/", views.ip_edit, name="ip_edit"),
    path("ipam/ips/<int:pk>/delete/", views.ip_delete, name="ip_delete"),
    path("ipam/isps/", views.isps, name="isps"),
    path("ipam/isps/new/", views.isp_create, name="isp_new"),
    path("ipam/isps/<int:pk>/edit/", views.isp_edit, name="isp_edit"),
    path("ipam/isps/<int:pk>/delete/", views.isp_delete, name="isp_delete"),
    path("ipam/circuits/new/", views.circuit_create, name="circuit_new"),
    path("ipam/circuits/<int:pk>/edit/", views.circuit_edit, name="circuit_edit"),
    path("ipam/circuits/<int:pk>/delete/", views.circuit_delete, name="circuit_delete"),
    path("ipam/calculator/", views.calculator, name="calculator"),
]
