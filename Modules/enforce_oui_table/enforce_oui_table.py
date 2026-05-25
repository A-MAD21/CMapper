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
SHARED_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store, write_json_store

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
MAC_FLAT_RE = re.compile(r"^[0-9A-Fa-f]{12}$")


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_mac(mac: str) -> str:
    mac = mac.strip().replace("-", ":").replace(".", "")
    if MAC_FLAT_RE.match(mac):
        mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
    return mac.upper()


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def load_oui_ranges(path: str) -> List[Tuple[int, int, str, str]]:
    ranges: List[Tuple[int, int, str, str]] = []
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
                vendor_label = vendor.strip()
                dtype = ""
                if "," in vendor_label:
                    parts = [p.strip() for p in vendor_label.split(",") if p.strip()]
                    vendor_label = parts[0] if parts else vendor_label
                    for part in parts[1:]:
                        if part.lower().startswith("device_type="):
                            dtype = part.split("=", 1)[1].strip().lower()
                start = mac_to_int(normalize_mac(start_str))
                end = mac_to_int(normalize_mac(end_str))
                ranges.append((start, end, vendor_label, dtype))
            except Exception:
                continue
    return ranges


def lookup_vendor(mac: str, ranges: List[Tuple[int, int, str, str]]) -> Tuple[Optional[str], Optional[str]]:
    try:
        mac_int = mac_to_int(normalize_mac(mac))
    except Exception:
        return None, None
    for start, end, vendor, dtype in ranges:
        if start <= mac_int <= end:
            return vendor, dtype or None
    return None, None


def is_mac_name(name: str) -> bool:
    if not name:
        return False
    name = name.strip()
    if MAC_RE.match(name):
        return True
    return bool(MAC_FLAT_RE.match(name.replace(":", "").replace("-", "")))


def _normalize_site_key(name: str) -> str:
    if not isinstance(name, str):
        return ""
    cleaned = " ".join(name.strip().split())
    while cleaned.endswith("."):
        cleaned = cleaned[:-1].rstrip()
    return cleaned


def _resolve_site_name(requested_name: str, sites):
    if not isinstance(requested_name, str):
        return requested_name
    if requested_name in sites:
        return requested_name
    target = _normalize_site_key(requested_name)
    for name in sites:
        if _normalize_site_key(name) == target:
            return name
    return requested_name


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config_path = sys.argv[1]
    config = load_json(config_path)
    db_path = config.get("database_path")
    site_name = config.get("site_name") or ""
    params = config.get("parameters", {}) if isinstance(config, dict) else {}
    override_existing_type = bool(params.get("override_existing_type", False))

    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return
    if not site_name:
        print(json.dumps({"status": "error", "message": "Missing site_name"}))
        return

    oui_ranges = load_oui_ranges(OUI_FILE)
    known_labels = {label.lower() for _, _, label, _ in oui_ranges if label}

    data = read_json_store(db_path, "devices")
    if data is None:
        print(json.dumps({"status": "error", "message": "Failed to read database"}))
        return

    sites = [s.get("name") for s in data.get("sites", []) if isinstance(s, dict) and s.get("name")]
    resolved_site = _resolve_site_name(site_name, sites)

    updated = 0
    now = datetime.now().isoformat()
    devices = data.get("devices", [])
    for device in devices:
        if device.get("site") != resolved_site:
            continue
        if device.get("locked"):
            continue
        mac = device.get("mac")
        name = device.get("name") or ""
        if not mac:
            continue
        vendor, mapped_type = lookup_vendor(mac, oui_ranges)
        if not vendor:
            continue

        changed = False
        current_type = (device.get("type") or "").lower()
        if mapped_type and (override_existing_type or current_type in ("", "unknown")) and current_type != mapped_type:
            device["type"] = mapped_type
            changed = True

        if changed:
            device["last_modified"] = now
            updated += 1

    data.setdefault("meta", {})["last_modified"] = now
    try:
        write_json_store(db_path, "devices", data)
    except Exception:
        print(json.dumps({"status": "error", "message": "Failed to write database"}))
        return

    print(json.dumps({
        "status": "success",
        "site": resolved_site,
        "updated": updated,
        "override_existing_type": override_existing_type
    }))


if __name__ == "__main__":
    main()
