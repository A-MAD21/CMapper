#!/usr/bin/env python3
"""
Add Device Manually - REAL Module Example
This shows exactly how to write modules for the platform
"""

import json
import sys
import os
from datetime import datetime
import uuid

def main():
    print("DEBUG: Module starting", file=sys.stderr)
    print(f"DEBUG: Args: {sys.argv}", file=sys.stderr)
    
    # 1. Read config passed by platform
    if len(sys.argv) < 2:
        error_msg = {"error": "No config file provided", "status": "failed"}
        print(f"DEBUG: Error: {error_msg}", file=sys.stderr)
        print(json.dumps(error_msg))
        sys.exit(1)
    
    config_path = sys.argv[1]
    print(f"DEBUG: Config path: {config_path}", file=sys.stderr)
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"DEBUG: Config loaded: {config}", file=sys.stderr)
    except Exception as e:
        error_msg = {"error": f"Failed to read config: {str(e)}", "status": "failed"}
        print(f"DEBUG: Error reading config: {error_msg}", file=sys.stderr)
        print(json.dumps(error_msg))
        sys.exit(1)
    
    # ... rest of the code ...
    
    # 2. Read current database
    db_path = config.get("database_path", "database.json")
    
    try:
        with open(db_path, 'r') as f:
            database = json.load(f)
    except Exception as e:
        print(json.dumps({
            "error": f"Failed to read database: {str(e)}",
            "status": "failed"
        }))
        sys.exit(1)
    
    # 3. Get parameters from config
    params = config.get("parameters", {})
    ip = params.get("ip", "").strip()
    name = params.get("name", "").strip()
    device_type = params.get("device_type", "router")
    site_name = config.get("site_name", "").strip()
    
    # 4. Validate inputs
    if not ip:
        print(json.dumps({
            "error": "IP address is required",
            "status": "failed"
        }))
        sys.exit(1)
    
    if not name:
        print(json.dumps({
            "error": "Device name is required",
            "status": "failed"
        }))
        sys.exit(1)
    
    if not site_name:
        print(json.dumps({
            "error": "Site name is required",
            "status": "failed"
        }))
        sys.exit(1)
    
    # 5. Check if site exists
    site_exists = any(s.get("name") == site_name for s in database.get("sites", []))
    if not site_exists:
        print(json.dumps({
            "error": f"Site '{site_name}' does not exist. Create it first.",
            "status": "failed"
        }))
        sys.exit(1)
    
    # 6. Check for duplicate device (same IP in same site)
    for device in database.get("devices", []):
        if device.get("ip") == ip and device.get("site") == site_name:
            print(json.dumps({
                "error": f"Device with IP {ip} already exists in site {site_name}",
                "status": "failed"
            }))
            sys.exit(1)
    
    # 7. Create new device
    new_device = {
        "id": f"dev_{str(uuid.uuid4())[:8]}",
        "site": site_name,
        "name": name,
        "ip": ip,
        "type": device_type,
        "discovered_by": "manual",
        "discovered_at": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "status": "unknown",
        "reachable": False,
        "config_backup": {
            "enabled": False,
            "last_backup": None,
            "path": None
        },
        "connections": [],
        "credentials_used": None,
        "modules_successful": ["add_device_manual"],
        "modules_failed": [],
        "locked": False,
        "notes": "Added manually via Add Device module"
    }
    
    # 8. Add to database
    if "devices" not in database:
        database["devices"] = []
    
    database["devices"].append(new_device)
    
    # 9. Write back to database
    try:
        with open(db_path, 'w') as f:
            json.dump(database, f, indent=2)
    except Exception as e:
        print(json.dumps({
            "error": f"Failed to write database: {str(e)}",
            "status": "failed"
        }))
        sys.exit(1)
    
    # 10. Return success
    result = {
        "status": "success",
        "message": f"Device '{name}' ({ip}) added to site '{site_name}'",
        "data": {
            "device_id": new_device["id"],
            "device_added": True,
            "device": new_device
        }
    }
    
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()