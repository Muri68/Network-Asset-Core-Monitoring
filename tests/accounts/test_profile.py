import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from apps.accounts import roles, services
from apps.accounts.models import User
from tests.factories import PASSWORD

PROFILE = reverse("accounts:profile")


@pytest.mark.django_db
class TestProfile:
    def test_change_password_keeps_session(self, as_user, viewer):
        client = as_user(viewer)
        new = "another-long-passphrase"
        response = client.post(reverse("accounts:profile_password"), {
            "current_password": PASSWORD, "new_password1": new, "new_password2": new,
        })
        assert response.url == PROFILE
        viewer.refresh_from_db()
        assert viewer.check_password(new)
        assert client.get(PROFILE).status_code == 200  # still signed in

    def test_change_password_needs_current(self, as_user, viewer):
        response = as_user(viewer).post(reverse("accounts:profile_password"), {
            "current_password": "wrong", "new_password1": "another-long-passphrase",
            "new_password2": "another-long-passphrase",
        })
        assert response.status_code == 400 and "current password is incorrect" in response.content.decode()

    def test_change_reveal_code(self, as_user, engineer):
        response = as_user(engineer).post(reverse("accounts:profile_reveal_code"), {
            "current_password": PASSWORD, "code1": "new-code-77", "code2": "new-code-77",
        })
        assert response.status_code == 302
        engineer.profile.refresh_from_db()
        assert services.check_reveal_code(engineer, "new-code-77")
        assert "new-code-77" not in engineer.profile.reveal_code_hash  # hashed, never stored as text

    def test_reveal_code_rules(self, as_user, engineer):
        client = as_user(engineer)
        cases = [
            ({"current_password": "wrong", "code1": "abcdef1", "code2": "abcdef1"}, "current password is incorrect"),
            ({"current_password": PASSWORD, "code1": "abc", "code2": "abc"}, "at least 6 characters"),
            ({"current_password": PASSWORD, "code1": "abcdef1", "code2": "abcdef2"}, "don&#x27;t match"),
            ({"current_password": PASSWORD, "code1": PASSWORD, "code2": PASSWORD}, "different from your login"),
        ]
        for data, message in cases:
            response = client.post(reverse("accounts:profile_reveal_code"), data)
            assert response.status_code == 400 and message in response.content.decode(), message
        assert services.check_reveal_code(engineer, "reveal-123")  # unchanged

    def test_viewer_profile_has_no_reveal_code_section(self, as_user, viewer):
        assert "Vault reveal code" not in as_user(viewer).get(PROFILE).content.decode()

    def test_regenerate_backup_codes(self, as_user, viewer):
        client = as_user(viewer)
        old = services.new_backup_codes(viewer)
        response = client.post(reverse("accounts:profile_backup_codes"), {"password": PASSWORD})
        assert response.url == reverse("accounts:backup_codes")
        page = client.get(response.url).content.decode()
        assert old[0] not in page and services.backup_codes_left(viewer) == 10


@pytest.mark.django_db
class TestBootstrapAdmin:
    def test_creates_admin(self):
        call_command("bootstrap_admin", "--username", "Boss", "--full-name", "The Boss", "--password", PASSWORD)
        user = User.objects.get(username="boss")
        assert user.role == roles.ADMIN and not user.must_change_password and user.check_password(PASSWORD)

    def test_refuses_when_admin_exists(self, admin):
        with pytest.raises(CommandError, match="already exists"):
            call_command("bootstrap_admin", "--username", "boss2", "--password", PASSWORD)

    def test_rejects_weak_password(self):
        with pytest.raises(CommandError):
            call_command("bootstrap_admin", "--username", "boss", "--password", "12345")
