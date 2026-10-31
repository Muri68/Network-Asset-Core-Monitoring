from django.urls import include, path

urlpatterns = [
    path("", include("apps.ui.urls")),
]
