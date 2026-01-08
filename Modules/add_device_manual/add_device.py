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
    device_type = params.get("device_type", "switch")
    os_name = params.get("os", "").strip()
    platform = params.get("platform", "").strip()
    vendor = params.get("vendor", "").strip()
    notes = params.get("notes", "").strip()
    links_raw = params.get("links", "")
    remote_device_id = params.get("remote_device_id", "").strip()
    local_interface = params.get("local_interface", "").strip()
    remote_interface = params.get("remote_interface", "").strip()
    link_protocol = params.get("protocol", "").strip() or "manual"
    add_reverse_links = bool(params.get("add_reverse_links", False))
    site_name = config.get("site_name", "").strip()
    
    # 4. Validate inputs
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
    
    # 6. Check for duplicate device (same IP or name in same site)
    for device in database.get("devices", []):
        if device.get("site") != site_name:
            continue
        if ip and device.get("ip") == ip:
            print(json.dumps({
                "error": f"Device with IP {ip} already exists in site {site_name}",
                "status": "failed"
            }))
            sys.exit(1)
        if device.get("name") == name:
            print(json.dumps({
                "error": f"Device named '{name}' already exists in site {site_name}",
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
        "model": platform,
        "platform": platform,
        "vendor": vendor,
        "os": os_name,
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
        "notes": notes or "Added manually via Add Device module"
    }

    def normalize_token(value):
        return value.strip().lower()

    def parse_links(value):
        if not value:
            return []
        if not isinstance(value, str):
            return []
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        parsed = []
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            while len(parts) < 4:
                parts.append("")
            parsed.append({
                "local_interface": parts[0],
                "remote_lookup": parts[1],
                "remote_interface": parts[2],
                "protocol": parts[3] or "manual",
            })
        return parsed

    def find_device_by_id(devices, site, device_id):
        for device in devices:
            if device.get("site") == site and device.get("id") == device_id:
                return device
        return None

    def find_device_by_name_or_ip(devices, site, token):
        token_norm = normalize_token(token)
        for device in devices:
            if device.get("site") != site:
                continue
            if normalize_token(str(device.get("name", ""))) == token_norm:
                return device
            if normalize_token(str(device.get("ip", ""))) == token_norm:
                return device
        return None

    def create_placeholder_device(site, token):
        placeholder = {
            "id": f"dev_{str(uuid.uuid4())[:8]}",
            "site": site,
            "name": token,
            "ip": token if token.count(".") == 3 else "",
            "type": "unknown",
            "model": "",
            "platform": "",
            "vendor": "",
            "os": "",
            "discovered_by": "manual",
            "discovered_at": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "status": "unknown",
            "reachable": False,
            "config_backup": {"enabled": False},
            "connections": [],
            "credentials_used": None,
            "modules_successful": ["add_device_manual"],
            "modules_failed": [],
            "locked": False,
            "notes": "Placeholder created via manual links"
        }
        return placeholder
    
    # 8. Add to database
    if "devices" not in database:
        database["devices"] = []
    
    database["devices"].append(new_device)

    devices = database["devices"]
    links = parse_links(links_raw)
    if remote_device_id:
        links.append({
            "local_interface": local_interface,
            "remote_lookup": remote_device_id,
            "remote_interface": remote_interface,
            "protocol": link_protocol or "manual",
            "lookup_by_id": True
        })
    connections_added = 0
    placeholders_created = 0

    for link in links:
        remote_token = link.get("remote_lookup", "").strip()
        if not remote_token:
            continue
        if link.get("lookup_by_id"):
            remote_device = find_device_by_id(devices, site_name, remote_token)
        else:
            remote_device = find_device_by_name_or_ip(devices, site_name, remote_token)
        if remote_device is None:
            if link.get("lookup_by_id"):
                continue
            remote_device = create_placeholder_device(site_name, remote_token)
            devices.append(remote_device)
            placeholders_created += 1

        new_device["connections"].append({
            "id": f"conn_{str(uuid.uuid4())[:8]}",
            "local_interface": link.get("local_interface") or "unknown",
            "remote_device": remote_device["id"],
            "remote_interface": link.get("remote_interface") or "unknown",
            "protocol": link.get("protocol") or "manual",
            "discovered_at": datetime.now().isoformat(),
            "status": "up"
        })
        connections_added += 1

        if add_reverse_links:
            reverse_local = link.get("remote_interface") or "unknown"
            reverse_remote = link.get("local_interface") or "unknown"
            existing_reverse = False
            for existing in remote_device.get("connections", []):
                if existing.get("remote_device") == new_device["id"] and existing.get("local_interface") == reverse_local:
                    existing_reverse = True
                    break
            if not existing_reverse:
                remote_device.setdefault("connections", []).append({
                    "id": f"conn_{str(uuid.uuid4())[:8]}",
                    "local_interface": reverse_local,
                    "remote_device": new_device["id"],
                    "remote_interface": reverse_remote,
                    "protocol": link.get("protocol") or "manual",
                    "discovered_at": datetime.now().isoformat(),
                    "status": "up"
                })
    
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
            "device": new_device,
            "connections_added": connections_added,
            "placeholders_created": placeholders_created
        }
    }
    
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
