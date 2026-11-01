"""Every audit action: key → (label shown in the UI, colour tone)."""

ACTIONS = {
    # Generic model changes
    "create": ("Created", "up"),
    "update": ("Updated", "info"),
    "delete": ("Deleted", "down"),
    # Sign-in
    "login": ("Signed in", "info"),
    "logout": ("Signed out", "muted"),
    "login_failed": ("Sign-in failed", "down"),
    "login_locked": ("Sign-in locked", "down"),
    "2fa_setup": ("2FA set up", "up"),
    "2fa_failed": ("Wrong 2FA code", "down"),
    "backup_code_used": ("Backup code used", "warn"),
    "backup_codes_new": ("New backup codes", "warn"),
    # Own account
    "password_change": ("Password changed", "warn"),
    "reveal_code_change": ("Reveal code changed", "warn"),
    # Admin actions on users
    "user_role_change": ("Role changed", "warn"),
    "password_reset": ("Password reset", "warn"),
    "2fa_reset": ("2FA reset", "warn"),
    "login_unlock": ("Sign-in unlocked", "info"),
    "vault_unlock": ("Vault unlocked", "info"),
    # Data
    "export": ("Export", "muted"),
    "import": ("Import", "muted"),
    # Later phases (vault, monitoring, settings)
    "reveal": ("Password revealed", "warn"),
    "copy": ("Password copied", "warn"),
    "reveal_failed": ("Reveal failed", "down"),
    "vault_locked": ("Vault locked", "down"),
    "settings_change": ("Settings changed", "info"),
    "maintenance_start": ("Maintenance started", "maint"),
    "incident_ack": ("Incident acknowledged", "info"),
}

CHOICES = [(key, label) for key, (label, _tone) in ACTIONS.items()]
