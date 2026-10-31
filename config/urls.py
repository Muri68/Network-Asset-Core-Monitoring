from django.urls import include, path

urlpatterns = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.inventory.urls")),
    path("", include("apps.dcim.urls")),
    path("", include("apps.ui.urls")),
]
