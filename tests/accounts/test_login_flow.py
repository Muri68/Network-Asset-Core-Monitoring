"""Sign-in: password → (new password) → 2FA setup / 2FA code → logged in."""

import pytest
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import roles, services
from tests.conftest import token_for
from tests.factories import PASSWORD, make_user

LOGIN = reverse("accounts:login")
DASH = reverse("ui:dashboard")


def password_step(client, username, password=PASSWORD, next_url=None):
    url = LOGIN + (f"?next={next_url}" if next_url else "")
    return client.post(url, {"username": username, "password": password})


def is_logged_in(client):
    return client.get(DASH).status_code == 200


@pytest.mark.django_db
class TestPasswordStep:
    def test_wrong_password(self, client, viewer):
        response = password_step(client, viewer.username, "wrong-password")
        assert response.status_code == 200
        assert "Incorrect username or password" in response.content.decode()
        assert not is_logged_in(client)

    def test_unknown_user(self, client):
        response = password_step(client, "nobody")
        assert "Incorrect username or password" in response.content.decode()

    def test_correct_password_alone_does_not_log_in(self, client, viewer):
        response = password_step(client, viewer.username)
        assert response.status_code == 302 and response.url == reverse("accounts:login_2fa")
        assert not is_logged_in(client)

    def test_email_and_case_insensitive_username(self, client, viewer):
        assert password_step(client, viewer.email.upper()).url == reverse("accounts:login_2fa")
        assert password_step(client, viewer.username.upper()).url == reverse("accounts:login_2fa")

    def test_inactive_user_rejected(self, client, viewer):
        viewer.is_active = False
        viewer.save()
        assert "Incorrect username or password" in password_step(client, viewer.username).content.decode()

    def test_lockout_after_five_failures(self, client, viewer):
        for _ in range(5):
            password_step(client, viewer.username, "wrong-password")
        response = password_step(client, viewer.username)  # even the right password is refused now
        assert response.status_code == 429
        assert "temporarily locked" in response.content.decode()
        assert viewer.username in services.login_locked_usernames()

    def test_admin_unlock_clears_lockout(self, client, viewer):
        for _ in range(5):
            password_step(client, viewer.username, "wrong-password")
        services.unlock_login(viewer)
        assert password_step(client, viewer.username).status_code == 302


@pytest.mark.django_db
class TestTwoFactor:
    def test_first_login_requires_setup(self, client):
        user = make_user(roles.VIEWER, with_2fa=False)
        assert password_step(client, user.username).url == reverse("accounts:login_2fa_setup")
        page = client.get(reverse("accounts:login_2fa_setup")).content.decode()
        assert "<svg" in page  # QR code
        assert not is_logged_in(client)

    def test_setup_with_valid_code_logs_in_and_shows_backup_codes(self, client):
        user = make_user(roles.VIEWER, with_2fa=False)
        password_step(client, user.username)
        client.get(reverse("accounts:login_2fa_setup"))
        device = TOTPDevice.objects.get(user=user, confirmed=False)
        response = client.post(reverse("accounts:login_2fa_setup"), {"token": token_for(device)})
        assert response.url == reverse("accounts:backup_codes")
        device.refresh_from_db()
        assert device.confirmed
        page = client.get(reverse("accounts:backup_codes")).content.decode()
        codes = list(StaticToken.objects.filter(device__user=user).values_list("token", flat=True))
        assert len(codes) == 10 and all(c in page for c in codes)
        # Shown once only
        assert client.get(reverse("accounts:backup_codes")).status_code == 302
        assert is_logged_in(client)

    def test_setup_with_wrong_code(self, client):
        user = make_user(roles.VIEWER, with_2fa=False)
        password_step(client, user.username)
        client.get(reverse("accounts:login_2fa_setup"))
        response = client.post(reverse("accounts:login_2fa_setup"), {"token": "000000"})
        assert "didn&#x27;t match" in response.content.decode()
        assert not is_logged_in(client)

    def test_verify_with_totp(self, client, viewer):
        password_step(client, viewer.username)
        device = services.confirmed_totp(viewer)
        response = client.post(reverse("accounts:login_2fa"), {"token": token_for(device)})
        assert response.url == DASH
        assert is_logged_in(client)

    def test_verify_with_backup_code_consumes_it(self, client, viewer):
        codes = services.new_backup_codes(viewer)
        password_step(client, viewer.username)
        client.post(reverse("accounts:login_2fa"), {"token": codes[0]})
        assert is_logged_in(client)
        assert services.backup_codes_left(viewer) == 9

    def test_five_wrong_codes_restart_sign_in(self, client, viewer):
        password_step(client, viewer.username)
        for _ in range(4):
            client.post(reverse("accounts:login_2fa"), {"token": "000000"})
        response = client.post(reverse("accounts:login_2fa"), {"token": "000000"})
        assert response.url == LOGIN
        # The pending sign-in is gone: the 2FA page now sends you back to the password step
        assert client.get(reverse("accounts:login_2fa")).url == LOGIN

    def test_2fa_page_without_password_step(self, client):
        assert client.get(reverse("accounts:login_2fa")).url == LOGIN

    def test_next_url_is_kept(self, client, viewer):
        target = reverse("inventory:devices")
        password_step(client, viewer.username, next_url=target)
        device = services.confirmed_totp(viewer)
        assert client.post(reverse("accounts:login_2fa"), {"token": token_for(device)}).url == target

    def test_external_next_url_ignored(self, client, viewer):
        password_step(client, viewer.username, next_url="https://evil.example/")
        device = services.confirmed_totp(viewer)
        assert client.post(reverse("accounts:login_2fa"), {"token": token_for(device)}).url == DASH


@pytest.mark.django_db
class TestTemporaryPassword:
    def test_new_user_full_first_login(self, client):
        """Admin-created Engineer: temp password → own password → 2FA setup → reveal code → dashboard."""
        user, temp = services.create_user(username="newbie", full_name="New Bie", email="", role=roles.ENGINEER)
        assert password_step(client, "newbie", temp).url == reverse("accounts:login_password")

        bad = client.post(reverse("accounts:login_password"), {"new_password1": "short", "new_password2": "short"})
        assert bad.status_code == 200 and not is_logged_in(client)

        new_pw = "a-much-better-passphrase"
        response = client.post(reverse("accounts:login_password"), {"new_password1": new_pw, "new_password2": new_pw})
        assert response.url == reverse("accounts:login_2fa_setup")
        user.refresh_from_db()
        assert not user.must_change_password and user.check_password(new_pw)

        client.get(reverse("accounts:login_2fa_setup"))
        device = TOTPDevice.objects.get(user=user)
        client.post(reverse("accounts:login_2fa_setup"), {"token": token_for(device)})
        next_url = client.get(reverse("accounts:backup_codes")).context["next_url"]

        # Engineers must create a reveal code before anything else
        assert client.get(next_url).url == reverse("accounts:reveal_code_setup")
        code = {"code1": "vault-code-9", "code2": "vault-code-9"}
        response = client.post(reverse("accounts:reveal_code_setup"), code)
        assert response.url == DASH
        assert services.check_reveal_code(user, "vault-code-9")
        assert is_logged_in(client)

    def test_cannot_reuse_temporary_password(self, client):
        user, temp = services.create_user(username="reuse", full_name="Re Use", email="", role=roles.VIEWER)
        password_step(client, "reuse", temp)
        response = client.post(reverse("accounts:login_password"), {"new_password1": temp, "new_password2": temp})
        assert "different from the current one" in response.content.decode()


@pytest.mark.django_db
def test_logout_requires_post(as_user, viewer):
    client = as_user(viewer)
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert client.post(reverse("accounts:logout")).url == LOGIN
    assert not is_logged_in(client)
