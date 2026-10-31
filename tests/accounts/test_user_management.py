import pytest
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts import roles, services
from apps.accounts.models import User
from tests.factories import make_user

USERS = reverse("accounts:users")


def action(client, user, name):
    return client.post(reverse("accounts:user_action", args=[user.pk, name]))


@pytest.mark.django_db
class TestCreateUser:
    def test_admin_creates_engineer_with_temp_password(self, as_user, admin):
        client = as_user(admin)
        response = client.post(reverse("accounts:user_create"), {
            "full_name": "Grace Adeyemi", "username": "Grace", "email": "grace@example.test", "role": "Engineer",
        })
        assert response.url == USERS
        user = User.objects.get(username="grace")  # stored lower-case
        assert user.role == roles.ENGINEER and user.must_change_password

        page = client.get(USERS).content.decode()
        shown = client.get(USERS)  # second view: temp password gone
        assert "Temporary password for" in page
        assert "Temporary password for" not in shown.content.decode()
        temp = page.split('<code class="mono flex-1 text-base font-semibold tracking-wide">')[1].split("<")[0]
        assert user.check_password(temp) and len(temp) == 19

    def test_duplicate_username_and_email_rejected(self, as_user, admin, viewer):
        client = as_user(admin)
        response = client.post(reverse("accounts:user_create"), {
            "full_name": "Dup", "username": viewer.username.upper(), "email": viewer.email, "role": "Viewer",
        })
        assert response.status_code == 400
        html = response.content.decode()
        assert "already taken" in html and "already has this email" in html

    def test_email_optional(self, as_user, admin):
        client = as_user(admin)
        for name in ("noemail1", "noemail2"):
            client.post(reverse("accounts:user_create"), {"full_name": name, "username": name, "email": "",
                                                          "role": "Viewer"})
        assert User.objects.filter(username__startswith="noemail", email__isnull=True).count() == 2

    @pytest.mark.parametrize("role", [roles.ENGINEER, roles.VIEWER])
    def test_non_admin_cannot_create(self, as_user, role):
        client = as_user(make_user(role))
        response = client.post(reverse("accounts:user_create"), {"full_name": "X", "username": "x", "role": "Admin"})
        assert response.status_code == 403
        assert not User.objects.filter(username="x").exists()


@pytest.mark.django_db
class TestEditUser:
    def test_change_role(self, as_user, admin, viewer):
        client = as_user(admin)
        response = client.post(reverse("accounts:user_edit", args=[viewer.pk]), {
            "full_name": viewer.full_name, "username": viewer.username, "email": viewer.email,
            "role": "Engineer", "is_active": "on",
        })
        assert response.url == USERS
        assert User.objects.get(pk=viewer.pk).role == roles.ENGINEER

    def test_last_admin_cannot_be_demoted(self, as_user, admin):
        client = as_user(admin)
        response = client.post(reverse("accounts:user_edit", args=[admin.pk]), {
            "full_name": admin.full_name, "username": admin.username, "email": admin.email, "role": "Viewer",
        })
        assert response.status_code == 200 and "only active Admin" in response.content.decode()
        assert User.objects.get(pk=admin.pk).role == roles.ADMIN

    def test_admin_can_be_demoted_when_another_exists(self, as_user, admin):
        other = make_user(roles.ADMIN)
        client = as_user(admin)
        client.post(reverse("accounts:user_edit", args=[other.pk]), {
            "full_name": other.full_name, "username": other.username, "email": other.email, "role": "Viewer",
            "is_active": "on",
        })
        assert User.objects.get(pk=other.pk).role == roles.VIEWER

    def test_cannot_deactivate_self_via_edit(self, as_user, admin):
        make_user(roles.ADMIN)  # not the last admin, so only the self-rule applies
        client = as_user(admin)
        client.post(reverse("accounts:user_edit", args=[admin.pk]), {
            "full_name": admin.full_name, "username": admin.username, "email": admin.email, "role": "Admin",
        })
        assert User.objects.get(pk=admin.pk).is_active


@pytest.mark.django_db
class TestUserActions:
    def test_reset_password(self, as_user, admin, viewer):
        client = as_user(admin)
        action(client, viewer, "reset-password")
        viewer.refresh_from_db()
        assert viewer.must_change_password
        assert "Temporary password for" in client.get(USERS).content.decode()

    def test_reset_2fa(self, as_user, admin, viewer):
        action(as_user(admin), viewer, "reset-2fa")
        assert not TOTPDevice.objects.filter(user=viewer).exists()

    def test_deactivate_and_activate(self, as_user, admin, viewer):
        client = as_user(admin)
        action(client, viewer, "deactivate")
        assert not User.objects.get(pk=viewer.pk).is_active
        action(client, viewer, "activate")
        assert User.objects.get(pk=viewer.pk).is_active

    def test_cannot_deactivate_self(self, as_user, admin):
        action(as_user(admin), admin, "deactivate")
        assert User.objects.get(pk=admin.pk).is_active

    def test_cannot_deactivate_last_admin(self, as_user, admin):
        other = make_user(roles.ADMIN)
        client = as_user(admin)
        other.is_active = False
        other.save()
        action(client, other, "activate")
        action(client, other, "deactivate")  # fine: admin still active
        assert not User.objects.get(pk=other.pk).is_active

    def test_unlock_vault(self, as_user, admin, engineer):
        from django.utils import timezone

        engineer.profile.vault_locked_at = timezone.now()
        engineer.profile.reveal_failed_attempts = 5
        engineer.profile.save()
        action(as_user(admin), engineer, "unlock-vault")
        engineer.profile.refresh_from_db()
        assert not engineer.profile.vault_locked and engineer.profile.reveal_failed_attempts == 0

    def test_engineer_cannot_use_actions(self, as_user, engineer, viewer):
        assert action(as_user(engineer), viewer, "reset-2fa").status_code == 403
        assert TOTPDevice.objects.filter(user=viewer).exists()

    def test_actions_require_post(self, as_user, admin, viewer):
        assert as_user(admin).get(reverse("accounts:user_action", args=[viewer.pk, "reset-2fa"])).status_code == 405


@pytest.mark.django_db
def test_guard_last_admin_service(admin):
    with pytest.raises(services.LastAdminError):
        services.guard_last_admin(admin, roles.VIEWER, True)
    with pytest.raises(services.LastAdminError):
        services.guard_last_admin(admin, roles.ADMIN, False)
    services.guard_last_admin(admin, roles.ADMIN, True)  # no change: fine


def test_temp_passwords_are_random_and_long_enough():
    passwords = {services.generate_temp_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) == 19 and p.count("-") == 3 for p in passwords)
