#!/usr/bin/env python3
"""
Export devices to CSV based on flexible filters.
"""

from __future__ import annotations

import base64
import csv
import ipaddress
import json
import sys
import os
from datetime import datetime
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_db(path: str) -> Dict[str, Any]:
    return read_json_store(path, "devices") or {}


def parse_device_types(raw: str) -> List[str]:
    if not raw:
        return []
    return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]


EXPORT_COLUMNS = {
    "name": "Name",
    "ip": "IP",
    "mac": "MAC",
    "site": "Site",
    "type": "Type",
    "domain": "Domain",
    "domain_name": "Domain Name",
    "vendor": "Vendor",
    "platform": "Platform",
    "model": "Model",
    "os": "OS",
    "vlan": "VLAN",
    "status": "Status",
    "reachable": "Reachable",
    "discovered_by": "Discovered By",
    "discovered_at": "Discovered At",
    "last_seen": "Last Seen",
    "last_modified": "Last Modified",
    "parent_switch_name": "Parent Switch",
    "parent_switch_ip": "Parent Switch IP",
    "parent_switch_port": "Parent Switch Port",
    "notes": "Notes",
}

DEFAULT_COLUMNS = [
    "name",
    "ip",
    "mac",
    "site",
    "type",
    "domain",
    "vendor",
    "platform",
    "last_seen",
    "last_modified",
]


def parse_columns(raw: Any) -> List[str]:
    if isinstance(raw, list):
        candidates = [str(entry).strip() for entry in raw]
    elif isinstance(raw, str) and raw.strip():
        candidates = [entry.strip() for entry in raw.split(",")]
    else:
        candidates = DEFAULT_COLUMNS
    columns = []
    for column in candidates:
        if column in EXPORT_COLUMNS and column not in columns:
            columns.append(column)
    return columns or DEFAULT_COLUMNS


def parse_ip_range(raw: str) -> Optional[Iterable[str]]:
    if not raw:
        return None
    raw = raw.strip()
    if "-" in raw:
        start, end = raw.split("-", 1)
        try:
            start_ip = ipaddress.IPv4Address(start.strip())
            end_ip = ipaddress.IPv4Address(end.strip())
        except ipaddress.AddressValueError:
            return None
        if int(end_ip) < int(start_ip):
            start_ip, end_ip = end_ip, start_ip
        return (str(ipaddress.IPv4Address(value)) for value in range(int(start_ip), int(end_ip) + 1))
    if "/" in raw:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            return None
        return (str(ip) for ip in net.hosts())
    return None


def match_contains(value: str, needle: str) -> bool:
    if not needle:
        return True
    return needle.lower() in (value or "").lower()


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_config(sys.argv[1])
    params = config.get("parameters", {}) if isinstance(config, dict) else {}
    site_name = config.get("site_name") or params.get("site_name") or ""
    site_scope = (params.get("site_scope") or "current").lower()
    db_path = config.get("database_path")

    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return

    device_types = parse_device_types(params.get("device_types", ""))
    ip_range_raw = params.get("ip_range", "")
    name_contains = params.get("name_contains", "")
    vendor_contains = params.get("vendor_contains", "")
    platform_contains = params.get("platform_contains", "")
    filename = params.get("filename") or ""
    columns = parse_columns(params.get("columns"))
    selected_only = parse_bool(params.get("selected_only"))
    selected_device_ids = set(params.get("selected_device_ids") or [])
    selected_only_active = selected_only and bool(selected_device_ids)

    ip_range = parse_ip_range(ip_range_raw)
    if ip_range_raw and ip_range is None:
        print(json.dumps({"status": "error", "message": "Invalid IP range format"}))
        return

    data = load_db(db_path)
    if not data:
        print(json.dumps({"status": "error", "message": "Failed to read database"}))
        return

    devices = data.get("devices", [])
    ip_set = set(ip_range) if ip_range is not None else None

    rows = []
    for device in devices:
        if selected_only_active:
            if device.get("id") not in selected_device_ids:
                continue
        if site_scope != "all":
            if site_name and device.get("site") != site_name:
                continue
        if device_types:
            dtype = (device.get("type") or "").lower()
            if dtype not in device_types:
                continue
        ip = device.get("ip") or ""
        if ip_set is not None:
            if not ip or ip not in ip_set:
                continue
        if not match_contains(device.get("name", ""), name_contains):
            continue
        if not match_contains(device.get("vendor", ""), vendor_contains):
            continue
        if not match_contains(device.get("platform", ""), platform_contains):
            continue
        rows.append(device)

    output = StringIO()
    writer = csv.writer(output)
    header = [EXPORT_COLUMNS[column] for column in columns]
    writer.writerow(header)
    for device in rows:
        row = [device.get(column, "") for column in columns]
        writer.writerow(row)

    content = output.getvalue().encode("utf-8")
    encoded = base64.b64encode(content).decode("ascii")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not filename:
        if site_scope == "all":
            filename = f"devices_export_all_{stamp}.csv"
        else:
            safe_site = site_name.replace(" ", "_") if site_name else "site"
            filename = f"devices_export_{safe_site}_{stamp}.csv"

    print(json.dumps({
        "status": "success",
        "count": len(rows),
        "columns": columns,
        "export": {
            "filename": filename,
            "content_base64": encoded
        }
    }))


if __name__ == "__main__":
    main()
