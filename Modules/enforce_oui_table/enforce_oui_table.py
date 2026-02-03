#!/usr/bin/env python3
"""
Enforce OUI Table Module

Renames devices whose name is a MAC address using OUI vendor name.
Optionally sets device type from a vendor-to-type mapping file.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUI_FILE = os.path.join(os.path.dirname(BASE_DIR), "mikrotik_mac_discovery", "oui_ranges.txt")
TYPE_MAP_FILE = os.path.join(BASE_DIR, "oui_device_types.txt")

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
MAC_FLAT_RE = re.compile(r"^[0-9A-Fa-f]{12}$")


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def normalize_mac(mac: str) -> str:
    mac = mac.strip().replace("-", ":").replace(".", "")
    if MAC_FLAT_RE.match(mac):
        mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
    return mac.upper()


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def load_oui_ranges(path: str) -> List[Tuple[int, int, str]]:
    ranges: List[Tuple[int, int, str]] = []
    if not os.path.exists(path):
        return ranges
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line or "-" not in line:
                continue
            try:
                left, vendor = line.split("=", 1)
                start_str, end_str = left.split("-", 1)
                start = mac_to_int(normalize_mac(start_str))
                end = mac_to_int(normalize_mac(end_str))
                ranges.append((start, end, vendor.strip()))
            except Exception:
                continue
    return ranges


def load_type_map(path: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not os.path.exists(path):
        return mapping
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            vendor, dtype = line.split("=", 1)
            vendor = vendor.strip().lower()
            dtype = dtype.strip().lower()
            if vendor and dtype:
                mapping[vendor] = dtype
    return mapping


def lookup_vendor(mac: str, ranges: List[Tuple[int, int, str]]) -> Optional[str]:
    try:
        mac_int = mac_to_int(normalize_mac(mac))
    except Exception:
        return None
    for start, end, vendor in ranges:
        if start <= mac_int <= end:
            return vendor
    return None


def is_mac_name(name: str) -> bool:
    if not name:
        return False
    name = name.strip()
    if MAC_RE.match(name):
        return True
    return bool(MAC_FLAT_RE.match(name.replace(":", "").replace("-", "")))


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config_path = sys.argv[1]
    config = load_json(config_path)
    db_path = config.get("database_path")
    site_name = config.get("site_name") or ""

    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return
    if not site_name:
        print(json.dumps({"status": "error", "message": "Missing site_name"}))
        return

    oui_ranges = load_oui_ranges(OUI_FILE)
    known_labels = {label.lower() for _, _, label in oui_ranges if label}
    type_map = load_type_map(TYPE_MAP_FILE)

    try:
        data = load_json(db_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to read database: {e}"}))
        return

    updated = 0
    now = datetime.now().isoformat()
    devices = data.get("devices", [])
    for device in devices:
        if device.get("site") != site_name:
            continue
        mac = device.get("mac")
        name = device.get("name") or ""
        if not mac:
            continue
        vendor = lookup_vendor(mac, oui_ranges)
        if not vendor:
            continue

        changed = False
        old_vendor = (device.get("vendor") or "").strip()
        old_label = (device.get("oui_label") or "").strip()
        name_norm = name.strip().lower()
        if is_mac_name(name) or name_norm in {old_vendor.lower(), old_label.lower()} or name_norm in known_labels:
            device["name"] = vendor
            changed = True
        if device.get("vendor") != vendor:
            device["vendor"] = vendor
            device["oui_label"] = vendor
            changed = True

        mapped_type = type_map.get(vendor.lower())
        current_type = (device.get("type") or "").lower()
        if mapped_type and (current_type in ("", "unknown") or old_vendor.lower() != vendor.lower()):
            device["type"] = mapped_type
            changed = True

        if changed:
            device["last_modified"] = now
            updated += 1

    data.setdefault("meta", {})["last_modified"] = now
    try:
        save_json(db_path, data)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to write database: {e}"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "updated": updated
    }))


if __name__ == "__main__":
    main()
