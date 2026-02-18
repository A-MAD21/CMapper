# CMapper - Detailed Handoff Notes (What Changed and Why)

Use this to brief another assistant on the exact changes made, the current behavior, and where to look.

---

## 1) Authentication + Role-Based Access Control

### Goals implemented
- Add login (username/password) to the app.
- Support 3 roles:
  1) admin: full control
  2) operator: read/write only on assigned sites
  3) guest: read-only on assigned sites
- Admin can manage users (create/remove, assign allowed sites, reset passwords, disable).
- Users can change their own password.
- �Allowed sites� supports an **All sites** option that stays valid when new sites are added.

### How it works (backend)
- Auth config stored in `settings.json` under `auth`.
- Users are stored with hashed passwords, role, allowed_sites, disabled flag.

#### Settings structure
```
"auth": {
  "enabled": true,
  "users": [
    {
      "username": "admin",
      "password_hash": "...",
      "role": "admin",
      "allowed_sites": ["*"] or ["SiteA","SiteB"],
      "disabled": false
    }
  ]
}
```

#### Key helper functions (Backend.py)
- `_get_effective_user()`
  - If auth is disabled, returns a synthetic admin user.
  - If auth enabled, reads user from session; returns None if missing/disabled.
- `_allowed_sites(user)` returns allowed site list (or `[]`).
- `*` means **all sites**.
- `_can_read_site` / `_can_write_site` enforce role + site rules.
- `_filter_sites_for_user` and `_filter_devices_for_user` enforce site scoping.

#### Auth endpoints
- `GET /api/auth/me` -> { auth_required, authenticated, user, role, allowed_sites }
- `POST /api/auth/login` -> login (blocks disabled users)
- `POST /api/auth/logout`
- `POST /api/auth/setup` -> initial admin (or admin update)
- `POST /api/auth/change_password` -> self-service
- `PUT /api/auth/config` -> toggle auth enabled

#### User management endpoints (admin only)
- `GET /api/users` -> list users (sanitized)
- `POST /api/users` -> create user
- `PUT /api/users/<username>` -> update role/sites/disabled/password
- `DELETE /api/users/<username>` -> remove

### Where enforcement happens
- Most API endpoints now check current user and role.
- Guests blocked from writes and module runs.
- Operators can write only on assigned sites.
- Admins unrestricted.

### Map file access rules
- `/generated_maps/*` and `/static/maps/*` require auth when enabled.
- Access uses allowed site list. "All sites" (`*`) bypasses filter.
- Site name matching uses safe normalization for map filenames.

### Session behavior
- Login uses Flask session cookie.
- Logout now **forces page reload** to prevent stale data showing for next user.

---

## 2) Settings UI: Admin + User Management

### UI changes (Templates/index.html)
- The duplicate empty Settings tab was removed (there used to be two settings sections).
- Settings now contains:
  - My Password (change own password)
  - Admin Login Setup (admin only)
  - User Management (admin only)

### User Management UI
- Admin can add users with:
  - username, password, role
  - allowed sites (multi-select with checkboxes)
- For existing users:
  - change role
  - change allowed sites
  - disable/enable
  - reset password
  - remove user

### Allowed Sites UI
- Multi-select dropdown with checkboxes.
- Includes �All sites� checkbox (stores `*`).
- If �All sites� checked, other site checkboxes are disabled.
- Site selection uses actual site list from `/api/sites`.

---

## 3) Map Tab Behavior

### Map access
- Guests can view **existing** maps but cannot generate.
- Admin/Operator can generate maps.

### Guest �Show Map� flow
- When clicking Show Map:
  - The UI requests `/api/map/<site>`.
  - If `map_url` returned -> iframe loads map.
  - If no map exists -> shows �No Map�.
  - Guests do NOT call `/api/generate_visual_map` anymore.

### Windows map URL fix
- `/api/map` now normalizes Windows backslashes when deciding map URL.
- If file is in `generated_maps`, URL becomes `/generated_maps/<file>`.

---

## 4) No External CDN Assets

### Removal
- Removed `https://unpkg.com/feather-icons`.

### Replacement
- Added local placeholder icon shim: `Static/js/feather-local.js`.
- `Templates/index.html` now loads `/static/js/feather-local.js`.
- The shim replaces `data-feather` elements with a basic inline SVG containing the first letter of the icon name.
- This avoids any external network calls.

---

## 5) Performance / Loading Improvements

### Backend timing logs
- Added an `after_request` logger that prints `[PERF] <path> took Xs`.
- Logs for API and map endpoints help identify slow routes.

### Frontend non-blocking load
- `loadData()` now loads sites/devices/stats/modules independently (not all-or-nothing).
- Each request has a timeout (default 8�10s).
- UI renders quickly and fills sections as data arrives.

---

## 6) Files Modified / Added

Modified:
- `Backend.py`
- `Templates/index.html`
- `Static/js/dashboard.js`

Added:
- `Static/js/feather-local.js`

Updated config:
- `settings.json` now contains auth users + enabled flag.

---

## 7) Known Behaviors / Troubleshooting

- If a guest sees �No Map�, it means no map file exists for that site.
  - Admin/Operator must generate first.
- If admin/guest can�t load a map:
  - Check that `/api/map/<site>` returns a `/generated_maps/...` URL.
  - Ensure the filename exists under `generated_maps/`.
- If login UI doesn�t show, check `settings.json` auth settings:
  - `auth.enabled` must be true
  - `auth.users` must contain at least one user

---

## 8) Monitoring & Logs (current behavior)
- Background ping loop every 5s for monitoring-enabled devices only; results stored in `monitoring.db` (JSON). Frontend shows a monitoring board with three lanes and draggable device chips; connections drawn from `devices.db` links. Toggle Monitoring button turns green when enabled; per-device rules modal supports latency + packet loss thresholds (future rules can be added). Activity logs per site live in `logs/<site>.log` with 60-day retention; monitoring APIs use file locks to avoid PermissionError on Windows.

## 9) Export/Import
- Admin-only `GET /api/export` and `POST /api/import` available in Settings → Data Transfer. Export zip includes `devices.db`, `settings.json`, `monitoring.db`. Import replaces those files and reloads.

## 10) New Module: MikroTik MAC Discovery
- Location: `Modules/mikrotik_mac_discovery/` (`module.json`, `mikrotik_mac_discovery.py`, editable `oui_ranges.txt`).
- Purpose: SSH to RouterOS and run `/tool/ip/scan` (fallback `/tool/mac-scan`) for a duration (default 30s, configurable). Collect IP/MAC/identity/interface, map vendor from local OUI ranges (format `AA:BB:CC:00:00:00-AA:BB:CC:FF:FF:FF=Vendor`). Only brand is inferred—no device type guessing. Optional note appended to device notes.
- Database update: writes into `devices.db` for the selected site with ids `dev_mac_<mac>_<site>`, sets vendor, mac, ip, name (identity or MAC), discovered_by `mikrotik_mac_discovery`, and timestamps; updates existing matching MAC in same site instead of duplicating.
- Frontend: automatically appears in Modules list; run requires Router IP, username, password, optional interface, scan duration (defaults to 30s), optional note.
- Windows note: SSH requires PuTTY `plink.exe` (or SSH keys). Set `PLINK_BIN` env var if `plink` is not on PATH.

---

## 11) Database Scale Note (future)
- Current storage is JSON (`devices.db`). For ~100 sites × 100 nodes (~10k devices) it should work, but performance will degrade because every read/write loads the entire file.
- If growth/updates increase, plan a migration to SQLite while keeping API responses the same.
- Migration would mainly touch Backend read/write helpers and any module that writes `devices.db` (update to DB queries).

---

## 12) OUI Table Format (changed)
- `Modules/mikrotik_mac_discovery/oui_ranges.txt` now supports device type inline:
  - `AA:BB:CC:00:00:00-AA:BB:CC:FF:FF:FF=Vendor,device_type=ap`
- This replaces the old separate `oui_device_types.txt` mapping (no longer used by Enforce OUI).
- UI "Edit OUI" now has a dropdown for device type and writes the `device_type=` into the same line.
- Mikrotik MAC discovery and Enforce OUI both parse the label before the comma and optional device_type.

---

## 13) Uniview NVR Packet Capture Module
- New module: `Modules/uniview_nvr_capture/` (uses Digest auth via `requests`).
- UI is a device table listing NVRs (pre-selected) + NIC (1/2), packet size, IP/Port modes (all/specify/filter).
- Captures for a fixed 30s internally, Start/Stop via:
  - `PUT /LAPI/V1.1/Network/PacketCapture/Start`
  - `PUT /LAPI/V1.1/Network/PacketCapture/Stop` (best-effort)
  - Download: `GET /LAPI/V1.0/Network/PacketCapture/File/DownLoad`
- Saves pcaps under `generated_maps/nvr_captures/`.
- No CDP parsing yet; only capture/download.
- Requires `requests` in `requirements.txt`.

---

This summary matches the current codebase in `D:\Net Automation\SW mapper\Test1\CMapper1`.
