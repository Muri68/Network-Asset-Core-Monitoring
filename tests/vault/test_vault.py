"""Password vault (PLAN.md §7.3, §9 "Vault")."""

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.urls import reverse

from apps.accounts import roles
from apps.accounts import services as account_services
from apps.alerts.models import Notification
from apps.audit.models import AuditLog
from apps.core import crypto
from apps.vault import services
from apps.vault.models import Credential, CredentialFolder, CredentialHistory, RevealLog
from tests.alerts.conftest import link
from tests.factories import make_user
from tests.inventory.helpers import make_device

pytestmark = pytest.mark.django_db

SECRET = "Sup3r-S3cret!pw"
NOTES = "enable secret is the same"
CODE = "reveal-123"  # set by make_user for vault roles


def make_credential(name="Core switch — admin", secret=SECRET, notes=NOTES, **kw):
    folder = kw.pop("folder", None) or CredentialFolder.objects.get_or_create(name="Network core")[0]
    c = Credential(folder=folder, name=name, username="admin", host="10.10.0.2", **kw)
    c.set_secret(secret)
    c.set_notes(notes)
    c.save()
    return c


def reveal(client, c, code=CODE):
    return client.post(reverse("vault:credential_reveal", args=[c.pk]), {"code": code})


class TestStorage:
    def test_database_holds_only_ciphertext(self):
        c = make_credential()
        with connection.cursor() as cur:
            cur.execute("SELECT secret_encrypted, notes_encrypted FROM vault_credential WHERE id = %s", [c.pk])
            row = " ".join(cur.fetchone())
        assert SECRET not in row and NOTES not in row
        assert c.secret == SECRET and c.notes == NOTES and c.key_id == crypto.current_key_id()

    def test_new_password_moves_the_old_one_to_history(self, engineer):
        c = make_credential()
        c.set_secret("new-one", by=engineer)
        c.save()
        (h,) = c.history.all()
        assert h.secret == SECRET and h.replaced_by == engineer and c.secret == "new-one"

    def test_audit_log_never_contains_secrets(self, as_user, engineer):
        folder = CredentialFolder.objects.create(name="Servers")
        as_user(engineer).post(reverse("vault:credential_new"), {
            "name": "NAS", "folder": folder.pk, "protocol": "https", "username": "root", "password": SECRET,
            "notes": NOTES})
        entries = " ".join(str(e.changes) + e.message for e in AuditLog.objects.all())
        assert SECRET not in entries and NOTES not in entries
        assert "•••" in str(AuditLog.objects.get(action="create", object_repr="NAS").changes)

    def test_reveal_log_is_append_only(self, engineer):
        entry = RevealLog.objects.create(user=engineer, credential=make_credential(), action="reveal")
        for statement in (lambda: RevealLog.objects.filter(pk=entry.pk).update(action="copy"),
                          lambda: RevealLog.objects.filter(pk=entry.pk).delete()):
            with pytest.raises(DatabaseError), transaction.atomic():
                statement()


class TestRevealRules:
    def test_right_code_reveals_and_logs(self, engineer):
        c = make_credential()
        outcome = services.reveal(engineer, c, CODE)
        assert outcome.status == "ok" and outcome.secret == SECRET and outcome.notes == NOTES
        assert RevealLog.objects.get().action == "reveal"
        assert AuditLog.objects.filter(action="reveal", object_repr=c.name, actor=engineer).exists()

    def test_wrong_codes_count_down_then_lock(self, admin, engineer, bot):
        link(admin, 1001)
        c = make_credential()
        for left in (4, 3, 2, 1):
            assert services.reveal(engineer, c, "nope").remaining == left
        assert services.reveal(engineer, c, "nope").status == "locked"
        engineer.profile.refresh_from_db()
        assert engineer.profile.vault_locked
        assert list(RevealLog.objects.order_by("at").values_list("action", flat=True)) == ["reveal_failed"] * 5 + [
            "locked"]
        assert AuditLog.objects.filter(action="vault_locked").exists()
        alert = Notification.objects.get(user=admin)
        assert "Vault locked: Eve Engineer" in alert.text and c.name in alert.text

    def test_locked_vault_refuses_even_the_right_code_until_unlocked(self, engineer):
        c = make_credential()
        for _ in range(5):
            services.reveal(engineer, c, "nope")
        assert services.reveal(engineer, c, CODE).status == "locked"
        account_services.unlock_vault(engineer)
        assert services.reveal(engineer, c, CODE).status == "ok"

    def test_success_resets_the_count(self, engineer):
        c = make_credential()
        for _ in range(4):
            services.reveal(engineer, c, "nope")
        services.reveal(engineer, c, CODE)
        assert services.reveal(engineer, c, "nope").remaining == 4

    def test_no_reveal_code_yet(self):
        user = make_user(roles.ENGINEER, username="newbie", with_reveal_code=False)
        assert services.reveal(user, make_credential(), "").status == "no_code"

    def test_viewers_never(self, viewer):
        assert services.reveal(viewer, make_credential(), CODE).status == "forbidden"

    def test_history_only_for_admins(self, admin, engineer):
        c = make_credential()
        c.set_secret("new-one")
        c.save()
        h = c.history.get()
        assert services.reveal(engineer, c, CODE, history=h).status == "forbidden"
        outcome = services.reveal(admin, c, CODE, history=h)
        assert outcome.secret == SECRET and RevealLog.objects.get().action == "history_reveal"


class TestPages:
    def test_password_and_notes_never_in_pages(self, as_user, admin):
        c = make_credential(device=make_device())
        client = as_user(admin)
        for url in (reverse("vault:vault"), c.get_absolute_url(), reverse("vault:credential_edit", args=[c.pk]),
                    reverse("vault:credential_password", args=[c.pk]), c.device.get_absolute_url() + "?tab=creds",
                    reverse("ui:dashboard")):
            html = client.get(url).content.decode()
            assert SECRET not in html and NOTES not in html, url
        assert c.name in client.get(c.device.get_absolute_url()).content.decode()  # Credentials tab lists it

    def test_reveal_endpoint(self, as_user, engineer):
        c = make_credential()
        response = reveal(as_user(engineer), c)
        html = response.content.decode()
        assert SECRET in html and NOTES in html and "no-store" in response["Cache-Control"]
        assert "{#" not in html and "<template" not in html  # hiding must be able to remove the secret entirely

    def test_wrong_code_page_says_how_many_are_left(self, as_user, engineer):
        html = reveal(as_user(engineer), make_credential(), "nope").content.decode()
        assert SECRET not in html and "4 attempts left" in html

    def test_locked_page(self, as_user, engineer):
        c = make_credential()
        client = as_user(engineer)
        for _ in range(5):
            response = reveal(client, c, "nope")
        assert "Your vault is locked" in response.content.decode()

    def test_copy_logged_only_after_a_reveal(self, as_user, engineer):
        c = make_credential()
        client = as_user(engineer)
        assert client.post(reverse("vault:credential_copied", args=[c.pk])).status_code == 403
        reveal(client, c)
        assert client.post(reverse("vault:credential_copied", args=[c.pk])).status_code == 204
        assert RevealLog.objects.filter(action="copy").count() == 1
        assert AuditLog.objects.filter(action="copy").exists()

    def test_add_edit_and_change_password(self, as_user, engineer):
        folder = CredentialFolder.objects.create(name="Servers")
        client = as_user(engineer)
        response = client.post(reverse("vault:credential_new"), {
            "name": "NAS", "folder": folder.pk, "protocol": "https", "username": "root", "password": SECRET,
            "notes": NOTES})
        c = Credential.objects.get()
        assert response.url == c.get_absolute_url() and c.secret == SECRET and c.created_by == engineer
        client.post(reverse("vault:credential_edit", args=[c.pk]), {
            "name": "NAS 01", "folder": folder.pk, "protocol": "https", "username": "root", "notes": ""})
        c.refresh_from_db()
        assert c.name == "NAS 01" and c.secret == SECRET and c.notes == NOTES  # unchanged
        client.post(reverse("vault:credential_password", args=[c.pk]), {"password": "brand-new"})
        c.refresh_from_db()
        assert c.secret == "brand-new" and c.history.get().secret == SECRET
        assert AuditLog.objects.filter(action="password_rotate").exists()

    def test_password_required_when_adding(self, as_user, engineer):
        folder = CredentialFolder.objects.create(name="Servers")
        response = as_user(engineer).post(reverse("vault:credential_new"), {
            "name": "NAS", "folder": folder.pk, "protocol": "https"})
        assert response.status_code == 400 and not Credential.objects.exists()

    def test_soft_delete_by_admins_keeps_the_log(self, as_user, admin, engineer):
        c = make_credential()
        services.reveal(engineer, c, CODE)
        assert as_user(engineer).post(reverse("vault:credential_delete", args=[c.pk])).status_code == 403
        as_user(admin).post(reverse("vault:credential_delete", args=[c.pk]))
        assert not Credential.objects.exists() and Credential.all_objects.get().deleted_at
        assert RevealLog.objects.count() == 1
        assert as_user(admin).get(c.get_absolute_url()).status_code == 404
        assert "(deleted)" in as_user(admin).get(reverse("vault:reveal_log")).content.decode()

    def test_folder_with_credentials_cant_be_deleted(self, as_user, engineer):
        c = make_credential()
        as_user(engineer).post(reverse("vault:folder_delete", args=[c.folder.pk]))
        assert CredentialFolder.objects.exists()
        empty = CredentialFolder.objects.create(name="Spare")
        as_user(engineer).post(reverse("vault:folder_delete", args=[empty.pk]))
        assert not CredentialFolder.objects.filter(pk=empty.pk).exists()

    def test_history_reveal_page_is_admin_only(self, as_user, admin, engineer):
        c = make_credential()
        c.set_secret("new-one")
        c.save()
        url = reverse("vault:history_reveal", args=[c.history.get().pk])
        assert as_user(engineer).post(url, {"code": CODE}).status_code == 403
        assert SECRET in as_user(admin).post(url, {"code": CODE}).content.decode()

    def test_search_lists_credentials_for_vault_roles(self, as_user, engineer):
        make_credential("Mikrotik edge")
        assert '"label": "Mikrotik edge"' in as_user(engineer).get(reverse("ui:dashboard")).content.decode()


class TestAccessLog:
    def test_engineers_see_their_own_admins_see_all(self, as_user, admin, engineer):
        c = make_credential()
        other = make_user(roles.ENGINEER, username="other", full_name="Otto Other")
        services.reveal(engineer, c, CODE)
        services.reveal(other, c, CODE)
        mine = as_user(engineer).get(reverse("vault:reveal_log")).content.decode()
        assert "Eve Engineer" in mine and "Otto Other" not in mine
        everyone = as_user(admin).get(reverse("vault:reveal_log")).content.decode()
        assert "Eve Engineer" in everyone and "Otto Other" in everyone

    def test_filters(self, as_user, admin, engineer):
        c = make_credential()
        services.reveal(engineer, c, CODE)
        services.reveal(engineer, c, "nope")
        html = as_user(admin).get(reverse("vault:reveal_log") + "?action=reveal_failed").content.decode()
        assert "entered a wrong code" in html and "revealed" not in html.split("Filter")[1].split("</ul>")[0]

    def test_csv_export_admins_only(self, as_user, admin, engineer):
        services.reveal(engineer, make_credential(), CODE)
        response = as_user(admin).get(reverse("vault:reveal_log") + "?format=csv")
        assert response["Content-Type"] == "text/csv" and "eng1,Revealed,Core switch" in response.content.decode()
        assert AuditLog.objects.filter(action="export", message__startswith="Vault access log").exists()
        assert as_user(engineer).get(reverse("vault:reveal_log") + "?format=csv")["Content-Type"] != "text/csv"


class TestKeyRotation:
    def test_rotation_end_to_end(self, bot):
        old, new = crypto.new_key(), crypto.new_key()
        with override_settings(ENCRYPTION_KEYS=[old]):
            c = make_credential()
            c.set_secret("second")
            c.save()
            bot.bot_token = "123456789:" + "Z" * 35
            bot.save()
        with override_settings(ENCRYPTION_KEYS=[new, old]):
            call_command("vault_rotate_key", stdout=open("/dev/null", "w"))
        with override_settings(ENCRYPTION_KEYS=[new]):  # old key removed
            c = Credential.objects.get()
            from apps.alerts.models import AlertSettings

            assert c.secret == "second" and c.notes == NOTES and c.key_id == crypto.key_id(new)
            assert CredentialHistory.objects.get().secret == SECRET
            assert CredentialHistory.objects.get().key_id == crypto.key_id(new)
            assert AlertSettings.load().bot_token == "123456789:" + "Z" * 35
        assert AuditLog.objects.filter(action="key_rotate", actor_repr__startswith="Command line").exists()

    def test_missing_old_key_changes_nothing(self):
        old, new = crypto.new_key(), crypto.new_key()
        with override_settings(ENCRYPTION_KEYS=[old]):
            make_credential()
        before = Credential.objects.get().secret_encrypted
        with override_settings(ENCRYPTION_KEYS=[new]), pytest.raises(CommandError, match="can't be decrypted"):
            call_command("vault_rotate_key", stdout=open("/dev/null", "w"))
        assert Credential.objects.get().secret_encrypted == before

    def test_generate_and_check(self, capsys):
        call_command("vault_rotate_key", "--generate")
        assert "NETCORE_ENCRYPTION_KEYS" in capsys.readouterr().out
        make_credential()
        call_command("vault_rotate_key", "--check")
        assert "1 password(s)  ← current" in capsys.readouterr().out
