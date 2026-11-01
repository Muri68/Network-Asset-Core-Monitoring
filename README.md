# NetCore — Network Asset & Core Monitoring System

A professional, independent network monitoring application that can be installed on any authorised network.
Self-hosted (LAN-only) uptime monitoring, IP address management, asset inventory,
rack/port documentation and an audited password vault. See [PLAN.md](PLAN.md) for the full spec.

## Current stage

**Live:** sign-in with mandatory two-factor (authenticator app + backup codes), roles
(Admin / Engineer / Viewer), user management, profile, vault reveal-code setup, and an
append-only **audit log** of every sign-in, security event and change.
**Also live:** locations, racks, devices, device models, ports, VLANs, connections, cable tracing,
ISPs, circuits, prefixes and IP addresses (IPAM).
**Still sample data:** monitoring, vault contents, settings.

Want sample devices to look around? `python manage.py load_demo_inventory` loads 30 demo devices
and 3 racks with their ports, VLANs and cables — only into an empty inventory, and they're real records
you'd delete afterwards.

## Run it locally

```bash
uv venv --python 3.13 .venv                 # or: python3.13 -m venv .venv
source .venv/bin/activate
uv pip install -r requirements-dev.txt      # or: pip install -r requirements-dev.txt
cp .env.example .env                        # then edit DJANGO_SECRET_KEY
python manage.py migrate
python manage.py bootstrap_admin --username yourname --full-name "Your Name" --email you@example.com
python manage.py tailwind runserver         # builds CSS on change + starts Django
```

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

In the sample-data vault, the demo reveal code is still `123456`.

## Layout

```
config/            Django settings & URLs
apps/accounts/     Users, roles, sign-in flow, 2FA, profile, user management
apps/audit/        Append-only audit log: log_event(), track(Model), viewer and CSV export
apps/core/         Shared base model (created/updated by) and form styling
apps/inventory/    Locations, racks, devices, device models, rack elevations
apps/dcim/         Ports, VLANs, connections, port patterns, cable tracing
apps/ipam/         ISPs, circuits, prefixes (subnet maths), IP addresses, NAT
apps/ui/           Sample-data views (replaced area by area), template tags
templates/         All pages, by area (auth, accounts, monitoring, inventory, dcim, ipam, vault, system)
tests/             pytest suite
assets/css/        Tailwind source (design tokens & components)
static/            Built CSS, app.js, vendored HTMX/Alpine/Chart.js, Inter font, Lucide icons
```
