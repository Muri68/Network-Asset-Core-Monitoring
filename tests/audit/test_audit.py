"""Audit trail (PLAN.md §6.8, §9 "Audit")."""

import json
import time

import pytest
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.urls import reverse

from apps.accounts import roles, services
from apps.accounts.models import Profile, User
from apps.audit.log import MASK, log_event, track, untrack
from apps.audit.models import AppendOnlyError, AuditLog
from tests.conftest import token_for
from tests.factories import PASSWORD, make_user

UA = {"HTTP_USER_AGENT": "pytest-browser/1.0"}


def entries(action):
    return list(AuditLog.objects.filter(action=action))


def all_audit_text():
    return json.dumps(list(AuditLog.objects.values()), default=str)


# --------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAppendOnly:
    def test_model_refuses_change_and_delete(self):
        entry = log_event("login", message="x")
        entry.message = "tampered"
        with pytest.raises(AppendOnlyError):
            entry.save()
        with pytest.raises(AppendOnlyError):
            entry.delete()

    def test_queryset_refuses_bulk_change_and_delete(self):
        log_event("login")
        with pytest.raises(AppendOnlyError):
            AuditLog.objects.all().update(message="tampered")
        with pytest.raises(AppendOnlyError):
            AuditLog.objects.all().delete()

    @pytest.mark.parametrize("sql", [
        "UPDATE audit_auditlog SET message = 'tampered'",
        "DELETE FROM audit_auditlog",
    ])
    def test_database_trigger_refuses_raw_sql(self, sql):
        log_event("login", message="original")
        with pytest.raises((IntegrityError, DatabaseError), match="append-only"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql)
        assert AuditLog.objects.get().message == "original"

    def test_deleting_a_user_keeps_their_entries(self, viewer):
        log_event("login", viewer, actor=viewer)
        untrack(User)  # avoid the delete entry needing the row; we only test the FK behaviour
        try:
            viewer.delete()
        finally:
            track(User, exclude=["password", "last_login", "date_joined", "must_change_password",
                                 "is_staff", "is_superuser", "first_name", "last_name"])
        entry = AuditLog.objects.get(action="login")
        assert entry.actor_repr == viewer.name


# --------------------------------------------------------------------------
# Sign-in events
# --------------------------------------------------------------------------


def password_step(client, username, password=PASSWORD):
    return client.post(reverse("accounts:login"), {"username": username, "password": password}, **UA)


@pytest.mark.django_db
class TestSignInEvents:
    def test_successful_login(self, client, viewer):
        password_step(client, viewer.username)
        assert not entries("login")  # password alone isn't a sign-in
        client.post(reverse("accounts:login_2fa"), {"token": token_for(services.confirmed_totp(viewer))}, **UA)
        [entry] = entries("login")
        assert entry.actor == viewer and entry.ip == "127.0.0.1" and entry.user_agent == "pytest-browser/1.0"

    def test_failed_login_names_the_attempted_user(self, client, viewer):
        password_step(client, viewer.username, "wrong-password")
        [entry] = entries("login_failed")
        assert entry.actor is None and entry.actor_repr == f"Unknown ({viewer.username})"
        assert entry.object_id == str(viewer.pk)
        assert "wrong-password" not in all_audit_text()

    def test_failed_login_for_unknown_user(self, client):
        password_step(client, "ghost", "whatever-pw")
        [entry] = entries("login_failed")
        assert entry.actor_repr == "Unknown (ghost)" and entry.object_id == ""

    def test_lockout_is_logged(self, client, viewer):
        for _ in range(5):
            password_step(client, viewer.username, "wrong-password")
        assert len(entries("login_failed")) == 4
        [locked] = entries("login_locked")
        assert "sign-in locked" in locked.message
        password_step(client, viewer.username)  # right password, still locked: recorded again
        assert len(entries("login_locked")) == 2 and not entries("login")

    def test_wrong_2fa_code(self, client, viewer):
        password_step(client, viewer.username)
        client.post(reverse("accounts:login_2fa"), {"token": "000000"})
        [entry] = entries("2fa_failed")
        assert entry.actor == viewer and "at sign-in" in entry.message

    def test_2fa_setup_and_backup_code_use(self, client):
        user = make_user(roles.VIEWER, with_2fa=False)
        password_step(client, user.username)
        client.get(reverse("accounts:login_2fa_setup"))
        from django_otp.plugins.otp_totp.models import TOTPDevice

        client.post(reverse("accounts:login_2fa_setup"), {"token": token_for(TOTPDevice.objects.get(user=user))})
        assert entries("2fa_setup")[0].actor == user
        client.post(reverse("accounts:logout"))
        codes = services.new_backup_codes(user)
        password_step(client, user.username)
        client.post(reverse("accounts:login_2fa"), {"token": codes[0]})
        assert "9 backup codes left" in entries("backup_code_used")[0].message
        assert codes[0] not in all_audit_text()

    def test_logout_and_idle_timeout(self, as_user, viewer):
        client = as_user(viewer)
        client.post(reverse("accounts:logout"))
        assert entries("logout")[0].actor == viewer

        client = as_user(viewer)
        session = client.session
        session["last_activity"] = time.time() - 31 * 60
        session.save()
        client.get(reverse("ui:dashboard"))
        assert "inactivity" in entries("logout")[0].message


# --------------------------------------------------------------------------
# Account and admin events
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountEvents:
    def test_admin_creates_user(self, as_user, admin):
        as_user(admin).post(reverse("accounts:user_create"), {
            "full_name": "Grace Adeyemi", "username": "grace", "email": "grace@example.test", "role": "Engineer",
        }, **UA)
        grace = User.objects.get(username="grace")
        [created] = AuditLog.objects.filter(action="create", object_id=str(grace.pk))
        assert created.actor == admin and created.object_repr == "Grace Adeyemi"
        assert created.changes["username"] == [None, "grace"] and "password" not in created.changes
        [role] = AuditLog.objects.filter(action="user_role_change", object_id=str(grace.pk))
        assert role.changes == {"role": [None, "Engineer"]} and role.actor == admin
        assert created.user_agent == "pytest-browser/1.0"

    def test_admin_edits_user_with_before_and_after(self, as_user, admin, viewer):
        old_email = viewer.email
        as_user(admin).post(reverse("accounts:user_edit", args=[viewer.pk]), {
            "full_name": viewer.full_name, "username": viewer.username, "email": "new@example.test",
            "role": "Engineer",
        })
        [update] = entries("update")
        assert update.changes["email"] == [old_email, "new@example.test"]
        assert update.changes["is_active"] == [True, False]  # checkbox left unticked
        assert entries("user_role_change")[0].changes == {"role": ["Viewer", "Engineer"]}  # newest first

    def test_no_entry_when_nothing_changed(self, as_user, admin, viewer):
        client = as_user(admin)
        before = AuditLog.objects.count()
        client.post(reverse("accounts:user_edit", args=[viewer.pk]), {
            "full_name": viewer.full_name, "username": viewer.username, "email": viewer.email,
            "role": "Viewer", "is_active": "on",
        })
        assert AuditLog.objects.count() == before

    @pytest.mark.parametrize("action,expected", [
        ("reset-password", "password_reset"), ("reset-2fa", "2fa_reset"),
        ("unlock-login", "login_unlock"), ("unlock-vault", "vault_unlock"),
    ])
    def test_admin_actions(self, as_user, admin, viewer, action, expected):
        as_user(admin).post(reverse("accounts:user_action", args=[viewer.pk, action]))
        [entry] = entries(expected)
        assert entry.actor == admin and entry.object_id == str(viewer.pk)

    def test_deactivate_is_logged_as_change(self, as_user, admin, viewer):
        as_user(admin).post(reverse("accounts:user_action", args=[viewer.pk, "deactivate"]))
        assert entries("update")[0].changes == {"is_active": [True, False]}

    def test_temp_password_never_logged(self, as_user, admin, viewer):
        client = as_user(admin)
        client.post(reverse("accounts:user_action", args=[viewer.pk, "reset-password"]))
        temp = client.get(reverse("accounts:users")).context["temp_password"]["password"]
        assert temp not in all_audit_text()

    def test_own_password_and_reveal_code_changes(self, as_user, engineer):
        client = as_user(engineer)
        new_pw = "brand-new-passphrase-1"
        client.post(reverse("accounts:profile_password"), {
            "current_password": PASSWORD, "new_password1": new_pw, "new_password2": new_pw})
        client.post(reverse("accounts:profile_reveal_code"), {
            "current_password": new_pw, "code1": "secret-code-5", "code2": "secret-code-5"})
        client.post(reverse("accounts:profile_backup_codes"), {"password": new_pw})
        assert entries("password_change")[0].actor == engineer
        assert entries("reveal_code_change")[0].message == "Reveal code changed"
        assert entries("backup_codes_new")
        text = all_audit_text()
        assert new_pw not in text and "secret-code-5" not in text

    def test_bootstrap_admin_is_attributed_to_command_line(self):
        call_command("bootstrap_admin", "--username", "boss", "--password", PASSWORD)
        [created] = AuditLog.objects.filter(action="create", object_repr="boss")
        assert created.actor is None and created.actor_repr == "Command line (bootstrap_admin)"


# --------------------------------------------------------------------------
# Generic tracking (used by later phases)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_track_masks_secret_fields(engineer):
    track(Profile, mask=["reveal_code_hash"])
    try:
        services.set_reveal_code(engineer, "another-code")
    finally:
        untrack(Profile)
    [update] = entries("update")
    assert update.changes["reveal_code_hash"] == [MASK, MASK]
    assert "another-code" not in all_audit_text() and "md5$" not in all_audit_text()


# --------------------------------------------------------------------------
# Audit log page
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAuditPage:
    def test_lists_filters_and_paginates(self, as_user, admin, viewer):
        for i in range(60):
            log_event("login_failed", actor_repr="Unknown (bot)", message=f"try {i}")
        log_event("password_reset", viewer, actor=admin)
        client = as_user(admin)
        page = client.get(reverse("audit:log"))
        assert page.status_code == 200 and len(page.context["entries"]) == 50
        assert page.context["page"].paginator.num_pages == 2

        filtered = client.get(reverse("audit:log"), {"action": "password_reset"})
        assert [e.action for e in filtered.context["entries"]] == ["password_reset"]
        assert viewer.name in filtered.content.decode()

        searched = client.get(reverse("audit:log"), {"q": "try 59"})
        assert len(searched.context["entries"]) == 1

    def test_date_filter(self, as_user, admin):
        log_event("login")
        client = as_user(admin)
        response = client.get(reverse("audit:log"), {"from": "2000-01-01", "to": "2000-01-02"})
        assert response.context["entries"].count() == 0

    def test_export_csv_is_itself_logged(self, as_user, admin, viewer):
        log_event("password_reset", viewer, actor=admin)
        response = as_user(admin).get(reverse("audit:export"), {"action": "password_reset"})
        body = response.content.decode()
        assert response["Content-Type"].startswith("text/csv")
        assert "Password reset" in body and viewer.name in body
        assert "Audit log CSV, 1 rows" in entries("export")[0].message

    def test_dashboard_shows_recent_activity_to_admin_only(self, as_user, admin, viewer):
        log_event("password_reset", viewer, actor=admin, message="dashboard-check")
        assert "dashboard-check" in as_user(admin).get(reverse("ui:dashboard")).content.decode()
        assert "dashboard-check" not in as_user(viewer).get(reverse("ui:dashboard")).content.decode()

    def test_user_page_shows_their_activity(self, as_user, admin, viewer):
        log_event("password_reset", viewer, actor=admin, message="user-page-check")
        page = as_user(admin).get(reverse("accounts:user_edit", args=[viewer.pk])).content.decode()
        assert "user-page-check" in page


@pytest.mark.django_db
def test_audit_page_renders_change_details(as_user, admin, viewer):
    log_event("update", viewer, actor=admin, changes={"email": ["old@example.test", "new@example.test"]})
    html = as_user(admin).get(reverse("audit:log")).content.decode()
    assert "Before" in html and "old@example.test" in html and "new@example.test" in html
