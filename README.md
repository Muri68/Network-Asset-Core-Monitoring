# NetCore — Network Asset & Core Monitoring System

A professional, independent network monitoring application that can be installed on any authorised network.
Self-hosted (LAN-only) uptime monitoring, IP address management, asset inventory,
rack/port documentation and an audited password vault. See [PLAN.md](PLAN.md) for the full spec.

## Current stage

**Live:** sign-in with mandatory two-factor (authenticator app + backup codes), roles
(Admin / Engineer / Viewer), user management, profile, vault reveal-code setup, and an
append-only **audit log** of every sign-in, security event and change.
**Also live:** locations, racks, devices, device models, ports, VLANs, connections, cable tracing,
ISPs, circuits, prefixes and IP addresses (IPAM), monitors, incidents and maintenance windows.
**Still sample data:** vault contents, Telegram alerts, settings.

Want sample devices to look around? `python manage.py load_demo_inventory` loads 30 demo devices
and 3 racks with their ports, VLANs and cables — only into an empty inventory, and they're real records
you'd delete afterwards.

## Run it locally

```bash
uv venv --python 3.13 .venv                 # or: python3.13 -m venv .venv
source .venv/bin/activate
uv pip install -r requirements-dev.txt      # or: pip install -r requirements-dev.txt
cp .env.example .env                        # then set DJANGO_SECRET_KEY and NETCORE_ENCRYPTION_KEYS
python manage.py migrate
python manage.py bootstrap_admin --username yourname --full-name "Your Name" --email you@example.com
python manage.py tailwind runserver         # builds CSS on change + starts Django
```

In a **second terminal**, start the checker that pings devices, checks websites and sends
Telegram alerts:

```bash
source .venv/bin/activate
python manage.py run_monitor                # keep it running; Ctrl+C to stop
```

The sidebar shows "Checker running" while it's up. Set your time zone in `.env`
(`DJANGO_TIME_ZONE=Africa/Lagos`) so times and maintenance windows match your clock.

### Telegram alerts

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username ending in `bot`.
2. Copy the token it gives you into **Settings → Telegram alerts** and save (it's checked with
   Telegram and stored encrypted with `NETCORE_ENCRYPTION_KEYS` — keep an offline copy of that key).
3. Each Admin and Engineer opens **My profile → Link Telegram → Open Telegram** and presses **Start**.
   `run_monitor` must be running: it receives the Start message and sends every alert.

Only outbound HTTPS to api.telegram.org is needed. Send `/status` to the bot for a quick summary.

### Password vault and the encryption key

Vault passwords, their notes and the bot token are encrypted with `NETCORE_ENCRYPTION_KEYS`.
**Keep an offline copy of that key** (printed or on a USB stick in a safe), separate from database
backups: without it the vault can't be decrypted. To change the key:

```bash
python manage.py vault_rotate_key --generate   # prints a new key
# put it FIRST in NETCORE_ENCRYPTION_KEYS, keep the old key after it, restart both processes
python manage.py vault_rotate_key              # re-encrypts everything with the new key
# then remove the old key and restart again;  --check shows which keys are in use
```

Five wrong reveal codes lock that person's vault and alert the Admins on Telegram; an Admin
unlocks it in **Users & roles**.

Open http://127.0.0.1:8000 and sign in. On first sign-in you scan a QR code with an
authenticator app (Google/Microsoft Authenticator, Authy…), save your backup codes, and
create your vault reveal code. Add other users from **Users & roles**.

Locked out after 5 wrong passwords? Wait 30 minutes, ask another Admin, or run
`python manage.py axes_reset_username <username>`.

## Tests

```bash
python -m pytest        # all tests
ruff check apps config tests
```

## Layout

```
config/            Django settings & URLs
apps/accounts/     Users, roles, sign-in flow, 2FA, profile, user management
apps/audit/        Append-only audit log: log_event(), track(Model), viewer and CSV export
apps/core/         Shared base model (created/updated by), form styling, encryption of stored secrets
apps/inventory/    Locations, racks, devices, device models, rack elevations
apps/dcim/         Ports, VLANs, connections, port patterns, cable tracing
apps/ipam/         ISPs, circuits, prefixes (subnet maths), IP addresses, NAT
apps/monitoring/   Checkers, state machine, run_monitor process, incidents, maintenance
apps/alerts/      Telegram client, notification outbox (down / still down / up / SSL), linking
apps/vault/       Encrypted credentials, folders, password history, reveal flow, access log, key rotation
apps/ui/           Dashboard, settings page, remaining sample-data views (import/export), template tags
templates/         All pages, by area (auth, accounts, monitoring, inventory, dcim, ipam, vault, system)
tests/             pytest suite
assets/css/        Tailwind source (design tokens & components)
static/            Built CSS, app.js, vendored HTMX/Alpine/Chart.js, Inter font, Lucide icons
```
