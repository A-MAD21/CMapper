#!/usr/bin/env python3
"""
Domain Lookup

Pings device names and stores the DNS domain suffix when the OS resolver expands
the short name to an FQDN, e.g. JBC001P6071 -> JBC001P6071.gig.holdings.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store, write_json_store


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_types(raw: Any) -> List[str]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        values = raw.split(",")
    else:
        values = []
    result = []
    for value in values:
        item = str(value or "").strip().lower()
        if item and item not in result:
            result.append(item)
    return result


def parse_suffixes(raw: Any) -> List[str]:
    if isinstance(raw, list):
        values = raw
    else:
        values = re.split(r"[\n,]+", str(raw or ""))
    suffixes = []
    for value in values:
        suffix = normalize_name(str(value or "")).lower()
        if suffix and suffix not in suffixes:
            suffixes.append(suffix)
    return suffixes


def normalize_name(name: str) -> str:
    return (name or "").strip().strip(".")


def lookup_base_name(name: str) -> str:
    clean = normalize_name(name)
    if "/" in clean:
        clean = clean.split("/", 1)[0].strip()
    return normalize_name(clean)


def is_mac_like(value: str) -> bool:
    clean = normalize_name(value).replace("-", ":")
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", clean):
        return True
    return bool(re.fullmatch(r"[0-9A-Fa-f]{12}", clean.replace(".", "")))


def candidate_names(name: str, strip_catched: bool, suffixes: List[str]) -> List[Tuple[str, str]]:
    clean = lookup_base_name(name)
    if not clean:
        return []
    base_names = [clean]
    if strip_catched and clean.lower().startswith("catched-"):
        stripped = clean[len("Catched-"):].strip()
        if stripped and stripped not in base_names:
            base_names.append(stripped)
    candidates: List[Tuple[str, str]] = []
    seen = set()
    for base in base_names:
        query_values = [base]
        if "." not in base:
            query_values.extend(f"{base}.{suffix}" for suffix in suffixes)
        for query in query_values:
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append((base, query))
    return candidates


def extract_ping_identity(output: str) -> Tuple[Optional[str], Optional[str]]:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        linux_match = re.match(r"^PING\s+([^\s(]+)\s+\(([^)]+)\)", line, re.IGNORECASE)
        if linux_match:
            return linux_match.group(1).strip().strip("."), linux_match.group(2).strip()
        windows_match = re.match(r"^Pinging\s+([^\s\[]+)\s+\[([^\]]+)\]", line, re.IGNORECASE)
        if windows_match:
            return windows_match.group(1).strip().strip("."), windows_match.group(2).strip()
    return None, None


def domain_from_fqdn(base_name: str, fqdn: str) -> Optional[str]:
    query_clean = normalize_name(base_name).lower()
    fqdn_clean = normalize_name(fqdn)
    fqdn_lower = fqdn_clean.lower()
    prefix = f"{query_clean}."
    if not query_clean or not fqdn_clean or not fqdn_lower.startswith(prefix):
        return None
    domain = fqdn_clean[len(query_clean) + 1:].strip(".")
    return domain or None


def ping_name(name: str, count: int, timeout_seconds: int) -> Tuple[Optional[str], Optional[str], str]:
    cmd = ["ping", "-c", str(count), "-W", str(timeout_seconds), name]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(timeout_seconds * count + 2, timeout_seconds + 3)
        )
    except Exception as exc:
        return None, None, str(exc)
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    fqdn, resolved_ip = extract_ping_identity(output)
    return fqdn, resolved_ip, output


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_config(sys.argv[1])
    params = config.get("parameters", {}) if isinstance(config, dict) else {}
    site_name = (config.get("site_name") or params.get("site_name") or "").strip()
    db_path = config.get("database_path")

    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return
    if not site_name:
        print(json.dumps({"status": "error", "message": "Missing site_name"}))
        return

    device_types = parse_types(params.get("device_types"))
    if not device_types:
        print(json.dumps({"status": "error", "message": "Select at least one device type"}))
        return

    try:
        ping_count = max(1, min(4, int(params.get("ping_count") or 1)))
    except Exception:
        ping_count = 1
    try:
        timeout_seconds = max(1, min(10, int(params.get("timeout_seconds") or 2)))
    except Exception:
        timeout_seconds = 2
    strip_catched = bool(params.get("strip_catched_prefix", True))
    suffixes = parse_suffixes(params.get("domain_suffixes", "okco.ir\ngig.holdings"))
    skip_mac_names = bool(params.get("skip_mac_names", True))

    data = read_json_store(db_path, "devices")
    if data is None:
        print(json.dumps({"status": "error", "message": "Failed to read database"}))
        return

    include_all = "all" in device_types
    matched = []
    for device in data.get("devices", []):
        if device.get("site") != site_name:
            continue
        if device.get("locked"):
            continue
        dtype = (device.get("type") or "unknown").strip().lower()
        if not include_all and dtype not in device_types:
            continue
        if not normalize_name(device.get("name") or ""):
            continue
        if skip_mac_names and is_mac_like(device.get("name") or ""):
            continue
        matched.append(device)

    updated = 0
    resolved = []
    failed = []
    now = datetime.now().isoformat()

    for device in matched:
        name = normalize_name(device.get("name") or "")
        found = False
        last_output = ""
        for base_name, query in candidate_names(name, strip_catched, suffixes):
            fqdn, resolved_ip, output = ping_name(query, ping_count, timeout_seconds)
            last_output = output
            domain = domain_from_fqdn(base_name, fqdn or "")
            if not domain:
                continue
            device["domain"] = domain
            device["domain_name"] = fqdn
            device["domain_lookup_name"] = base_name
            device["domain_query"] = query
            device["domain_resolved_ip"] = resolved_ip or ""
            device["domain_last_checked"] = now
            device["last_modified"] = now
            updated += 1
            resolved.append({
                "id": device.get("id"),
                "name": name,
                "query": query,
                "fqdn": fqdn,
                "domain": domain,
                "resolved_ip": resolved_ip
            })
            found = True
            break
        if not found:
            device["domain_last_checked"] = now
            failed.append({
                "id": device.get("id"),
                "name": name,
                "reason": "not_resolved",
                "output": last_output[:300]
            })

    data.setdefault("meta", {})["last_modified"] = now
    try:
        write_json_store(db_path, "devices", data)
    except Exception:
        print(json.dumps({"status": "error", "message": "Failed to write database"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "matched": len(matched),
        "updated": updated,
        "resolved": resolved,
        "failed": failed[:20]
    }))


if __name__ == "__main__":
    main()
