"""
Roles and what each one may do (PLAN.md §5).

Roles are Django Groups (created by migration 0002). Code never checks group
names directly: it asks `can(user, capability)`, so the matrix below is the
single place that decides access.
"""

ADMIN = "Admin"
ENGINEER = "Engineer"
VIEWER = "Viewer"
ROLES = [ADMIN, ENGINEER, VIEWER]

CAPABILITIES = {
    # View inventory, racks, ports, IPAM, monitors, dashboards
    "view": {ADMIN, ENGINEER, VIEWER},
    # Create / edit / delete inventory, ports, IPAM, monitors, maintenance
    "edit": {ADMIN, ENGINEER},
    # Permanently delete records (others retire instead)
    "delete_hard": {ADMIN},
    # See the vault, reveal and edit credentials (reveal still needs the reveal code)
    "vault": {ADMIN, ENGINEER},
    # Reveal logs of every user (others see only their own)
    "vault_logs_all": {ADMIN},
    "export": {ADMIN, ENGINEER},
    "import": {ADMIN},
    # Users, roles, unlocking logins and vaults
    "manage_users": {ADMIN},
    "audit": {ADMIN},
    "settings": {ADMIN},
}

# Rows shown in the "What each role can do" table on the Users page.
CAPABILITY_LABELS = [
    ("view", "View inventory, racks, ports, IPAM, monitors"),
    ("edit", "Create / edit inventory, ports, IPAM, monitors"),
    ("delete_hard", "Permanently delete devices (others can retire them)"),
    ("vault", "See vault entries and reveal passwords (with reveal code)"),
    ("vault_logs_all", "View reveal logs of all users"),
    ("import", "Import data"),
    ("export", "Export data"),
    ("manage_users", "Manage users, roles, unlock logins and vaults"),
    ("audit", "Audit log"),
    ("settings", "System settings"),
]


def can(user, capability):
    if not (user and user.is_authenticated and user.is_active):
        return False
    return user.role in CAPABILITIES[capability]


def capabilities_for(user):
    return {cap: can(user, cap) for cap in CAPABILITIES}
