# Network Asset & Core Monitoring System — Implementation Plan (v1)

> Status: **Approved — UI prototype built** · Last updated: 2026-10-31
> Builders: project owner + Claude Code (pair-building, phase by phase)

---

## 1. Goal

A self-hosted, single-organisation web application that combines:

1. **Uptime monitoring** of websites, servers and network devices (Uptime-Kuma style)
2. **IPAM** — ISP public IP blocks and private subnets with full subnet breakdown and per-IP assignment
3. **Asset inventory** — every server, switch, firewall, access-control unit, NVR, camera, patch panel, etc.
4. **Rack & port documentation** — rack/U placement, every port, what it connects to, its function and VLANs
5. **Encrypted password vault** — reveal requires a personal reveal code; every access is logged
6. **Telegram alerting** — down / recovered / still-down, with probable cause
7. **Full audit trail** of everything that happens in the app

---

## 2. Confirmed decisions

| Area | Decision |
|---|---|
| Tenancy | Single organisation |
| Hosting | Ubuntu server inside the LAN, reachable **from the LAN only** (no internet exposure) |
| Code hosting | Private **GitHub** repository |
| Backups | Local on the server for now; **NAS** added as the off-server target when available |
| Scale | ≤ 1,000 devices/monitors, a handful of users |
| Monitoring v1 | Ping, TCP, HTTP(S) + keyword, DNS, SSL expiry, **dependency awareness**. SNMP → v2 |
| Down rule | Down after **3 consecutive failed checks** |
| Alerts | **Telegram** only in v1; muted during maintenance windows |
| Frontend | Django templates + **HTMX** + **Tailwind** |
| Vault access | **Per role**; reveal requires a user-generated **reveal code** (set on Profile page) |
| IPAM | Every usable IP is assignable to a device/service |
| Audit | Log every action (changes, auth events, reveals, imports, exports, settings) |
| Import/Export | CSV + Excel import (with preview) and export |
| User management | Only Admins create users and assign roles |

---

## 3. Tech stack

Versions checked against PyPI on 2026-10-31. Pin exact versions in `requirements.txt` at Phase 0.

| Layer | Choice | Notes |
|---|---|---|
| Language | Python **3.13** (Docker image) | Django 6.1 supports 3.12–3.14; 3.13 has the broadest library support |
| Framework | **Django 6.1.x** | Built-in CSP support (6.0+). Upgrade to 6.2 LTS when released (≈ Apr 2027) |
| Database | **PostgreSQL 17** | Native `inet`/`cidr` types for IPAM |
| Queue / cache | **Redis 7** | Celery broker + cache + rate-limit counters |
| Background jobs | **Celery 5.6** + celery-beat | Check scheduling, alert outbox, rollups, retention |
| UI | HTMX 2, Tailwind CSS (standalone CLI via `django-tailwind-cli`, no Node needed), Alpine.js (small UI bits), Chart.js (response-time graphs) | Dark mode supported |
| 2FA | `django-otp` (TOTP) | Mandatory for all users |
| Brute-force protection | `django-axes` | Login lockout |
| Crypto | `cryptography` (Fernet / MultiFernet) | Vault encryption with key rotation |
| Password hashing | Django `Argon2PasswordHasher` (`argon2-cffi`) | Login passwords **and** reveal codes |
| IP fields | `django-netfields` (Postgres `inet`/`cidr`) + stdlib `ipaddress` | Verify maintenance status at Phase 4; fallback = stdlib + validated CharFields |
| Checkers | `icmplib` (async, unprivileged ping), `httpx` (async HTTP), `dnspython` (async DNS), stdlib `asyncio`/`ssl` (TCP/SSL) | |
| Import/export | `django-import-export` + `openpyxl` | Dry-run preview before commit |
| Telegram | Bot API via `httpx` (no heavy SDK) | Long-polling for bot commands → no inbound webhook needed |
| Server | Gunicorn behind **Nginx**, internal TLS certificate | LAN-only, so no Let's Encrypt; use an internal CA (e.g. `mkcert`/step-ca) or a self-signed cert trusted on staff machines |
| Packaging | **Docker Compose** | |
| Testing | `pytest`, `pytest-django`, `factory_boy`, `coverage` | |
| Quality | `ruff` (lint + format), `pre-commit` | |

---

## 4. Project structure

```
Network_monitoring_tool/
├── PLAN.md
├── README.md
├── docker/
│   ├── compose.dev.yml
│   ├── compose.prod.yml
│   ├── nginx/
│   └── Dockerfile
├── .env.example
├── requirements/{base,dev,prod}.txt
├── manage.py
├── config/                 # settings (base/dev/prod), urls, celery.py, wsgi/asgi
├── apps/
│   ├── core/               # base models (timestamps, soft delete), mixins, permissions, layout
│   ├── accounts/           # User, Profile, roles, 2FA, reveal code, Telegram linking
│   ├── audit/              # AuditLog, request-context middleware, signal hooks
│   ├── inventory/          # Location, Rack, Manufacturer, DeviceModel, Device, attributes
│   ├── dcim/               # Port, Connection, ExternalEndpoint, VLAN, path tracing
│   ├── ipam/               # ISP, Circuit, Prefix, IPAddress
│   ├── monitoring/         # Monitor, checkers, state machine, Incident, Maintenance, rollups
│   ├── alerts/             # Telegram client, notification outbox, reminders
│   ├── vault/              # CredentialFolder, Credential, history, RevealLog, crypto
│   └── io/                 # import/export resources and views
├── templates/
├── static/
└── tests/                  # mirrors apps/
```

---

## 5. Roles & permissions

Roles are implemented as Django **Groups** created by a data migration. Every view uses a `RoleRequiredMixin`; a parametrised test walks every URL and asserts access per role.

| Capability | Admin | Engineer | Viewer |
|---|:-:|:-:|:-:|
| View inventory, racks, ports, IPAM, monitors, dashboards | ✅ | ✅ | ✅ |
| Create/edit/delete inventory, ports, IPAM, monitors, maintenance | ✅ | ✅ | ❌ |
| See vault (list) | ✅ | ✅ | ❌ |
| Reveal/copy credential (with reveal code) | ✅ | ✅ | ❌ |
| Create/edit credentials | ✅ | ✅ | ❌ |
| View reveal logs for all users | ✅ | own only | ❌ |
| Import / export | ✅ | ✅ (export) | ❌ |
| Manage users, roles, unlock vault lockouts | ✅ | ❌ | ❌ |
| View audit log, system settings, Telegram bot config | ✅ | ❌ | ❌ |

---

## 6. Data model

All models inherit `created_at`, `updated_at`, `created_by`, `updated_by`. "Soft delete" = `deleted_at` timestamp, hidden by default manager.

### 6.1 accounts
- **User** (custom, `AbstractUser`) — email unique, `must_change_password` flag.
- **Profile** — `user` 1:1, `reveal_code_hash`, `reveal_failed_attempts`, `vault_locked_at`, `telegram_chat_id` (unique, nullable), `telegram_link_token` + expiry, `alerts_muted` (bool), `timezone`.

### 6.2 inventory
- **Location** — name (unique), address, notes.
- **Rack** — location FK, name, `height_u` (default 42), notes. Unique (location, name).
- **Manufacturer** — name (unique).
- **DeviceModel** — manufacturer FK, name, `device_type` (choice: server, switch, router, firewall, access_point, access_control, nvr, camera, patch_panel, ups, pdu, storage, other), `height_u`, **port template** (list of port name patterns, e.g. `GigabitEthernet1/0/[1-48]`).
- **Device** — name (unique), device_model FK, `device_type` (copied from model, overridable), serial, asset_tag, firmware, `management_ip` (FK → IPAddress, nullable), mac, location FK, rack FK (nullable), `position_u` (nullable), `face` (front/rear), **`parent` FK → Device** (dependency, nullable), status (active / spare / faulty / retired), purchase_date, warranty_expiry, owner/responsible, notes.
  - Validation: device fits inside rack height; no U overlap with another device on the same rack & face.
  - Creating a device from a model auto-creates its ports from the port template.
  - Delete: default action is **Retire**. Hard delete (Admin only) cascades ports/connections, unassigns IPs, unlinks credentials (credentials kept).
- **DeviceAttribute** — device FK, key, value (free-form extra fields, e.g. `nvr_channel = 7`). Unique (device, key).

### 6.3 dcim (ports & connections)
- **VLAN** — `vid` (1–4094), name, location FK (nullable = global), description. Unique (location, vid).
- **Port** — device FK, name, `label`, type (copper/fiber/sfp/console/power/other), speed, `mode` (access / trunk / routed / none), `untagged_vlan` FK, `tagged_vlans` M2M, `function` (text, e.g. "Internet uplink"), remark, enabled, **`paired_port` FK → Port** (patch-panel front↔rear pass-through). Unique (device, name).
- **ExternalEndpoint** — name (e.g. "ISP fibre handoff", "Wall jack B-12"), location, description.
- **Connection** — `a_port` FK Port, `b_port` FK Port (nullable), `b_external` FK ExternalEndpoint (nullable), cable label, cable type, colour, length, remark.
  - DB check constraint: exactly one of `b_port` / `b_external`.
  - A port may appear in **at most one** connection (either side) — enforced in model `clean()` + a DB trigger/exclusion constraint.
  - One record, shown on **both** devices.
- **Path tracing** — follow Connection → `paired_port` → Connection … to show full path, e.g. `Firewall:port1 → PP-3:12(front) → PP-3:12(rear) → Switch-01:Gi1/0/10`.

### 6.4 ipam
- **ISP** — name, account number, support phone/email, account manager, portal URL, notes.
- **Circuit** — ISP FK, circuit_id (unique), bandwidth, type (fibre/microwave/…), install date, contract start/end, monthly cost + currency, termination device/port FK, notes.
- **Prefix** — `cidr` (unique), kind (public/private), ISP FK (nullable), circuit FK (nullable), VLAN FK (nullable), location FK, `gateway` (defaults to first usable, editable), description, status.
  - **Calculated (not stored):** network address, netmask, wildcard, broadcast, first/last usable, usable count, utilisation %.
  - Edge cases: `/31` (RFC 3021, both usable, no broadcast) and `/32` (single host).
- **IPAddress** — `address` (unique), prefix FK (auto-resolved by containment), status (used / reserved / free / dhcp), assigned device FK, assigned port FK, `service` (text, e.g. "Customer portal"), `dns_name`, **NAT**: `nat_inside` FK → IPAddress + `nat_ports` (e.g. `443→443, 80→80`), description.
  - Only assigned/reserved IPs are stored; the prefix detail page **generates** the full IP list and merges stored rows, so large subnets don't create thousands of rows.

### 6.5 monitoring
- **Monitor** — name, type (ping / tcp / http / keyword / dns / ssl), target (host/IP/URL), port, `interval_s` (default 60, min 20), `timeout_s` (default 10), `retries` (default 3), HTTP options (method, expected status codes, keyword, follow redirects, ignore TLS errors), DNS options (record type, expected value), SSL options (warn days, default 14), device FK (nullable), **`parent` FK → Monitor** (nullable; defaults to parent device's primary monitor), enabled, `next_check_at`.
- **MonitorState** (1:1) — `status` (UP / PENDING / DOWN / UNREACHABLE / MAINTENANCE / PAUSED), `consecutive_failures`, `last_check_at`, `last_change_at`, `last_response_ms`, `last_error`.
- **CheckResult** — monitor FK, timestamp, ok, response_ms, status_code, error_class, message. Indexed (monitor, timestamp). **Retention 30 days.**
- **DailyUptime** — monitor FK, date, checks, failures, uptime %, avg/p95 response. Kept forever.
- **Incident** — monitor FK, started_at, ended_at, cause, error_class, `suppressed_by` FK Incident (for unreachable children), acknowledged_by/at, notes.
- **MaintenanceWindow** — name, start, end, recurrence (none/daily/weekly), devices M2M, monitors M2M, include children (bool), reason, created_by.

### 6.6 alerts
- **Notification** (outbox) — user FK, incident FK, event (down / recovered / reminder / ssl_expiring / test), channel (telegram), payload, status (pending / sent / failed), attempts, next_attempt_at, sent_at, error. **Unique (incident, event, user, sequence)** → no duplicate alerts.
- **AlertSettings** (singleton) — bot token (encrypted), reminder interval (default 60 min), quiet behaviour, global enable.

### 6.7 vault
- **CredentialFolder** — name, parent folder (nullable), description. *(Folder-level permissions arrive in v2.)*
- **Credential** — folder FK, name, device FK (nullable), username, `secret_ciphertext` (bytes), `key_version`, url / host, `notes_ciphertext`, protocol (ssh/https/telnet/winbox/other), last_changed_at, soft delete.
- **CredentialHistory** — credential FK, previous `secret_ciphertext`, key_version, replaced_at, replaced_by. Revealable with the same flow (Admin only).
- **RevealLog** — user FK, credential FK, action (reveal / copy / reveal_failed / history_reveal), timestamp, IP, user agent. Append-only.

### 6.8 audit
- **AuditLog** — timestamp, actor FK (nullable for system), actor_repr, IP, user agent, action (create / update / delete / login / login_failed / logout / 2fa_failed / password_change / reveal / reveal_failed / vault_locked / import / export / settings_change / user_role_change / maintenance_start / …), object content type + id + repr, `changes` JSON (field: [old, new]).
  - Secrets are **never** written to the log (ciphertext fields masked as `"••• changed"`).
  - **Append-only**: no edit/delete in UI; Postgres trigger rejects `UPDATE`/`DELETE` on the table.
  - Request context (user, IP, UA) captured by middleware via `contextvars`; model changes captured by a registration helper using `pre_save`/`post_save`/`post_delete` signals.

---

## 7. Key designs

### 7.1 Monitoring engine
1. **Dispatcher** (celery-beat, every 10 s): selects monitors where `next_check_at <= now` using `SELECT … FOR UPDATE SKIP LOCKED`, bumps `next_check_at`, and enqueues them in **batches of ~50** to the `checks` queue.
2. **Checker worker** (dedicated Celery queue): runs each batch with `asyncio.gather` — `icmplib.async_multiping`, `httpx.AsyncClient`, `asyncio.open_connection`, `dnspython` async resolver, TLS handshake for cert expiry. ~17 checks/s at 1,000 monitors is light.
3. **Error classification** → cause shown in alerts:
   `timeout`, `host_unreachable`, `connection_refused`, `dns_failure`, `tls_error`, `ssl_expired`, `http_status (e.g. 503)`, `keyword_missing`, `unreachable_via_parent`.
4. **State machine** (per result, in a DB transaction):
   - Success → reset failures; if was DOWN/UNREACHABLE → **recover**: close incident, queue "recovered" (with downtime duration).
   - Failure → `consecutive_failures += 1`; status PENDING until it reaches `retries` (3).
   - On reaching threshold:
     - In active maintenance window → MAINTENANCE, no alert.
     - Any ancestor DOWN/UNREACHABLE → **UNREACHABLE**, incident linked via `suppressed_by`, no alert.
     - Any ancestor PENDING → wait one more cycle before deciding (avoids child alerts racing ahead of the parent).
     - Otherwise → **DOWN**, open incident, queue "down" alerts.
   - When a parent goes DOWN, currently-DOWN children are converted to UNREACHABLE and their unsent alerts cancelled.
5. **Maintenance**: monitors in a window keep running checks (history stays accurate) but never alert; ending a window re-evaluates state.
6. **Rollups & retention** (nightly): build `DailyUptime`, delete `CheckResult` older than 30 days.
7. **SSL expiry**: daily warning alert when ≤ `warn_days` remaining.

### 7.2 Telegram alerts
- **Bot setup**: Admin enters the bot token (created via @BotFather) in Settings; token stored encrypted.
- **User linking**: Profile → "Link Telegram" → one-time token → deep link `https://t.me/<bot>?start=<token>` → a long-polling task (`getUpdates`, every few seconds) receives `/start <token>` and stores the chat ID. No inbound webhook needed.
- **Recipients**: all Admins + Engineers with a linked chat and alerts not muted.
- **Outbox delivery**: notification rows are sent by a worker with exponential back-off (up to 24 h). If the internet is down, alerts queue up and send on recovery, marked *"delayed — originally at 14:02"*.
- **Message format**:
  ```
  🔴 DOWN: Core-Firewall (ping 10.0.0.1)
  Cause: timeout — no reply after 3 checks
  Since: 2026-11-02 14:02  ·  Affected: 14 devices unreachable
  ```
  ```
  🟢 UP: Core-Firewall — was down 12m 40s
  ```
- **Reminders**: every 60 min while an incident stays open.
- **"Send test alert"** button on Profile and Settings.

### 7.3 Vault
- **Encryption**: `MultiFernet` with keys loaded from `VAULT_KEYS` (Docker secret file, comma-separated, first = current). Each ciphertext records `key_version`.
- **Rotation**: `manage.py vault_rotate_key` re-encrypts all secrets + history with the new primary key, then the old key can be removed.
- **Reveal code**: set on Profile (6–32 chars, confirm twice, requires current login password), stored with Argon2. Changing it is audited.
- **Reveal flow**:
  1. Credential page shows `••••••••` — the secret is **never** in the initial HTML.
  2. User clicks **Reveal** → HTMX modal asks for reveal code → `POST /vault/<id>/reveal/`.
  3. Server checks role + code (rate-limited) → returns a fragment with the plaintext, `Cache-Control: no-store`. Logs `RevealLog` + `AuditLog`.
  4. Auto-hides after **30 s**; **Copy** button copies to clipboard and logs a `copy` event (`navigator.sendBeacon`).
  5. **5 failed attempts** → vault locked for that user, Admins notified on Telegram, Admin must unlock.
- **Reveal logs**: per credential ("who saw this password") and per user ("what did this user see"), filterable and exportable (Admin).

### 7.4 Security baseline
- HTTPS only (HSTS), secure + HttpOnly + SameSite cookies, CSRF everywhere, Django's built-in **CSP**, `X-Frame-Options: DENY`.
- **2FA (TOTP) mandatory** for all users; enforced on first login. Backup codes generated.
- `django-axes`: lock after 5 failed logins (per user+IP), Admin unlock.
- Session expiry after **30 min idle**; re-auth required for user management and settings.
- Rate limiting on login, reveal, and Telegram-link endpoints (Redis counters).
- Admin URL moved to a non-default path; Django admin restricted to superusers.
- Secrets (`SECRET_KEY`, DB password, `VAULT_KEYS`, bot token) only via env / Docker secrets, never in git.
- Nginx: request size limits, basic rate limits; listens on the LAN interface only.
- `ufw` on host: 443 (and SSH) allowed **from LAN subnets only**; no inbound port-forward on the firewall. Outbound HTTPS to `api.telegram.org` is the only internet access required.

### 7.5 UI
- Layout: left sidebar (Dashboard, Monitors, Devices, Racks, Connections, IPAM, Vault, Audit, Settings), top bar with global search (devices, IPs, monitors, credentials by name).
- Responsive: desktop-first; mobile usable for dashboard, monitor status, device lookup, and vault reveal.
- Dark mode (system default + toggle).
- Every list: search, filters, sort, pagination, export button, empty state with "Add first …" call to action.
- **Dashboard**: status counters (Up / Down / Unreachable / Maintenance / Paused), open incidents, recent alerts, SSL certs expiring, warranties expiring in 90 days, IP utilisation of public blocks. Refreshes via HTMX every 10 s.
- **Device page** tabs: Overview · Ports & Connections · IPs · Monitors · Credentials · Attributes · History (audit).
- **Rack page**: visual elevation (front/rear) with clickable devices.
- **Prefix page**: summary card (network, gateway, mask, broadcast, usable range, usage bar) + full IP table with inline assign.
- Forms: server-side validation, inline HTMX errors, double-submit prevention (button disable + idempotent POST handling).

### 7.6 Import / export
- Resources: Devices, Ports, Connections, Prefixes, IP Addresses, VLANs, Locations/Racks, Monitors. *(Credentials: export excluded for security; import Admin-only, v2.)*
- Downloadable **templates** (CSV & XLSX) with example rows.
- Import = upload → **dry-run preview** (rows to create / update / errors with line numbers) → confirm → commit in one transaction. Logged in audit.
- Natural keys for matching (e.g. device name, `device+port name`, IP address), so re-importing updates rather than duplicates.

---

## 8. Phased delivery

Each phase ends with: working feature, tests passing, a short demo/review with you, and a commit.

### Phase UI — Clickable UI prototype ✅ (done 2026-10-31)
- Full UI built as real Django templates (Tailwind v4 + HTMX + Alpine) with **fake data only** — no database, auth or workers.
- Design inspired by `design/design-ref.png`: dark-first theme (light theme + toggle), glowing gauge, status rings, gradient meters, Uptime-Kuma heartbeat bars.
- All pages from §7.5: dashboard, monitors (+ detail, add form), incidents, maintenance, devices (+ detail with front-panel port view, add form), racks (+ elevation), connections (+ cable trace through patch panels), VLANs, prefixes (+ detail with address map), ISPs & circuits, subnet calculator, vault (+ working reveal-code flow, demo code `123456`), reveal log, audit log, users & roles, profile, import/export (with preview), settings, login + 2FA, 404.
- Fake data lives in `apps/ui/fake_data.py`; views in `apps/ui/views.py`. Each later phase replaces one area's fake views with real models while **keeping the templates**.
- All third-party JS/fonts/icons are vendored in `static/` (LAN-only — no CDN at runtime).
- Verified: every page returns 200; no horizontal scroll at 390 px phone width.

### Phase 0 — Foundation
- ~~`git init`, `.gitignore`, `README.md`~~ (done in Phase UI); `pre-commit` (ruff); push to the private GitHub repo
- Split settings, `.env.example`, custom `User` model **before the first migration**; move `apps/ui` views into the real apps as they land
- Docker Compose (dev): web, postgres, redis, celery worker, celery beat
- Base layout: Tailwind + HTMX + Alpine, sidebar, dark mode, messages, empty-state & table components
- pytest + factory_boy setup, CI workflow file (GitHub Actions, if you use GitHub)
- **Done when:** `docker compose up` shows a styled login page; tests run green.

### Phase 1a — Authentication & authorization ✅ (approved by owner 2026-10-31)
- Done: custom `User` + `Profile`, roles as Groups (migration), capability map in `apps/accounts/roles.py`, `capability_required` on every restricted view, buttons/nav/search hidden by role, multi-step sign-in (password → forced new password → TOTP setup with QR + 10 backup codes / TOTP or backup code → reveal-code setup for vault roles), axes lockout, 30-min idle timeout (dashboard polling doesn't count), user management (create with one-time temp password, edit role, deactivate, reset password, reset 2FA, unlock sign-in, unlock vault, last-Admin guard), profile (password, reveal code, backup codes), `bootstrap_admin`.
- Checks: 176 pytest tests (every page × every role, full sign-in journeys, lockout, idle timeout, last-Admin rules), ruff clean, scripted browser walk-through.
- Decisions taken: **SQLite for now** (owner, 2026-10-31; PostgreSQL before the audit trigger/IPAM work). Axes lockout **expires after 30 min** in addition to Admin unlock, so a single Admin can't be permanently locked out. `django-axes` pinned to 8.3.1 (9.0.0 was one day old).
- Deferred to Phase 1b: audit framework.

### Phase 1b — Audit trail ✅ (approved by owner 2026-10-31)
- `apps/audit`: `AuditLog` (actor, IP, browser, action, object, before→after `changes`, message). Append-only three ways: model guards, queryset guards, and **database triggers** (SQLite now; the PostgreSQL trigger is in the same migration). Deleting a user never touches audit rows.
- `log_event()` for explicit events; `track(Model, exclude=…, mask=…)` for automatic create/update/delete diffs — later phases register their models with it. Secrets are masked ("••• changed"); passwords, temporary passwords, backup codes and reveal codes never reach the log.
- Recorded now: sign-in, sign-out (incl. idle timeout), failed sign-in (with attempted username), lockouts, 2FA setup / wrong codes / backup-code use / new backup codes, password and reveal-code changes, user create/edit (field diffs), role changes, deactivate/activate, password reset, 2FA reset, sign-in and vault unlocks, audit CSV exports; `bootstrap_admin` is attributed to "Command line".
- UI: Audit log page (search, user, action, date range, pagination, expandable before/after, CSV export), dashboard "Recent activity" (Admins), per-user activity on the user page.
- Checks: 210 pytest tests, ruff clean, browser walk-through.
- Note: IP is taken from the direct connection; when Nginx is added (Phase 9) the trusted proxy header must be read instead (`apps/audit/context.py: client_ip`).

### Phase 1 — Accounts, roles, security & audit
- Roles (Groups via data migration), `RoleRequiredMixin`, URL-access test harness
- Login, forced password change, **mandatory TOTP 2FA** + backup codes, axes lockout, idle timeout
- Admin user management: create user (temporary password), assign role, deactivate, unlock
- Profile page: change password, set/change **reveal code**, 2FA management (Telegram link placeholder)
- `manage.py bootstrap_admin` for first run
- **Audit framework**: middleware context, signal registration, append-only trigger, audit log viewer with filters
- **Done when:** Admin can create an Engineer and Viewer; each sees only what they should; every login/change appears in the audit log.

### Phase 2 — Inventory & racks
- Location, Rack, Manufacturer, DeviceModel (with port templates), Device, DeviceAttribute
- CRUD with filters/search, retire vs delete, rack U-overlap validation
- Rack elevation view, device detail page (tabs scaffolded)
- **Done when:** you can model Rack 1 with a firewall and Cisco switch placed in the correct U positions.

### Phase 3 — Ports, VLANs & connections
- VLAN CRUD; Port CRUD + bulk create from pattern (`Gi1/0/[1-48]`) and auto-create from model template
- Connection create/edit from either side, external endpoints, patch-panel pass-through, one-port-one-connection constraint
- Device "Ports & Connections" tab; global connections table (filter by rack, device, VLAN, function); path trace view
- **Done when:** "Firewall port 1 → Switch port 10, function: Internet + VLAN trunk" is entered once and appears on both devices and in the global table.

### Phase 4 — IPAM
- ISP, Circuit, Prefix (with calculator), IPAddress, NAT mapping
- Prefix page with generated IP list, inline assignment, utilisation; IP ↔ device/port linking; device "IPs" tab
- Containment & duplicate validation; `/31`, `/32` edge cases
- **Done when:** entering `129.205.100.140/28` shows network `.128`, usable `.129–.142`, broadcast `.143`, mask `255.255.255.240`, and each IP can be assigned.
  *(Note: `.140/28` normalises to network `129.205.100.128/28` — the UI will warn and offer to correct a host-bit address.)*

### Phase 5 — Monitoring engine & dashboard
- Monitor + MonitorState + CheckResult + Incident + MaintenanceWindow models
- Async checkers (ping, tcp, http/keyword, dns, ssl), dispatcher, dedicated `checks` queue
- State machine with retries, dependencies (UNREACHABLE), maintenance suppression
- Monitor list/detail (status, uptime 24h/7d/30d, response-time chart, incident history), quick "monitor this device" from device page
- Dashboard; nightly rollups & retention
- **Done when:** unplugging a test device produces DOWN after 3 checks, its children show UNREACHABLE, and recovery closes the incident — all covered by automated tests with fake targets.

### Phase 6 — Telegram alerts
- Bot settings, user linking via deep link, long-poll updates task
- Notification outbox, retries/back-off, delayed-delivery marking, reminders, recovery messages, SSL-expiry warnings
- Per-user mute, test-alert button, vault-lockout alert to Admins
- **Done when:** a real phone receives DOWN → reminder → UP messages for a test monitor, and no alert is sent during a maintenance window.

### Phase 7 — Password vault
- Crypto module (MultiFernet, key versions), key-rotation command
- Folders, credentials (linked to devices), history
- Reveal/copy flow with reveal code, auto-hide, lockout, RevealLog views
- Device "Credentials" tab
- **Done when:** a reveal without the right code is impossible, every reveal/copy/failure is logged, DB dumps contain only ciphertext, and key rotation works end-to-end.

### Phase 8 — Import / export
- Resources, templates, dry-run preview, commit, audit entries
- Export buttons on every major list (CSV/XLSX); rack and port-map export
- **Done when:** a 200-row device + port + connection spreadsheet imports cleanly, and a file with conflicts is rejected with clear row-level errors.

### Phase 9 — Production deployment & hardening
- `compose.prod.yml`: Nginx (internal TLS cert), Gunicorn, workers, beat, Postgres, Redis; `sysctls: net.ipv4.ping_group_range` for unprivileged ping
- Ubuntu host hardening guide (ufw LAN-only, unattended-upgrades, SSH keys only)
- **Backups**: nightly encrypted `pg_dump` + media → local backup folder now, **NAS** target added when available (setting already in the UI); vault key backed up **separately** (offline); documented and **tested restore**
- **Self-monitoring**: app exposes `/health/`; an external heartbeat (e.g. healthchecks.io free tier or a small VPS) alerts if the app goes silent
- Error logging (structured logs, Django error emails or Sentry self-hosted — optional), Celery queue-depth check
- Security review pass + `manage.py check --deploy` clean
- **Done when:** the app runs on the Ubuntu server over HTTPS on the LAN (and is not reachable from the internet), backups restore successfully on a fresh VM, and the external heartbeat fires when the app is stopped.

---

## 9. Testing strategy

| Area | What must be tested |
|---|---|
| Permissions | Every URL × every role (parametrised); Viewer can't reach vault; Engineer can't manage users |
| Audit | Create/update/delete produce correct diffs; secrets masked; log is append-only (trigger test) |
| IPAM | Subnet math for /8–/32 incl. /31, /32; containment; duplicate IP rejection; NAT links |
| DCIM | Rack overlap; one-connection-per-port; connection visible on both ends; path trace through patch panels |
| Monitoring | Each checker vs local fake servers (HTTP 200/503/keyword, closed port, slow host, bad cert); state machine transitions; parent/child suppression; maintenance; race where child fails before parent |
| Alerts | Outbox idempotency (no duplicates), retries, delayed marking, reminders; Telegram API mocked |
| Vault | Encrypt/decrypt round-trip, key rotation, reveal requires code, lockout at 5, plaintext never in list/detail HTML, every reveal logged |
| Import | Dry-run doesn't write; errors reported per row; re-import updates not duplicates |

Target: ≥ 85% coverage on `monitoring`, `vault`, `ipam`, `audit`, `accounts`.

---

## 10. Definition of done for v1

- All phases 0–9 complete and demoed.
- All tests green; `manage.py check --deploy` clean; ruff clean.
- Deployed on the Ubuntu server, HTTPS, LAN-only, 2FA enforced.
- Real Telegram alerts verified for down, reminder, recovery, and maintenance suppression.
- Backup + restore tested; vault key stored off-server.
- README covers install, upgrade, backup/restore, key rotation, adding the Telegram bot.

---

## 11. Assumptions (confirm or correct)

| # | Assumption |
|---|---|
| A1 | Vault master key lives on the server (Docker secret file), not an external KMS |
| A2 | Default check interval 60 s, configurable per monitor, minimum 20 s |
| A3 | Single site initially; everything is attached to a Location |
| A4 | IPv4 only in v1; multiple ISPs supported |
| A5 | Device dependencies (parent) are set manually; auto-suggest from uplink in v2 |
| A6 | Alerts go to all Admins + Engineers with linked Telegram; per-user mute; reminder every 60 min |
| A7 | 2FA stays mandatory for **all** users even though the app is LAN-only, because it guards the vault (configurable in Settings → Security) |
| A8 | Raw check results kept 30 days; daily uptime summaries forever |
| A9 | No public status page in v1 (internal dashboards only) |
| A10 | Docker Compose deployment; no separate staging server (dev = local Compose, prod = Ubuntu server) |
| A11 | "Log everything" = all changes, auth/security events, reveals, imports, exports, settings — **not** plain page views |
| A12 | No fixed deadline; phase-by-phase delivery with review |
| A13 | Engineers may export data; credentials are never exportable in v1 |
| A14 | Time zone is a single configurable app setting (stored in UTC, displayed in local time) |

---

## 12. Decisions still needed from you

| # | Question | Status |
|---|---|---|
| D1 | Internet or LAN access? | ✅ **LAN only** (2026-10-31) |
| D2 | Where do off-server backups go? | ✅ **NAS** once set up; local backups until then (see risk table) |
| D3 | Domain name for Let's Encrypt? | ✅ Not needed — LAN-only uses an internal certificate. Optional: an internal DNS name such as `netcore.lan` |
| D4 | Code on GitHub? | ✅ **Yes**, private repo |
| D5 | Should plain **page views** also be audited? | Open — recommendation: no (noise) |

---

## 13. Open risks

| Risk | Impact | Mitigation |
|---|---|---|
| Vault key lost | All stored passwords unrecoverable | Offline key backup (printed/USB in safe) separate from DB backups; restore drill |
| Server compromised (key + DB together) | All passwords exposed | 2FA, LAN-only, host hardening, timely updates, rotate device passwords after any incident |
| No NAS yet — backups only on the server | Disk failure loses everything | Settings → Backups shows a warning until the NAS target is set; copy the vault key offline now |
| Monitor host or ISP link down | No alerts can be sent | Outbox queues + delayed delivery; external heartbeat (Phase 9) |
| LAN-only: no alerts reach you if the server itself dies | Silent failure | External heartbeat is **outbound-only** (server pings healthchecks.io), so it still works without inbound access |
| Alpine.js needs `unsafe-eval` under a strict CSP | Weaker CSP | Move to `@alpinejs/csp` build (register components with `Alpine.data`) in Phase 0, or accept on a LAN-only app |
| ICMP inside Docker | Ping checks fail | `ping_group_range` sysctl / `NET_RAW` capability, tested in Phase 5 |
| Static reveal code | Could be observed/reused | Lockout, full logging, Telegram one-time code option in v2 |
| Telegram API unreachable/blocked | Missed alerts | Outbox retries; WhatsApp/email as second channel in v2 |
| Messy imports | Conflicting connections/IPs | Dry-run validation with row-level errors |
| Library lag behind Django 6.1 | Blocked upgrade | Verify each dependency's support at Phase 0; fallback to Django 5.2 LTS |

---

## 14. v2 backlog (not blocked by v1 design)

- SNMP polling (interface status, CPU, temperature) and SNMP traps
- WhatsApp (Meta Cloud API) and email alert channels
- Reveal code delivered as one-time code via Telegram/WhatsApp
- Per-folder / per-credential vault permissions
- Public status page
- IPv6 IPAM
- Auto-suggest dependencies from uplink connections; topology map
- Custom-field builder per device type
- Network discovery / ARP & MAC table import
- Device config backups
- Multi-site probes (remote agents)
- REST API / mobile app
