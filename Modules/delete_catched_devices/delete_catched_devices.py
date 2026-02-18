#!/usr/bin/env python3
"""
Delete Catched Devices

Removes devices whose name starts with "Catched-" within the selected site,
and prunes connections pointing to removed devices.
"""

import json
import portalocker
import sys
from datetime import datetime


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config_path = sys.argv[1]
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"Failed to read config: {exc}"}))
        return

    site_name = (config.get("site_name") or "").strip()
    db_path = config.get("database_path")
    if not site_name:
        print(json.dumps({"status": "error", "message": "Site name is required"}))
        return
    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return

    try:
        with portalocker.Lock(db_path, "r", timeout=5, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"Failed to read database: {exc}"}))
        return

    devices = data.get("devices", [])
    removed_ids = set()
    kept_devices = []
    for device in devices:
        if device.get("site") != site_name:
            kept_devices.append(device)
            continue
        name = (device.get("name") or "").strip()
        if name.startswith("Catched-"):
            removed_ids.add(device.get("id"))
            continue
        kept_devices.append(device)

    cleaned = 0
    if removed_ids:
        now = datetime.now().isoformat()
        for device in kept_devices:
            if device.get("site") != site_name:
                continue
            connections = device.get("connections") or []
            new_conns = [c for c in connections if c.get("remote_device") not in removed_ids]
            if len(new_conns) != len(connections):
                device["connections"] = new_conns
                device["last_modified"] = now
                cleaned += 1

    data["devices"] = kept_devices
    data.setdefault("meta", {})["last_modified"] = datetime.now().isoformat()

    try:
        with portalocker.Lock(db_path, "w", timeout=5, encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"Failed to write database: {exc}"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "devices_removed": len(removed_ids),
        "connections_cleaned": cleaned
    }))


if __name__ == "__main__":
    main()
