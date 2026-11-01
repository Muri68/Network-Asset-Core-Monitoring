from django.urls import path

from . import views

app_name = "dataio"

urlpatterns = [
    path("data/", views.data, name="data"),
    path("data/import/preview/", views.preview, name="preview"),
    path("data/import/confirm/", views.confirm, name="confirm"),
    path("data/template/<str:key>.<str:fmt>", views.template, name="template"),
    path("data/export/<str:key>.<str:fmt>", views.export, name="export"),
]
