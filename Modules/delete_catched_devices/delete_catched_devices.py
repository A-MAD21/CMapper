#!/usr/bin/env python3
"""
Delete Catched Devices

Removes devices whose name starts with "Catched-" within the selected site,
and prunes connections pointing to removed devices.
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


def add_block(data, device):
    block = {
        "id": device.get("id") or "",
        "site": device.get("site") or "",
        "ip": device.get("ip") or "",
        "mac": normalize_mac(device.get("mac") or ""),
        "name": device.get("name") or "",
        "blocked_at": datetime.now().isoformat(),
        "blocked_by": "delete_catched_devices",
    }
    meta = data.setdefault("meta", {})
    blocks = meta.setdefault("blocked_devices", [])
    blocks[:] = [item for item in blocks if not _same_block(item, block)]
    blocks.append(block)


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
    params = config.get("parameters", {})
    block_rediscovery = bool(params.get("block_rediscovery", False))
    db_path = config.get("database_path")
    if not site_name:
        print(json.dumps({"status": "error", "message": "Site name is required"}))
        return
    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return

    data = read_json_store(db_path, "devices")
    if data is None:
        print(json.dumps({"status": "error", "message": "Failed to read database"}))
        return

    devices = data.get("devices", [])
    removed_ids = set()
    kept_devices = []
    for device in devices:
        if device.get("site") != site_name:
            kept_devices.append(device)
            continue
        name = (device.get("name") or "").strip()
        if name.startswith("Catched-") and not device.get("locked"):
            removed_ids.add(device.get("id"))
            if block_rediscovery:
                add_block(data, device)
            continue
        kept_devices.append(device)

    cleaned = 0
    if removed_ids:
        now = datetime.now().isoformat()
        for device in kept_devices:
            if device.get("site") != site_name:
                continue
            if device.get("locked"):
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
        write_json_store(db_path, "devices", data, merge=False)
    except Exception:
        print(json.dumps({"status": "error", "message": "Failed to write database"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "devices_removed": len(removed_ids),
        "blocked_rediscovery": block_rediscovery,
        "connections_cleaned": cleaned
    }))


if __name__ == "__main__":
    main()
