from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("audit/", views.audit_log, name="log"),
    path("audit/export.csv", views.audit_export, name="export"),
]
