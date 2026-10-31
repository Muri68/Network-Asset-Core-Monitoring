from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/new-password/", views.login_password, name="login_password"),
    path("login/2fa/setup/", views.login_2fa_setup, name="login_2fa_setup"),
    path("login/2fa/", views.login_2fa, name="login_2fa"),
    path("logout/", views.logout_view, name="logout"),
    path("account/backup-codes/", views.backup_codes, name="backup_codes"),
    path("account/reveal-code/", views.reveal_code_setup, name="reveal_code_setup"),
    path("profile/", views.profile, name="profile"),
    path("profile/password/", views.profile_password, name="profile_password"),
    path("profile/reveal-code/", views.profile_reveal_code, name="profile_reveal_code"),
    path("profile/backup-codes/", views.profile_backup_codes, name="profile_backup_codes"),
    path("users/", views.users, name="users"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/", views.user_edit, name="user_edit"),
    path(
        "users/<int:pk>/<str:action>/",
        views.user_action,
        name="user_action",
    ),
]
