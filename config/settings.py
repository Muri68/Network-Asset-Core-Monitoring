"""
Django settings for NetCore.

Authentication and authorization are real; the other areas still render fake
data from apps/ui (see PLAN.md for the phase plan). Values that differ per
machine come from environment variables, loaded from a local .env file.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default):
    return os.environ.get(name, "1" if default else "0").lower() in ("1", "true", "yes")


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "axes",
    "django_tailwind_cli",
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.inventory",
    "apps.dcim",
    "apps.ipam",
    "apps.monitoring",
    "apps.alerts",
    "apps.ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.context.AuditContextMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.SessionSecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must stay last: turns an axes lockout into the lockout page.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.access",
                "apps.ui.context_processors.shell",
            ],
            "builtins": ["apps.ui.templatetags.ui"],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite for local development (PLAN.md: PostgreSQL before the audit/IPAM steps).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("SQLITE_PATH", "db.sqlite3"),
        # The web server and the monitor process write at the same time: WAL lets readers and one
        # writer work together, and writers wait (up to 20 s) instead of failing with "database is locked".
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "apps.accounts.backends.UsernameOrEmailBackend",
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "ui:dashboard"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Sessions: closed with the browser, hard cap of 12 h, and signed out after
# NETCORE_IDLE_TIMEOUT of no real activity (enforced in SessionSecurityMiddleware).
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
NETCORE_IDLE_TIMEOUT = timedelta(minutes=int(os.environ.get("NETCORE_IDLE_TIMEOUT_MINUTES", "30")))
NETCORE_LOGIN_STEP_TIMEOUT = timedelta(minutes=10)
NETCORE_MAX_2FA_FAILURES = 5

# HTTPS-only cookies once deployed behind Nginx with TLS.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Two-factor (TOTP authenticator apps)
OTP_TOTP_ISSUER = "NetCore"

# Brute-force protection: 5 failures for the same username from the same IP.
AXES_FAILURE_LIMIT = 5
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_COOLOFF_TIME = timedelta(minutes=30)
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "auth/locked.html"
AXES_ENABLE_ADMIN = False

# Keys for secrets stored in the database (apps/core/crypto.py): comma-separated, newest first.
ENCRYPTION_KEYS = [k.strip() for k in os.environ.get("NETCORE_ENCRYPTION_KEYS", "").split(",") if k.strip()]

# --------------------------------------------------------------------------
# Locale, static files
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
# Times are stored in UTC and shown (and typed) in this zone (PLAN.md A14).
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

TAILWIND_CLI_SRC_CSS = "assets/css/source.css"
TAILWIND_CLI_DIST_CSS = "css/app.css"
TAILWIND_CLI_VERSION = "4.3.3"
