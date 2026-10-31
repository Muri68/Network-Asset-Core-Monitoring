from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("devices/", views.devices, name="devices"),
    path("devices/new/", views.device_create, name="device_new"),
    path("devices/<int:pk>/", views.device_detail, name="device_detail"),
    path("devices/<int:pk>/edit/", views.device_edit, name="device_edit"),
    path("devices/<int:pk>/retire/", views.device_retire, name="device_retire"),
    path("devices/<int:pk>/delete/", views.device_delete, name="device_delete"),
    path("device-models/", views.device_models, name="device_models"),
    path("device-models/<int:pk>/edit/", views.device_model_edit, name="device_model_edit"),
    path("racks/", views.racks, name="racks"),
    path("racks/new/", views.rack_create, name="rack_new"),
    path("racks/check/", views.rack_check, name="rack_check"),
    path("racks/<int:pk>/", views.rack_detail, name="rack_detail"),
    path("racks/<int:pk>/edit/", views.rack_edit, name="rack_edit"),
    path("racks/<int:pk>/delete/", views.rack_delete, name="rack_delete"),
    path("locations/new/", views.location_create, name="location_new"),
    path("locations/<int:pk>/edit/", views.location_edit, name="location_edit"),
    path("locations/<int:pk>/delete/", views.location_delete, name="location_delete"),
]
