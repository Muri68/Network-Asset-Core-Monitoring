# NetCore — Network Asset & Core Monitoring System

A professional, independent network monitoring application that can be installed on any authorised network.
Self-hosted (LAN-only) uptime monitoring, IP address management, asset inventory,
rack/port documentation and an audited password vault. See [PLAN.md](PLAN.md) for the full spec.

## Current stage: UI prototype

Every page is built with **fake data** (`apps/ui/fake_data.py`). There is no database, login
or background worker yet, and nothing you type is saved.

## Run it locally

```bash
uv venv --python 3.13 .venv          # or: python3.13 -m venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt   # or: pip install -r requirements.txt
python manage.py tailwind runserver  # builds CSS on change + starts Django
```

Open http://127.0.0.1:8000. The first run downloads the Tailwind CLI (v4.3.3) once.

Try:
- **⌘K / Ctrl+K** — search devices, IPs, monitors, credentials
- **Password vault → any credential → Reveal** — demo reveal code `123456` (5 wrong codes lock it)
- **Devices → SW-CORE-01** — front-panel port map, ports & connections, cable trace
- **Prefixes → 129.205.100.128/28** — subnet breakdown and address map
- **Subnet calculator** — type any CIDR
- Sun/moon icon — light/dark theme

## Layout

```
config/            Django settings & URLs
apps/ui/           Prototype views, fake data, template tags (icons, gauge, rings)
templates/         All pages, by area (monitoring, inventory, dcim, ipam, vault, accounts, system)
assets/css/        Tailwind source (design tokens & components)
static/            Built CSS, app.js, vendored HTMX/Alpine/Chart.js, Inter font, Lucide icons
```
