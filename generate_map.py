#!/usr/bin/env python3
"""
Text Map Generator

Generates a human-readable, hierarchical topology text map from the device
database.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional


def _load_database(db_path: str, fallback_path: str | None = None) -> Dict[str, Any]:
    """Load database JSON from primary path, optionally falling back to a secondary path."""
    target = db_path if os.path.exists(db_path) else fallback_path
    if not target:
        raise FileNotFoundError(f"Database not found at {db_path}")
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_graph(devices: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Tuple[str, str, str, str]]], int]:
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

    for d in devices:
        did = d.get("id")
        if not did:
            continue
        for c in d.get("connections") or []:
            remote_id = c.get("remote_device")
            if not remote_id or remote_id not in id_map:
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
    return id_map, adj, conn_count


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


def generate_map_from_database(site_name: str | None = None) -> Dict[str, Any]:
    """
    Generate a text map from the device database.

    Output is written to ./generated_maps/<site>_text_map_<timestamp>.txt
    and a dict is returned for the API layer.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "devices.db")
    fallback_db_path = os.path.join(base_dir, "database.json")
    out_dir = os.path.join(base_dir, "generated_maps")
    os.makedirs(out_dir, exist_ok=True)

    try:
        data = _load_database(db_path, fallback_db_path)
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

        id_map, adj, conn_count = _build_graph(devices)

        root_device = _pick_root_device(selected_site, devices)
        if not root_device or not root_device.get("id"):
            return {"status": "error", "message": "Could not determine a root device for the map"}

        # BFS tree for a readable hierarchy
        root_id = root_device["id"]
        visited = set([root_id])
        parent: Dict[str, str] = {}
        parent_edge: Dict[str, tuple[str, str, str]] = {}  # child -> (local, remote, proto)

        q = deque([root_id])
        while q:
            cur = q.popleft()
            for nbr, local_intf, remote_intf, proto in adj.get(cur, []):
                if nbr in visited:
                    continue
                visited.add(nbr)
                parent[nbr] = cur
                parent_edge[nbr] = (local_intf, remote_intf, proto)
                q.append(nbr)

        # Build children list for printing
        children: Dict[str, List[str]] = defaultdict(list)
        for child, par in parent.items():
            children[par].append(child)

        def write_node(node_id: str, indent: str, out_lines: List[str]) -> None:
            out_lines.append(f"{indent}- {_format_device_line(id_map[node_id])}")

            # Sort children by name for stability
            kids = sorted(children.get(node_id, []), key=lambda x: (id_map[x].get("name") or "", id_map[x].get("ip") or ""))
            for kid in kids:
                local_intf, remote_intf, proto = parent_edge.get(kid, ("", "", ""))
                link = " ".join([x for x in [proto, local_intf, ("-> " + remote_intf) if remote_intf else ""] if x]).strip()
                if link:
                    out_lines.append(f"{indent}  link: {link}")
                write_node(kid, indent + "  ", out_lines)

        out_lines: List[str] = []
        out_lines.append(f"Site: {site_name}")
        out_lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
        out_lines.append(f"Devices: {len(devices)} | Connection entries: {conn_count}")
        out_lines.append("")
        write_node(root_id, "", out_lines)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_site = "".join(ch for ch in (site_name or "site") if ch.isalnum() or ch in ("-", "_")).strip() or "site"
        filename = f"{safe_site}_text_map_{timestamp}.txt"
        out_path = os.path.join(out_dir, filename)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")

        return {
            "status": "success",
            "message": "Text map generated successfully",
            "map_file": filename,
            "map_url": f"/generated_maps/{filename}",
            "site_name": site_name,
            "device_count": len(devices),
            "connection_count": conn_count,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import sys

    site = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(generate_map_from_database(site), indent=2))
