"""Pieces used when NetCore runs behind Nginx on the server (deploy/)."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.monitoring.models import MonitorHeartbeat

pytestmark = pytest.mark.django_db


def beat(seconds_ago):
    now = timezone.now()
    MonitorHeartbeat.objects.update_or_create(pk=1, defaults={"last_beat": now - timedelta(seconds=seconds_ago),
                                                              "started_at": now - timedelta(hours=1)})


def test_health_needs_no_sign_in(client):
    beat(5)
    response = client.get(reverse("ui:health"))
    assert response.status_code == 200 and response.json() == {"web": "ok", "monitor": "ok"}


def test_health_reports_a_stopped_checker(client):
    beat(120)
    response = client.get(reverse("ui:health"))
    assert response.status_code == 503 and response.json()["monitor"] == "down"


def test_real_ip_from_proxy(client, settings, admin):
    settings.BEHIND_PROXY = True
    client.post(reverse("accounts:login"), {"username": admin.username, "password": "wrong"},
                REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="10.255.15.40")
    assert AuditLog.objects.filter(ip="10.255.15.40").exists()


def test_header_ignored_without_proxy(client, settings, admin):
    settings.BEHIND_PROXY = False
    client.post(reverse("accounts:login"), {"username": admin.username, "password": "wrong"},
                REMOTE_ADDR="10.0.0.9", HTTP_X_REAL_IP="1.2.3.4")
    assert not AuditLog.objects.filter(ip="1.2.3.4").exists()
    assert AuditLog.objects.filter(ip="10.0.0.9").exists()


def test_deploy_check_is_clean(settings, monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 60)
    settings.DEBUG = False
    settings.SECRET_KEY = "k" * 20 + "Zq9#mT2$vL7!pR4&wX8*nB3^cF6@hJ1%" + "y" * 20
    settings.BEHIND_PROXY = True
    settings.SECURE_SSL_REDIRECT = True
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = True
    settings.ALLOWED_HOSTS = ["10.255.15.66"]
    call_command("check", "--deploy", "--fail-level", "WARNING")
