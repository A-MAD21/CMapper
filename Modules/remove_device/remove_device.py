#!/usr/bin/env python3
"""
Remove Device - module for deleting a device and handling dependencies.
"""

import json
import sys
from datetime import datetime


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No config file provided", "status": "failed"}))
        sys.exit(1)

    config_path = sys.argv[1]
    try:
        with open(config_path, "r") as handle:
            config = json.load(handle)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to read config: {exc}", "status": "failed"}))
        sys.exit(1)

    site_name = (config.get("site_name") or "").strip()
    params = config.get("parameters", {})
    device_id = (params.get("device_id") or "").strip()
    keep_dependents = bool(params.get("keep_dependents", True))
    db_path = config.get("database_path", "database.json")

    if not site_name:
        print(json.dumps({"error": "Site name is required", "status": "failed"}))
        sys.exit(1)
    if not device_id:
        print(json.dumps({"error": "Device id is required", "status": "failed"}))
        sys.exit(1)

    try:
        with open(db_path, "r") as handle:
            database = json.load(handle)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to read database: {exc}", "status": "failed"}))
        sys.exit(1)

    devices = database.get("devices", [])
    target = next((d for d in devices if d.get("id") == device_id and d.get("site") == site_name), None)
    if not target:
        print(json.dumps({"error": "Device not found in site", "status": "failed"}))
        sys.exit(1)

    target_id = target.get("id")
    dependents = set()

    for device in devices:
        if device.get("site") != site_name:
            continue
        if device.get("id") == target_id:
            for conn in device.get("connections") or []:
                rid = conn.get("remote_device")
                if rid:
                    dependents.add(rid)
            continue
        for conn in device.get("connections") or []:
            if conn.get("remote_device") == target_id:
                dependents.add(device.get("id"))
                break

    remove_ids = {target_id}
    if not keep_dependents:
        remove_ids.update(dependents)

    kept_devices = []
    for device in devices:
        if device.get("id") in remove_ids and device.get("site") == site_name:
            continue
        kept_devices.append(device)

    for device in kept_devices:
        if device.get("site") != site_name:
            continue
        connections = device.get("connections") or []
        device["connections"] = [
            conn for conn in connections if conn.get("remote_device") not in remove_ids
        ]
        device["last_modified"] = datetime.now().isoformat()

    database["devices"] = kept_devices

    try:
        with open(db_path, "w") as handle:
            json.dump(database, handle, indent=2)
    except Exception as exc:
        print(json.dumps({"error": f"Failed to write database: {exc}", "status": "failed"}))
        sys.exit(1)

    result = {
        "status": "success",
        "message": "Device removal completed",
        "data": {
            "device_removed": True,
            "devices_removed": len(remove_ids),
            "removed_ids": sorted(remove_ids),
            "dependents_found": len(dependents),
            "dependents_removed": 0 if keep_dependents else len(dependents),
        },
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
