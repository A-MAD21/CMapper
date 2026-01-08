# CMapper

## What this app does
- Flask web app for building and viewing network maps.
- Stores sites, devices, and discovery sessions in `devices.db` (JSON).
- Runs discovery modules from `Modules/` asynchronously.
- Generates HTML maps into `generated_maps/`.
- Provides API endpoints for sites/devices/modules/settings/stats.

## Key entry points
- `Backend.py`: Flask backend and API routes.
- `Templates/`: HTML UI templates.
- `Static/`: CSS/JS assets.
- `Modules/`: discovery modules (each module has `module.json` + python script).
- `generated_maps/`: output map files.

## Local run (typical)
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python Backend.py
```

## Troubleshooting
- If the UI appears stuck on load, the `feather-icons` CDN may be blocked, which can break initialization. Guard the `feather.replace()` call or host the script locally.

## Notes / TODO
- Confirm discovery workflow per module (CDP, manual add, etc.).
- Validate generated map paths and URL routing.

## Discovery Ideas (Non-CDP)
- SNMP polling to learn vendor/model, interfaces, and MAC tables.
- DHCP/DNS/AD inventory for hosts and IPs without active scanning.
- MAC OUI lookup to classify devices by vendor.
- Passive capture (SPAN/tap) to discover hosts and relationships.
