from django.urls import path

from . import views

app_name = "alerts"

urlpatterns = [
    path("settings/alerts/", views.settings_save, name="settings_save"),
    path("settings/alerts/test/", views.settings_test, name="settings_test"),
    path("profile/telegram/", views.card, name="card"),
    path("profile/telegram/link/", views.link, name="link"),
    path("profile/telegram/unlink/", views.unlink, name="unlink"),
    path("profile/telegram/mute/", views.mute, name="mute"),
    path("profile/telegram/test/", views.test, name="test"),
]
