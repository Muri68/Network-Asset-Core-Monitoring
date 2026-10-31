import time

import pytest
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp

from apps.accounts import roles, services

from .factories import make_user


@pytest.fixture(autouse=True)
def fast_hashing(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def token_for(device, offset=0):
    """Current TOTP code for a device (offset in 30 s steps)."""
    code = totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits, drift=device.drift + offset)
    return f"{code:0{device.digits}d}"


def sign_in(client, user):
    """Log a user in as if they finished the full password + 2FA flow."""
    client.force_login(user, backend="apps.accounts.backends.UsernameOrEmailBackend")
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = services.confirmed_totp(user).persistent_id
    session["last_activity"] = time.time()
    session.save()
    return client


@pytest.fixture
def admin(db):
    return make_user(roles.ADMIN, username="admin1", full_name="Ada Admin")


@pytest.fixture
def engineer(db):
    return make_user(roles.ENGINEER, username="eng1", full_name="Eve Engineer")


@pytest.fixture
def viewer(db):
    return make_user(roles.VIEWER, username="view1", full_name="Vic Viewer")


@pytest.fixture
def as_user(client):
    def _as(user):
        return sign_in(client, user)

    return _as
