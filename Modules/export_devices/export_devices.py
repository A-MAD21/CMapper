#!/usr/bin/env python3
"""
Export devices to CSV based on flexible filters.
"""

from __future__ import annotations

import base64
import csv
import ipaddress
import json
import portalocker
import sys
from datetime import datetime
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional


def load_json(path: str) -> Dict[str, Any]:
    with portalocker.Lock(path, "r", timeout=5, encoding="utf-8") as f:
        return json.load(f)


def parse_device_types(raw: str) -> List[str]:
    if not raw:
        return []
    return [entry.strip().lower() for entry in raw.split(",") if entry.strip()]


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


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_json(sys.argv[1])
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

    ip_range = parse_ip_range(ip_range_raw)
    if ip_range_raw and ip_range is None:
        print(json.dumps({"status": "error", "message": "Invalid IP range format"}))
        return

    try:
        data = load_json(db_path)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"Failed to read database: {exc}"}))
        return

    devices = data.get("devices", [])
    ip_set = set(ip_range) if ip_range is not None else None

    rows = []
    for device in devices:
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
    include_site = site_scope == "all"
    header = ["name", "ip", "mac", "type", "vendor", "platform"]
    if include_site:
        header.append("site")
    header.extend(["last_seen", "last_modified"])
    writer.writerow(header)
    for device in rows:
        row = [
            device.get("name", ""),
            device.get("ip", ""),
            device.get("mac", ""),
            device.get("type", ""),
            device.get("vendor", ""),
            device.get("platform", "")
        ]
        if include_site:
            row.append(device.get("site", ""))
        row.extend([
            device.get("last_seen", ""),
            device.get("last_modified", "")
        ])
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
        "export": {
            "filename": filename,
            "content_base64": encoded
        }
    }))


if __name__ == "__main__":
    main()
