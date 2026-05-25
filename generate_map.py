#!/usr/bin/env python3
"""
Text Map Generator

Generates a human-readable, hierarchical topology text map from the device
database.
"""

from __future__ import annotations

import json
import os
import glob
import ipaddress
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional


def _load_database(db_path: str) -> Dict[str, Any]:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT json FROM json_store WHERE name = 'devices'")
    row = cur.fetchone()
    conn.close()
    if not row:
        raise FileNotFoundError("No devices data in sqlite database")
    return json.loads(row[0])


def _build_graph(devices: List[Dict[str, Any]]) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, List[Tuple[str, str, str, str]]],
    int,
    List[Dict[str, Any]]
]:
    """
    Build an adjacency list based on per-device 'connections'.

    Returns:
      - id_map: device_id -> device dict
      - adj: device_id -> list of (neighbor_id, local_intf, remote_intf, protocol)
      - conn_count: number of connection entries considered
    """
    id_map = {d.get("id"): d for d in devices if d.get("id")}
    adj: Dict[str, List[Tuple[str, str, str, str]]] = defaultdict(list)
    conn_count = 0
    unreachable: List[Dict[str, Any]] = []

    for d in devices:
        did = d.get("id")
        if not did:
            continue
        for c in d.get("connections") or []:
            remote_id = c.get("remote_device")
            if not remote_id or remote_id not in id_map:
                unreachable.append({
                    "source_id": did,
                    "source_name": d.get("name") or did,
                    "source_ip": d.get("ip") or "?",
                    "remote_name": c.get("remote_name") or c.get("remote_lookup") or c.get("remote_device_id") or "Unknown",
                    "remote_ip": c.get("remote_ip") or c.get("remote_address") or "",
                    "local_interface": c.get("local_interface") or "",
                    "remote_interface": c.get("remote_interface") or "",
                })
                continue
            conn_count += 1
            adj[did].append(
                (
                    remote_id,
                    c.get("local_interface") or "",
                    c.get("remote_interface") or "",
                    c.get("protocol") or "",
                )
            )
    return id_map, adj, conn_count, unreachable


def _pick_root_device(site: Dict[str, Any], devices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    root_ip = site.get("root_ip")
    if root_ip:
        for d in devices:
            if d.get("ip") == root_ip:
                return d
    return devices[0] if devices else None


def _format_device_line(device: Dict[str, Any]) -> str:
    name = device.get("name") or "Unknown"
    ip = device.get("ip") or "?"
    platform = device.get("platform") or device.get("model") or ""
    dtype = device.get("type") or ""
    extras = " | ".join([x for x in [dtype, platform] if x])
    return f"{name} ({ip})" + (f" | {extras}" if extras else "")


def _type_rank(device_type: Any) -> int:
    value = str(device_type or "unknown").strip().lower()
    priority = {
        "router": 0,
        "switch": 1,
        "server": 2,
        "ap": 3,
        "access point": 3,
        "access_point": 3,
        "nvr": 4,
        "camera": 4,
        "other": 5,
        "finger": 5,
        "pc": 6,
        "host": 6,
        "workstation": 6,
        "pda": 7,
    }
    return priority.get(value, 8)


def _ip_sort_value(value: Any) -> Tuple[int, Any]:
    try:
        return (0, int(ipaddress.ip_address(str(value or ""))))
    except ValueError:
        return (1, str(value or "").lower())


def _device_sort_key(device: Dict[str, Any], root_id: str = "") -> Tuple[Any, ...]:
    return (
        _type_rank(device.get("type")),
        0 if device.get("id") == root_id else 1,
        _ip_sort_value(device.get("ip")),
        str(device.get("name") or "").lower(),
    )


def _device_fields(device: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(device.get("name") or device.get("id") or "Unknown"),
        str(device.get("ip") or "-"),
        str(device.get("type") or "unknown"),
    )


def _short_port(value: Any) -> str:
    port = str(value or "").strip()
    if not port:
        return "?"
    return re.sub(
        r"^(?:TenGigabitEthernet|FortyGigabitEthernet|TwentyFiveGigE|GigabitEthernet|FastEthernet|Ethernet|Te|Gi|Fa|Eth)",
        "",
        port,
        flags=re.IGNORECASE,
    ) or port


def generate_map_from_database(site_name: str | None = None) -> Dict[str, Any]:
    """
    Generate a text map from the device database.

    Output is written to ./generated_maps/<site>_text_map_<timestamp>.txt
    and a dict is returned for the API layer.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "cmapp.sqlite3")
    out_dir = os.path.join(base_dir, "generated_maps")
    os.makedirs(out_dir, exist_ok=True)

    def _prune_old_maps(out_dir: str, safe_site: str, keep: int = 10) -> None:
        patterns = [
            os.path.join(out_dir, f"{safe_site}_text_map_*.txt"),
            os.path.join(out_dir, f"{safe_site}_map_*.html"),
        ]
        files: List[str] = []
        for pattern in patterns:
            files.extend(glob.glob(pattern))
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old_path in files[keep:]:
            try:
                os.remove(old_path)
            except OSError:
                pass

    try:
        data = _load_database(db_path)
        sites = data.get("sites", [])
        if not sites:
            return {"status": "error", "message": "No sites found in database"}

        # Choose site
        selected_site: Optional[Dict[str, Any]] = None
        if site_name:
            for s in sites:
                if s.get("name") == site_name:
                    selected_site = s
                    break
            if not selected_site:
                return {"status": "error", "message": f"Site '{site_name}' not found"}
        else:
            selected_site = sites[0]
            site_name = selected_site.get("name") or "default"

        # Devices for site
        all_devices = data.get("devices", [])
        devices = [d for d in all_devices if d.get("site") == site_name]
        if not devices:
            return {"status": "error", "message": f"No devices found for site '{site_name}'"}

        id_map, adj, conn_count, unreachable = _build_graph(devices)

        root_device = _pick_root_device(selected_site, devices)
        if not root_device or not root_device.get("id"):
            return {"status": "error", "message": "Could not determine a root device for the map"}

        root_id = root_device["id"]

        # Connections are documentation links: display them beneath both
        # participating devices even when discovery stored only one direction.
        connected_ids: Dict[str, set[str]] = defaultdict(set)
        child_ports: Dict[str, Dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        link_pairs = set()
        for did, links in adj.items():
            for nbr_id, local_intf, remote_intf, _proto in links:
                if did == nbr_id:
                    continue
                connected_ids[did].add(nbr_id)
                connected_ids[nbr_id].add(did)
                if remote_intf:
                    child_ports[did][nbr_id].add(_short_port(remote_intf))
                if local_intf:
                    child_ports[nbr_id][did].add(_short_port(local_intf))
                link_pairs.add(tuple(sorted((did, nbr_id))))

        devices_sorted = sorted(devices, key=lambda d: _device_sort_key(d, root_id))
        name_width = min(46, max(16, max(len(_device_fields(d)[0]) for d in devices_sorted)))
        ip_width = max(10, max(len(_device_fields(d)[1]) for d in devices_sorted))
        type_width = max(8, max(len(_device_fields(d)[2]) for d in devices_sorted))

        def table_line(number: str, device: Dict[str, Any], indent: str = "") -> str:
            name, ip, dtype = _device_fields(device)
            return (
                f"{indent}{number:<7} "
                f"{name[:name_width]:<{name_width}}  "
                f"{ip:<{ip_width}}  "
                f"{dtype:<{type_width}}"
            )

        def connection_line(number: str, device: Dict[str, Any], ports: set[str]) -> str:
            name, ip, dtype = _device_fields(device)
            port_text = ", ".join(sorted(ports)) if ports else "?"
            return f"{number:<8}\t{name}({ip}-{dtype}-{port_text})"

        out_lines: List[str] = []
        out_lines.append("NETWORK CONNECTION DOCUMENT")
        out_lines.append("=" * 78)
        out_lines.append(f"Site: {site_name}")
        out_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        out_lines.append(f"Devices: {len(devices)}    Connections: {len(link_pairs)}")
        out_lines.append("")
        out_lines.append("Order: Routers, Switches, Servers, APs, NVRs, Others, PCs, PDAs, Remaining")
        out_lines.append("")
        out_lines.append(f"{'No.':<8}{'Name':<{name_width}}  {'IP':<{ip_width}}  {'Type':<{type_width}}")
        out_lines.append("-" * (10 + name_width + ip_width + type_width + 4))

        for number, dev in enumerate(devices_sorted, start=1):
            did = dev.get("id")
            if not did:
                continue
            out_lines.append(table_line(f"{number}.", dev))
            neighbors = [
                id_map[nbr_id] for nbr_id in connected_ids.get(did, set())
                if nbr_id in id_map
            ]
            neighbors.sort(key=lambda d: _device_sort_key(d, root_id))
            if neighbors:
                for child_number, neighbor in enumerate(neighbors, start=1):
                    out_lines.append(
                        connection_line(
                            f"{number}.{child_number}",
                            neighbor,
                            child_ports.get(did, {}).get(neighbor.get("id"), set()),
                        )
                    )
            else:
                out_lines.append(f"{'':<8}\t(no documented connections)")
            out_lines.append("")

        out_lines.append("UNRESOLVED CONNECTIONS")
        out_lines.append("-" * 78)
        if unreachable:
            for entry in unreachable:
                remote_name = entry.get("remote_name") or "Unknown"
                remote_ip = entry.get("remote_ip") or "?"
                local = entry.get("local_interface") or "?"
                remote = entry.get("remote_interface") or "?"
                out_lines.append(
                    f"  Source {entry.get('source_ip')}: {remote_name} ({remote_ip}) via {local} to {remote}"
                )
        else:
            out_lines.append("  None")

        out_lines.append("")
        out_lines.append("=" * 78)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_site = "".join(ch for ch in (site_name or "site") if ch.isalnum() or ch in ("-", "_")).strip() or "site"
        filename = f"{safe_site}_text_map_{timestamp}.txt"
        out_path = os.path.join(out_dir, filename)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")

        _prune_old_maps(out_dir, safe_site)
        return {
            "status": "success",
            "message": "Text map generated successfully",
            "map_file": filename,
            "map_url": f"/generated_maps/{filename}",
            "site_name": site_name,
            "device_count": len(devices),
            "connection_count": len(link_pairs),
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import sys

    site = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(generate_map_from_database(site), indent=2))
