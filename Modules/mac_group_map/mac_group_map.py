#!/usr/bin/env python3
"""Map a filtered group of inventory devices by tracing Cisco MAC tables."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
MAC_SEARCH_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "mac_table_search"))
for path in (SHARED_DIR, MAC_SEARCH_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlite_store import read_json_store
import mac_table_search as mac_search


def log(path: Optional[str], message: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")


def matches_group(device: Dict[str, Any], device_type: str, name_filter: str, match_mode: str) -> bool:
    actual_type = str(device.get("type") or "unknown").lower()
    if device_type != "all" and actual_type != device_type:
        return False
    if not name_filter:
        return True
    name = str(device.get("name") or "").lower()
    wanted = name_filter.lower()
    if match_mode == "contains":
        return wanted in name
    return name.startswith(wanted)


def trace_target(
    target: Dict[str, Any],
    start_ip: str,
    devices_data: Dict[str, Any],
    site: str,
    username: str,
    password: str,
    port: int,
    max_hops: int,
) -> Tuple[Optional[list[Dict[str, Any]]], str]:
    target_mac = mac_search.normalize_mac(str(target.get("mac") or ""))
    if not target_mac:
        return None, "missing MAC"
    mac_display = mac_search.cisco_mac(target_mac)
    current_ip = start_ip
    visited = set()
    trace: list[Dict[str, Any]] = []

    for _hop in range(max_hops):
        if current_ip in visited:
            return None, "loop detected"
        visited.add(current_ip)
        devices = [device for device in devices_data.get("devices", []) if device.get("site") == site]
        inventory_switch = mac_search.device_by_ip(devices, current_ip) or {}
        session = None
        try:
            session = mac_search.CiscoSession(current_ip, username, password, port)
            discovered_name = session.hostname()
            switch_name = (
                discovered_name
                if discovered_name and discovered_name != current_ip
                else inventory_switch.get("name") or current_ip
            )
            output = session.command(f"show mac address-table | include {mac_display}")
            found = mac_search.parse_mac_entry(output, mac_display)
            if not found:
                return None, f"MAC not found on {switch_name} ({current_ip})"
            switch_port = found["port"]
            status = session.command(f"show interfaces {switch_port} status")
            is_trunk = mac_search.port_is_trunk(status, switch_port)
            row = {
                "switch_name": switch_name,
                "switch_ip": current_ip,
                "port": switch_port,
                "vlan": found["vlan"],
                "entry_type": found["entry_type"],
                "trunk": is_trunk,
            }
            trace.append(row)
            if not is_trunk:
                return trace, ""

            cdp_output = session.command(f"show cdp neighbors interface {switch_port} detail")
            next_name, next_ip, next_port = mac_search.cdp_neighbor(cdp_output)
            if not next_ip:
                cdp_output = session.command(f"show cdp neighbor {switch_port} detail")
                next_name, next_ip, next_port = mac_search.cdp_neighbor(cdp_output)
            if not next_ip:
                next_name, next_ip, next_port = mac_search.database_neighbor(devices, inventory_switch, switch_port)
            if not next_ip:
                return None, f"no CDP neighbor after trunk port {switch_port}"
            row["next_switch_name"] = next_name
            row["next_switch_ip"] = next_ip
            row["next_port"] = next_port
            current_ip = next_ip
        except Exception as exc:
            message = str(exc)[:160]
            if "Authentication failed" in message:
                return None, "authentication failed"
            return None, message or "SSH query failed"
        finally:
            if session:
                session.close()
    return None, "maximum trunk hops reached"


def trace_from_known_switches(
    target: Dict[str, Any],
    start_ip: str,
    devices_data: Dict[str, Any],
    site: str,
    username: str,
    password: str,
    port: int,
    max_hops: int,
) -> Tuple[Optional[list[Dict[str, Any]]], str, int]:
    site_switches = [
        device for device in devices_data.get("devices", [])
        if device.get("site") == site
        and str(device.get("type") or "").lower() == "switch"
        and device.get("ip")
    ]
    candidates = [start_ip] + [
        str(device["ip"]) for device in site_switches
        if str(device["ip"]) != start_ip
    ]
    last_error = "MAC not found"
    attempted = 0
    for candidate_ip in candidates:
        attempted += 1
        trace, error = trace_target(
            target,
            candidate_ip,
            devices_data,
            site,
            username,
            password,
            port,
            max_hops,
        )
        if trace:
            return trace, "", attempted
        if error == "authentication failed":
            return None, error, attempted
        last_error = error
    return None, f"not found across {attempted} known switch(es); last result: {last_error}", attempted


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        config = json.load(handle)
    params = config.get("parameters") or {}
    site = str(config.get("site_name") or "")
    db_path = str(config.get("database_path") or "")
    log_file = config.get("log_file")
    data = read_json_store(db_path, "devices") or {}
    devices = [device for device in data.get("devices", []) if device.get("site") == site]

    device_type = str(params.get("device_type") or "").strip().lower()
    name_filter = str(params.get("name_prefix") or "").strip()
    name_match_mode = str(params.get("name_match_mode") or "starts_with").strip().lower()
    if name_match_mode not in ("starts_with", "contains"):
        name_match_mode = "starts_with"
    start = mac_search.device_by_id(devices, str(params.get("start_switch_id") or ""))
    start_ip = str(params.get("start_switch_ip") or "").strip() or str((start or {}).get("ip") or "")
    username = str(params.get("username") or "").strip()
    password = str(params.get("password") or "")
    port = int(params.get("ssh_port") or 22)
    max_hops = max(1, min(int(params.get("max_hops") or 10), 30))

    if not start_ip:
        print(json.dumps({"status": "error", "message": "Select a starting switch or enter its IP address"}))
        return

    matching = [
        device for device in devices
        if matches_group(device, device_type, name_filter, name_match_mode)
    ]
    with_mac = [device for device in matching if mac_search.normalize_mac(str(device.get("mac") or ""))]
    without_mac = len(matching) - len(with_mac)

    if log_file:
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(f"MAP GROUP - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    label = "all types" if device_type == "all" else device_type
    if name_filter:
        match_label = "contains" if name_match_mode == "contains" else "starts with"
        log(log_file, f"Filter: {label}, name {match_label} \"{name_filter}\"")
    else:
        log(log_file, f"Filter: {label}, all names")
    log(log_file, f"Matched: {len(matching)} device(s)")
    if without_mac:
        log(log_file, f"Skipped without MAC: {without_mac}")
    log(log_file, "")
    log(log_file, "Mapped devices:")

    mapped = []
    failed = []
    total_connections = 0
    for index, target in enumerate(with_mac, 1):
        trace, error, probed = trace_from_known_switches(
            target, start_ip, data, site, username, password, port, max_hops
        )
        if not trace:
            failed.append({"id": target.get("id"), "name": target.get("name"), "reason": error})
            log(log_file, f"{index}. {target.get('name') or target.get('mac')}: not mapped ({error})")
            if error == "authentication failed":
                log(log_file, "Stopped: SSH credentials were rejected.")
                break
            continue
        _record, links = mac_search.save_trace(
            db_path,
            data,
            site,
            target,
            mac_search.normalize_mac(str(target.get("mac") or "")),
            mac_search.cisco_mac(mac_search.normalize_mac(str(target.get("mac") or ""))),
            trace,
            write_notes=False,
            source_module="mac_group_map",
        )
        final = trace[-1]
        location = (
            f"{final['switch_name']} ({final['switch_ip']}) "
            f"Port {mac_search.display_port(final['port'])}"
        )
        probe_label = f" [searched {probed} switch(es)]" if probed > 1 else ""
        log(log_file, f"{index}. {target.get('name') or target.get('mac')} -> {location}{probe_label}")
        mapped.append({"id": target.get("id"), "name": target.get("name"), "location": location})
        total_connections += links

    log(log_file, "")
    log(log_file, f"Mapped: {len(mapped)} device(s)")
    log(log_file, f"Connections written: {total_connections}")
    if failed:
        log(log_file, f"Not mapped: {len(failed)} device(s)")

    print(json.dumps({
        "status": "success",
        "matched_devices": len(matching),
        "mapped_devices": len(mapped),
        "connections_written": total_connections,
        "skipped_without_mac": without_mac,
        "mapped": mapped,
        "failed": failed,
    }))


if __name__ == "__main__":
    main()
