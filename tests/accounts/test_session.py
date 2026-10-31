import time

import pytest
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY

from apps.accounts import roles, services
from tests.factories import make_user

DASH = reverse("ui:dashboard")


def age_session(client, seconds):
    session = client.session
    session["last_activity"] = time.time() - seconds
    session.save()


@pytest.mark.django_db
class TestIdleTimeout:
    def test_active_session_is_kept(self, as_user, viewer):
        client = as_user(viewer)
        age_session(client, 29 * 60)
        assert client.get(DASH).status_code == 200

    def test_idle_session_is_signed_out(self, as_user, viewer):
        client = as_user(viewer)
        age_session(client, 31 * 60)
        response = client.get(DASH)
        assert response.status_code == 302 and response.url.startswith(reverse("accounts:login"))
        assert client.get(DASH).status_code == 302

    def test_htmx_request_gets_hx_redirect(self, as_user, viewer):
        client = as_user(viewer)
        age_session(client, 31 * 60)
        response = client.get(reverse("ui:dashboard_live"), headers={"HX-Request": "true"})
        assert response.status_code == 204 and "HX-Redirect" in response.headers

    def test_background_polling_does_not_count_as_activity(self, as_user, viewer):
        client = as_user(viewer)
        age_session(client, 20 * 60)
        before = client.session["last_activity"]
        assert client.get(reverse("ui:dashboard_live"), headers={"X-Background": "1"}).status_code == 200
        assert client.session["last_activity"] == before

    def test_real_request_refreshes_activity(self, as_user, viewer):
        client = as_user(viewer)
        age_session(client, 20 * 60)
        before = client.session["last_activity"]
        client.get(DASH)
        assert client.session["last_activity"] > before


@pytest.mark.django_db
def test_session_without_second_factor_is_rejected(client, viewer):
    """A session that never passed 2FA (e.g. forged) can't use the app."""
    client.force_login(viewer, backend="apps.accounts.backends.UsernameOrEmailBackend")
    response = client.get(DASH)
    assert response.status_code == 302 and response.url.startswith(reverse("accounts:login"))


@pytest.mark.django_db
def test_reset_2fa_signs_user_out(as_user, viewer):
    client = as_user(viewer)
    services.reset_two_factor(viewer)
    assert client.get(DASH).status_code == 302


@pytest.mark.django_db
def test_deactivated_user_is_signed_out(as_user, viewer):
    client = as_user(viewer)
    viewer.is_active = False
    viewer.save()
    assert client.get(DASH).status_code == 302


@pytest.mark.django_db
def test_vault_role_without_reveal_code_is_sent_to_setup(as_user):
    engineer = make_user(roles.ENGINEER, with_reveal_code=False)
    client = as_user(engineer)
    assert client.get(DASH).url == reverse("accounts:reveal_code_setup")
    # Signing out is still possible
    assert client.post(reverse("accounts:logout")).status_code == 302


@pytest.mark.django_db
def test_viewer_never_asked_for_reveal_code(as_user):
    viewer = make_user(roles.VIEWER, with_reveal_code=False)
    assert as_user(viewer).get(DASH).status_code == 200
    assert as_user(viewer).get(reverse("accounts:reveal_code_setup")).url == DASH


@pytest.mark.django_db
def test_device_session_key_is_set_after_real_login(client, viewer):
    from tests.conftest import token_for

    client.post(reverse("accounts:login"), {"username": viewer.username, "password": "correct-horse-battery-staple"})
    client.post(reverse("accounts:login_2fa"), {"token": token_for(services.confirmed_totp(viewer))})
    assert client.session[DEVICE_ID_SESSION_KEY]
