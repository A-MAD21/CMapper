#!/usr/bin/env python3
"""
CSV Device Import

Imports devices from a CSV file using columns for name, IP, and MAC.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", ".."))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

from sqlite_store import read_json_store, write_json_store


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_mac(mac: str) -> str:
    mac = (mac or "").strip().replace("-", ":").replace(".", "")
    if len(mac) == 12:
        mac = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    return mac.upper()


def _normalize_header(value: str) -> str:
    text = (value or "").strip().lower()
    cleaned = []
    prev_space = False
    for ch in text:
        if ch.isalnum():
            cleaned.append(ch)
            prev_space = False
        else:
            if not prev_space:
                cleaned.append(" ")
                prev_space = True
    return "".join(cleaned).strip()


def _find_column(field_map: Dict[str, str], candidates: List[str]) -> Optional[str]:
    # Exact match first
    for name in candidates:
        key = name.lower().strip()
        if key in field_map:
            return field_map[key]
    return None


def _resolve_csv_path(path: str) -> str:
    if not path:
        return os.path.join(PROJECT_DIR, "share", "test1.csv")
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(PROJECT_DIR, path))


def _load_devices(db_path: str) -> Dict[str, Any]:
    return read_json_store(db_path, "devices") or {}


def _save_devices(db_path: str, data: Dict[str, Any]) -> None:
    write_json_store(db_path, "devices", data)


def _build_lookup(devices: List[Dict[str, Any]], site: str):
    by_ip: Dict[str, Dict[str, Any]] = {}
    by_mac: Dict[str, Dict[str, Any]] = {}
    for dev in devices:
        if dev.get("site") != site:
            continue
        ip = (dev.get("ip") or "").strip()
        if ip:
            by_ip[ip] = dev
        mac = normalize_mac(dev.get("mac") or "")
        if mac:
            by_mac[mac] = dev
    return by_ip, by_mac


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Missing config path"}))
        return

    config = load_config(sys.argv[1])
    params = config.get("parameters", {}) if isinstance(config, dict) else {}
    site_name = (config.get("site_name") or "").strip()
    if not site_name:
        print(json.dumps({"status": "error", "message": "Missing site_name"}))
        return

    db_path = config.get("database_path")
    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return

    csv_path = _resolve_csv_path(str(params.get("csv_path") or ""))
    if not os.path.exists(csv_path):
        print(json.dumps({"status": "error", "message": f"CSV not found: {csv_path}"}))
        return

    data = _load_devices(db_path)
    devices = data.get("devices", []) if isinstance(data, dict) else []

    by_ip, by_mac = _build_lookup(devices, site_name)

    created = 0
    updated = 0
    skipped = 0
    now = datetime.now().isoformat()

    with open(csv_path, "rb") as raw_f:
        raw = raw_f.read(4096)
        raw_f.seek(0)
        encoding = "utf-8"
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            encoding = "utf-16"
        text_stream = io.TextIOWrapper(raw_f, encoding=encoding, errors="ignore", newline="")
        sample = text_stream.read(2048)
        text_stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";"])
        except csv.Error:
            dialect = csv.excel_tab
        reader = csv.DictReader(text_stream, dialect=dialect)
        if not reader.fieldnames:
            print(json.dumps({"status": "error", "message": "CSV has no header"}))
            return

        field_map = {name.lower().strip(): name for name in reader.fieldnames}
        name_col = _find_column(field_map, ["name", "device name", "device_name", "hostname"])
        ip_col = _find_column(field_map, ["ip", "ip address", "address"])
        mac_col = _find_column(field_map, ["mac", "mac address", "mac-address"])

        # Fuzzy header match (handles extra spaces/BOM/odd delimiters)
        if not ip_col or not mac_col:
            for original in reader.fieldnames:
                norm = _normalize_header(original)
                if not ip_col and norm in ("ip", "ip address", "address"):
                    ip_col = original
                if not mac_col and (norm == "mac" or norm == "mac address"):
                    mac_col = original

        if not ip_col and not mac_col:
            print(json.dumps({"status": "error", "message": "CSV missing IP or MAC column"}))
            return

        for row in reader:
            ip = (row.get(ip_col) if ip_col else "") or ""
            ip = ip.strip()
            mac_raw = (row.get(mac_col) if mac_col else "") or ""
            mac = normalize_mac(mac_raw)
            name = (row.get(name_col) if name_col else "") or ""
            name = name.strip()

            if not ip and not mac:
                skipped += 1
                continue

            dev = None
            if ip and ip in by_ip:
                dev = by_ip[ip]
            elif mac and mac in by_mac:
                dev = by_mac[mac]

            if dev is None:
                dev = {
                    "id": f"dev_{uuid.uuid4().hex[:8]}",
                    "site": site_name,
                    "name": name or ip or mac,
                    "ip": ip,
                    "mac": mac,
                    "type": "unknown",
                    "discovered_by": "csv_device_import",
                    "discovered_at": now,
                    "last_modified": now,
                    "last_seen": now,
                    "status": "unknown"
                }
                devices.append(dev)
                if ip:
                    by_ip[ip] = dev
                if mac:
                    by_mac[mac] = dev
                created += 1
            else:
                if name:
                    dev["name"] = name
                if ip:
                    dev["ip"] = ip
                    by_ip[ip] = dev
                if mac:
                    dev["mac"] = mac
                    by_mac[mac] = dev
                dev["last_modified"] = now
                updated += 1

    if isinstance(data, dict):
        data["devices"] = devices
        data.setdefault("meta", {})["last_modified"] = now
        _save_devices(db_path, data)

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "csv_path": csv_path,
        "created": created,
        "updated": updated,
        "skipped": skipped
    }))


if __name__ == "__main__":
    main()
