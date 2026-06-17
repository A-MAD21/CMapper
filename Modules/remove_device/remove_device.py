#!/usr/bin/env python3
"""
Remove Device - module for deleting a device and handling dependencies.
"""

import json
import sys
import os
from datetime import datetime

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store, write_json_store


def normalize_mac(value: str) -> str:
    mac = (value or "").strip().replace("-", ":").replace(".", "")
    if len(mac) == 12:
        mac = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    return mac.upper()


def _same_block(left, right):
    if left.get("site") != right.get("site"):
        return False
    left_mac = normalize_mac(left.get("mac") or "")
    right_mac = normalize_mac(right.get("mac") or "")
    if left_mac and right_mac and left_mac == right_mac:
        return True
    if left.get("id") and right.get("id") and left.get("id") == right.get("id"):
        return True
    if not left_mac and not right_mac and left.get("ip") and left.get("ip") == right.get("ip"):
        return True
    return False


def add_block(database, device):
    block = {
        "id": device.get("id") or "",
        "site": device.get("site") or "",
        "ip": device.get("ip") or "",
        "mac": normalize_mac(device.get("mac") or ""),
        "name": device.get("name") or "",
        "blocked_at": datetime.now().isoformat(),
        "blocked_by": "remove_device",
    }
    meta = database.setdefault("meta", {})
    blocks = meta.setdefault("blocked_devices", [])
    blocks[:] = [item for item in blocks if not _same_block(item, block)]
    blocks.append(block)


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
    block_rediscovery = bool(params.get("block_rediscovery", False))
    db_path = config.get("database_path")

    if not site_name:
        print(json.dumps({"error": "Site name is required", "status": "failed"}))
        sys.exit(1)
    if not device_id:
        print(json.dumps({"error": "Device id is required", "status": "failed"}))
        sys.exit(1)

    database = read_json_store(db_path, "devices")
    if database is None:
        print(json.dumps({"error": "Failed to read database", "status": "failed"}))
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
            if block_rediscovery:
                add_block(database, device)
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
        write_json_store(db_path, "devices", database, merge=False)
    except Exception:
        print(json.dumps({"error": "Failed to write database", "status": "failed"}))
        sys.exit(1)

    result = {
        "status": "success",
        "message": "Device removal completed",
        "data": {
            "device_removed": True,
            "devices_removed": len(remove_ids),
            "blocked_rediscovery": block_rediscovery,
            "removed_ids": sorted(remove_ids),
            "dependents_found": len(dependents),
            "dependents_removed": 0 if keep_dependents else len(dependents),
        },
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
