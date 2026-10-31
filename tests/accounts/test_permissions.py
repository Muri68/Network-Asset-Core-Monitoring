"""Every page × every role (PLAN.md §9 "Permissions")."""

import pytest
from django.urls import reverse

from apps.accounts import roles
from tests.factories import make_user

# (url name, args, capability needed). Every GET page in the app must be listed here:
# test_every_url_is_covered fails if a new page is added without an entry.
PAGES = [
    ("ui:dashboard", [], "view"),
    ("ui:dashboard_live", [], "view"),
    ("ui:monitors", [], "view"),
    ("ui:monitor_detail", [12], "view"),
    ("ui:monitor_new", [], "edit"),
    ("ui:incidents", [], "view"),
    ("ui:maintenance", [], "view"),
    ("ui:devices", [], "view"),
    ("ui:device_detail", [2], "view"),
    ("ui:device_new", [], "edit"),
    ("ui:racks", [], "view"),
    ("ui:rack_detail", [1], "view"),
    ("ui:connections", [], "view"),
    ("ui:connection_trace", [5], "view"),
    ("ui:vlans", [], "view"),
    ("ui:prefixes", [], "view"),
    ("ui:prefix_detail", [1], "view"),
    ("ui:isps", [], "view"),
    ("ui:calculator", [], "view"),
    ("ui:vault", [], "vault"),
    ("ui:credential_detail", [1], "vault"),
    ("ui:reveal_log", [], "vault"),
    ("audit:log", [], "audit"),
    ("audit:export", [], "audit"),
    ("ui:data", [], "export"),
    ("ui:settings", [], "settings"),
    ("accounts:users", [], "manage_users"),
    ("accounts:profile", [], "view"),
]

POST_ONLY = {"ui:credential_reveal", "ui:import_preview", "accounts:user_create", "accounts:user_action",
             "accounts:profile_password", "accounts:profile_reveal_code", "accounts:profile_backup_codes",
             "accounts:logout"}
PUBLIC = {"accounts:login", "accounts:login_password", "accounts:login_2fa_setup", "accounts:login_2fa"}
SPECIAL = {"accounts:backup_codes", "accounts:reveal_code_setup", "accounts:user_edit"}


def test_every_url_is_covered():
    from django.urls import get_resolver

    names = set()
    for ns in ("ui", "accounts", "audit"):
        resolver = get_resolver().namespace_dict[ns][1]
        names |= {f"{ns}:{n}" for n in resolver.reverse_dict if isinstance(n, str)}
    covered = {name for name, _, _ in PAGES} | POST_ONLY | PUBLIC | SPECIAL
    assert names - covered == set(), "Add new pages to PAGES in test_permissions.py"


@pytest.mark.django_db
@pytest.mark.parametrize("name,args,cap", PAGES)
def test_anonymous_is_sent_to_login(client, name, args, cap):
    response = client.get(reverse(name, args=args))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.django_db
@pytest.mark.parametrize("role", roles.ROLES)
@pytest.mark.parametrize("name,args,cap", PAGES)
def test_role_access(as_user, role, name, args, cap):
    client = as_user(make_user(role))
    response = client.get(reverse(name, args=args))
    if role in roles.CAPABILITIES[cap]:
        assert response.status_code == 200, f"{role} should reach {name}"
    else:
        assert response.status_code == 403, f"{role} must not reach {name}"


@pytest.mark.django_db
@pytest.mark.parametrize("role,allowed", [(roles.ADMIN, True), (roles.ENGINEER, True), (roles.VIEWER, False)])
def test_vault_reveal_post(as_user, role, allowed):
    client = as_user(make_user(role))
    response = client.post(reverse("ui:credential_reveal", args=[1]), {"code": "000000"})
    assert response.status_code == (200 if allowed else 403)


@pytest.mark.django_db
@pytest.mark.parametrize("role,allowed", [(roles.ADMIN, True), (roles.ENGINEER, False), (roles.VIEWER, False)])
def test_import_is_admin_only(as_user, role, allowed):
    client = as_user(make_user(role))
    response = client.post(reverse("ui:import_preview"), {"resource": "devices"})
    assert response.status_code == (200 if allowed else 403)


@pytest.mark.django_db
def test_viewer_sees_no_admin_or_vault_links(as_user, viewer):
    html = as_user(viewer).get(reverse("ui:dashboard")).content.decode()
    for name in ("ui:vault", "audit:log", "ui:settings", "accounts:users", "ui:data", "ui:monitor_new"):
        assert f'href="{reverse(name)}"' not in html, name
    assert '"kind": "Credential"' not in html  # search palette doesn't list credentials


@pytest.mark.django_db
def test_engineer_nav(as_user, engineer):
    html = as_user(engineer).get(reverse("ui:dashboard")).content.decode()
    assert f'href="{reverse("ui:vault")}"' in html
    assert f'href="{reverse("ui:monitor_new")}"' in html
    for name in ("audit:log", "ui:settings", "accounts:users"):
        assert f'href="{reverse(name)}"' not in html, name


@pytest.mark.django_db
def test_device_page_hides_edit_for_viewer(as_user, viewer):
    html = as_user(viewer).get(reverse("ui:device_detail", args=[2])).content.decode()
    assert "Add connection" not in html
    assert "Reveal in vault" not in html
