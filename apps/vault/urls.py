from django.urls import path

from . import views

app_name = "vault"

urlpatterns = [
    path("vault/", views.vault, name="vault"),
    path("vault/new/", views.credential_create, name="credential_new"),
    path("vault/log/", views.reveal_log, name="reveal_log"),
    path("vault/folders/new/", views.folder_form, name="folder_new"),
    path("vault/folders/<int:pk>/", views.folder_form, name="folder_edit"),
    path("vault/folders/<int:pk>/delete/", views.folder_delete, name="folder_delete"),
    path("vault/history/<int:pk>/reveal/", views.history_reveal, name="history_reveal"),
    path("vault/<int:pk>/", views.credential_detail, name="credential"),
    path("vault/<int:pk>/edit/", views.credential_edit, name="credential_edit"),
    path("vault/<int:pk>/password/", views.credential_password, name="credential_password"),
    path("vault/<int:pk>/delete/", views.credential_delete, name="credential_delete"),
    path("vault/<int:pk>/reveal/", views.credential_reveal, name="credential_reveal"),
    path("vault/<int:pk>/copied/", views.credential_copied, name="credential_copied"),
]
