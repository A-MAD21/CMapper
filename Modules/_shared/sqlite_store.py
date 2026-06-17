import json
import os
import sqlite3
from datetime import datetime


def _get_conn(db_path: str) -> sqlite3.Connection:
    if not db_path:
        raise ValueError("db_path is required")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS json_store ("
        "name TEXT PRIMARY KEY,"
        "json TEXT NOT NULL,"
        "updated_at TEXT NOT NULL)"
    )
    return conn


def read_json_store(db_path: str, name: str, default=None):
    try:
        conn = _get_conn(db_path)
        cur = conn.execute("SELECT json FROM json_store WHERE name = ?", (name,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def _parse_ts(value):
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _newer_ts(*values):
    parsed = [(value, _parse_ts(value)) for value in values if value]
    parsed = [(value, ts) for value, ts in parsed if ts is not None]
    if not parsed:
        return next((value for value in values if value), "")
    return max(parsed, key=lambda item: item[1])[0]


def _device_merge_ts(device):
    if not isinstance(device, dict):
        return None
    return _parse_ts(device.get("last_modified")) or _parse_ts(device.get("last_seen")) or _parse_ts(device.get("discovered_at"))


def _norm_mac(value):
    return str(value or "").strip().replace("-", ":").lower()


def _conn_key(conn):
    if not isinstance(conn, dict):
        return None
    if conn.get("id"):
        return ("id", conn.get("id"))
    return (
        conn.get("protocol") or "",
        conn.get("remote_device") or "",
        conn.get("local_interface") or "",
        conn.get("remote_interface") or "",
    )


def _merge_connections(current_connections, incoming_connections):
    merged = []
    by_key = {}

    def add_or_merge(conn):
        if not isinstance(conn, dict):
            return
        key = _conn_key(conn)
        if key is None:
            merged.append(conn)
            return
        existing = by_key.get(key)
        if existing is None:
            clone = dict(conn)
            by_key[key] = clone
            merged.append(clone)
            return
        existing_ts = _parse_ts(existing.get("discovered_at"))
        incoming_ts = _parse_ts(conn.get("discovered_at"))
        if incoming_ts and (not existing_ts or incoming_ts >= existing_ts):
            existing.update(conn)
        else:
            for field, value in conn.items():
                if existing.get(field) in ("", None, [], {}) and value not in ("", None, [], {}):
                    existing[field] = value

    for conn in current_connections or []:
        add_or_merge(conn)
    for conn in incoming_connections or []:
        add_or_merge(conn)
    return merged


def _merge_string_lists(current_values, incoming_values):
    result = []
    seen = set()
    for value in list(current_values or []) + list(incoming_values or []):
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _device_key(device):
    if not isinstance(device, dict):
        return None
    site = device.get("site") or ""
    mac = (device.get("mac") or "").lower()
    if site and mac:
        return ("site_mac", site, mac)
    if device.get("id"):
        return ("id", device.get("id"))
    ip = device.get("ip") or ""
    if site and ip:
        return ("site_ip", site, ip)
    return None


def _device_lookup_keys(device):
    if not isinstance(device, dict):
        return []
    keys = []
    site = device.get("site") or ""
    mac = _norm_mac(device.get("mac"))
    ip = device.get("ip") or ""
    if site and mac:
        keys.append(("site_mac", site, mac))
    if device.get("id"):
        keys.append(("id", device.get("id")))
    # Only use IP as an identity when MAC is unavailable. Same-IP/different-MAC
    # rows can be real conflicts or stale leases, so do not collapse those here.
    if site and ip and not mac:
        keys.append(("site_ip", site, ip))
    return keys


def _is_manual_device(device):
    if not isinstance(device, dict):
        return False
    if device.get("discovered_by") == "manual":
        return True
    return "add_device_manual" in (device.get("modules_successful") or [])


def _block_key(block):
    if not isinstance(block, dict):
        return None
    site = block.get("site") or ""
    mac = _norm_mac(block.get("mac"))
    if site and mac:
        return ("site_mac", site, mac)
    if block.get("id"):
        return ("id", block.get("id"))
    ip = block.get("ip") or ""
    if site and ip:
        return ("site_ip", site, ip)
    return None


def _merge_blocks(*groups):
    merged = {}
    for group in groups:
        for item in group or []:
            key = _block_key(item)
            if key is None:
                continue
            existing = merged.get(key)
            if existing is None or (_parse_ts(item.get("blocked_at")) or datetime.min) >= (_parse_ts(existing.get("blocked_at")) or datetime.min):
                merged[key] = dict(item)
    return list(merged.values())


def _matches_block(device, block):
    if not isinstance(device, dict) or not isinstance(block, dict):
        return False
    if device.get("site") != block.get("site"):
        return False
    device_mac = _norm_mac(device.get("mac"))
    block_mac = _norm_mac(block.get("mac"))
    if device_mac and block_mac and device_mac == block_mac:
        return True
    if device.get("id") and block.get("id") and device.get("id") == block.get("id"):
        return True
    # IP-only blocks are intentionally conservative and only match devices
    # that still have no MAC. An IP can be reused by another real device.
    if not block_mac and not device_mac and device.get("ip") and device.get("ip") == block.get("ip"):
        return True
    return False


def _matching_block(device, blocks):
    for block in blocks or []:
        if _matches_block(device, block):
            return block
    return None


def _remove_matching_blocks(device, blocks):
    return [item for item in blocks or [] if not _matches_block(device, item)]


def _merge_device(current, incoming):
    if not isinstance(current, dict):
        return dict(incoming)
    if not isinstance(incoming, dict):
        return dict(current)

    merged = dict(current)
    current_ts = _device_merge_ts(current)
    incoming_ts = _device_merge_ts(incoming)
    incoming_is_newer = incoming_ts is not None and (current_ts is None or incoming_ts >= current_ts)

    if incoming_is_newer:
        for key, value in incoming.items():
            if key in ("id", "connections", "modules_successful", "modules_failed", "last_seen", "last_modified"):
                continue
            merged[key] = value
    else:
        for key, value in incoming.items():
            if key in ("id", "connections", "modules_successful", "modules_failed", "last_seen", "last_modified"):
                continue
            if merged.get(key) in ("", None, [], {}) and value not in ("", None, [], {}):
                merged[key] = value

    merged["last_seen"] = _newer_ts(current.get("last_seen"), incoming.get("last_seen"))
    merged["last_modified"] = _newer_ts(current.get("last_modified"), incoming.get("last_modified"))
    if current.get("discovered_at") and incoming.get("discovered_at"):
        merged["discovered_at"] = current.get("discovered_at")
    elif incoming.get("discovered_at"):
        merged["discovered_at"] = incoming.get("discovered_at")

    merged["connections"] = _merge_connections(current.get("connections"), incoming.get("connections"))
    merged["modules_successful"] = _merge_string_lists(current.get("modules_successful"), incoming.get("modules_successful"))
    merged["modules_failed"] = _merge_string_lists(current.get("modules_failed"), incoming.get("modules_failed"))
    return merged


def _site_key(site):
    if not isinstance(site, dict):
        return None
    if site.get("id"):
        return ("id", site.get("id"))
    if site.get("name"):
        return ("name", site.get("name"))
    return None


def _merge_site(current, incoming):
    if not isinstance(current, dict):
        return dict(incoming)
    if not isinstance(incoming, dict):
        return dict(current)
    merged = dict(current)
    if incoming.get("last_scan"):
        merged["last_scan"] = _newer_ts(current.get("last_scan"), incoming.get("last_scan"))
    for key, value in incoming.items():
        if key == "last_scan":
            continue
        if merged.get(key) in ("", None, [], {}) and value not in ("", None, [], {}):
            merged[key] = value
    return merged


def _merge_devices_store(current, incoming):
    if not isinstance(current, dict) or not isinstance(incoming, dict):
        return incoming

    merged = dict(current)
    current_meta = current.get("meta") or {}
    incoming_meta = incoming.get("meta") or {}
    blocks = _merge_blocks(current_meta.get("blocked_devices"), incoming_meta.get("blocked_devices"))
    current_devices = current.get("devices") or []
    incoming_devices = incoming.get("devices") or []
    devices = []
    by_key = {}
    id_aliases = {}

    def add_device(device):
        if not isinstance(device, dict):
            return
        nonlocal blocks
        if _is_manual_device(device):
            blocks = _remove_matching_blocks(device, blocks)
        elif _matching_block(device, blocks):
            return None
        keys = _device_lookup_keys(device)
        existing = next((by_key[key] for key in keys if key in by_key), None)
        if existing is not None:
            existing_id = existing.get("id")
            incoming_id = device.get("id")
            merged_device = _merge_device(existing, device)
            existing.clear()
            existing.update(merged_device)
            for key in _device_lookup_keys(existing):
                by_key[key] = existing
            if incoming_id and existing_id and incoming_id != existing_id:
                id_aliases[incoming_id] = existing_id
            return existing
        if not keys:
            devices.append(dict(device))
            return None
        clone = dict(device)
        for key in keys:
            by_key[key] = clone
        devices.append(clone)
        return clone

    for device in current_devices:
        add_device(device)
    for incoming_device in incoming_devices:
        add_device(incoming_device)

    if id_aliases:
        for device in devices:
            if not isinstance(device, dict):
                continue
            for conn in device.get("connections") or []:
                remote = conn.get("remote_device")
                if remote in id_aliases:
                    conn["remote_device"] = id_aliases[remote]
            device["connections"] = _merge_connections([], device.get("connections"))

    sites = []
    by_site_key = {}
    for site in current.get("sites") or []:
        key = _site_key(site)
        if key is None:
            sites.append(dict(site))
            continue
        by_site_key[key] = dict(site)
        sites.append(by_site_key[key])
    for incoming_site in incoming.get("sites") or []:
        key = _site_key(incoming_site)
        if key is None or key not in by_site_key:
            if isinstance(incoming_site, dict):
                sites.append(dict(incoming_site))
            continue
        merged_site = _merge_site(by_site_key[key], incoming_site)
        by_site_key[key].clear()
        by_site_key[key].update(merged_site)

    merged.update(incoming)
    merged["devices"] = devices
    merged["sites"] = sites
    merged["meta"] = dict(current_meta)
    merged["meta"].update(incoming_meta)
    merged["meta"].pop("deleted_devices", None)
    if blocks:
        merged["meta"]["blocked_devices"] = blocks
    else:
        merged["meta"].pop("blocked_devices", None)
    merged["meta"]["last_modified"] = _newer_ts(
        (current.get("meta") or {}).get("last_modified"),
        (incoming.get("meta") or {}).get("last_modified"),
    )
    return merged


def write_json_store(db_path: str, name: str, data, merge: bool = True) -> None:
    now = datetime.now().isoformat()
    conn = _get_conn(db_path)
    if merge and name == "devices":
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("SELECT json FROM json_store WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            try:
                current = json.loads(row[0])
                data = _merge_devices_store(current, data)
            except Exception:
                pass
    payload = json.dumps(data)
    conn.execute(
        "INSERT INTO json_store (name, json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
        (name, payload, now)
    )
    conn.commit()
    conn.close()
