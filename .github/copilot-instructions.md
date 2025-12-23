# Copilot Instructions for Network Discovery Platform

## Architecture Overview
This is a Flask-based web application for automated network topology discovery and visualization. The system uses a modular architecture where discovery modules (e.g., CDP) populate a JSON database (`database.json`), and visualization modules generate interactive HTML maps.

**Key Components:**
- `Backend.py`: Main Flask app handling web routes, module execution, and database operations
- `Modules/`: Directory containing pluggable discovery/visualization modules (e.g., `cdp_discovery/`, `view_map/`)
- `database.json`: JSON file storing sites, devices, and connections discovered via modules
- `generate_map.py`: Script that reads `database.json` and generates D3.js-based HTML topology maps

**Data Flow:**
1. Modules like `cdp_discovery` SSH into network devices (Cisco switches) using Netmiko
2. Parse `show cdp neighbors` output to discover connected devices
3. Update `database.json` with device info and bidirectional connections
4. `generate_map.py` creates visual maps (e.g., `Roodan_map.html`) from database data

## Developer Workflows

### Running the Application
```bash
python Backend.py
```
Starts the Flask development server. Access the web UI at `http://localhost:5000` to run discovery modules and view generated maps.

### Module Development
- Create a new directory under `Modules/` (e.g., `Modules/my_module/`)
- Add `module.json` with metadata:
  ```json
  {
    "name": "My Discovery Module",
    "description": "Discovers devices via custom protocol",
    "parameters": ["target_ip", "credentials"]
  }
  ```
- Implement `my_module.py` that reads config from stdin (JSON), writes results to stdout (JSON)
- Backend runs modules asynchronously via `ModuleRunner` class

### Database Operations
- Use `read_database()` and `write_database()` functions in `Backend.py` for thread-safe JSON file access
- Devices have connections with `local_interface`, `remote_device`, `remote_interface` (often null for CDP)
- Sites group devices by location (e.g., "Roodan", "Mashhad")

### Map Generation
- Run `python generate_map.py` to regenerate HTML maps from current database
- Maps use D3.js force-directed graphs; customize styling in embedded CSS
- Visual maps saved as `{site_name}_map.html` (e.g., `Roodan_map.html`)

## Project Conventions

### Dependencies
- Use Netmiko for Cisco device SSH connections (fallback to paramiko)
- Portalocker for file locking on `database.json` writes
- Keep dependencies minimal; add to `requirements.txt`

### Error Handling
- Modules return JSON with `status` ("success", "error", "warning") and `message`
- Backend logs module output to stderr for debugging
- Database operations include try/catch for JSON parsing errors

### Code Patterns
- Device discovery: Parse CLI output with regex (e.g., CDP neighbor tables)
- Connection deduplication: Use sorted tuples as keys for bidirectional links
- Asynchronous execution: Use `threading` for non-blocking module runs
- File paths: Relative to project root; use `os.path.join()` for cross-platform compatibility

### Testing
- Manual testing via web UI; check generated maps for accuracy
- Debug modules by running directly: `python Modules/cdp_discovery/cdp_module.py < config.json`
- Validate database integrity after discovery runs

## Integration Points
- External: SSH to network devices (credentials stored in database per device)
- Internal: Modules communicate via JSON config/results; no direct API calls
- Web UI: Flask routes serve static files (`Static/`), templates (`Templates/`), and API endpoints for module execution</content>
<parameter name="filePath">d:\Net Automation\SW mapper\Test1\.github\copilot-instructions.md