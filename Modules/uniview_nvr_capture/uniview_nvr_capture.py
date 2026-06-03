#!/usr/bin/env python3
"""
Uniview NVR Packet Capture

Logs in via LAPI (Digest auth), starts capture, waits, then downloads pcap.
"""

from __future__ import annotations

import json
import os
import ipaddress
import re
import struct
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store, write_json_store


def _append_log(path: Optional[str], message: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_db(path: str) -> Dict[str, Any]:
    return read_json_store(path, "devices") or {}


def save_json(path: str, data: Dict[str, Any]) -> None:
    write_json_store(path, "devices", data)


def normalize_mac(mac: str) -> str:
    mac = mac.strip().replace("-", ":").replace(".", "")
    if len(mac) == 12:
        mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
    return mac.upper()


def _is_mac_like(value: str) -> bool:
    if not value:
        return False
    norm = normalize_mac(value)
    if len(norm) != 17 or norm.count(":") != 5:
        return False
    return all(c in "0123456789ABCDEF:" for c in norm)




def parse_cdp(payload: bytes) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    dest_pattern = b"\x01\x00\x0c\xcc\xcc\xcc"
    idx_dest = payload.find(dest_pattern)
    if idx_dest >= 6:
        src = payload[idx_dest - 6:idx_dest]
        result["switch_mac"] = ":".join(f"{b:02x}" for b in src)
    snap_prefix = b"\xaa\xaa\x03\x00\x00\x0c\x20"
    idx = payload.find(snap_prefix)
    if idx == -1:
        return result
    if idx + 8 > len(payload):
        return result
    pid = int.from_bytes(payload[idx + 6:idx + 8], "big")
    if pid == 0x2004:
        return parse_dtp(payload, idx + 8)
    if pid not in (0x2000, 0x2002):
        return result
    cdp = payload[idx + 8:]
    if len(cdp) < 4:
        return result
    offset = 4  # version(1), ttl(1), checksum(2)
    while offset + 4 <= len(cdp):
        t = int.from_bytes(cdp[offset:offset+2], "big")
        l = int.from_bytes(cdp[offset+2:offset+4], "big")
        if l < 4 or offset + l > len(cdp):
            break
        value = cdp[offset+4:offset+l]
        if t == 0x0001:  # Device ID
            result["device_id"] = value.split(b"\x00")[0].decode(errors="ignore").strip()
        elif t == 0x0003:  # Port ID
            result["port_id"] = value.split(b"\x00")[0].decode(errors="ignore").strip()
        elif t == 0x0006:  # Platform
            result["platform"] = value.split(b"\x00")[0].decode(errors="ignore").strip()
        elif t == 0x000a:  # Native VLAN
            if len(value) >= 2:
                result["vlan"] = int.from_bytes(value[:2], "big")
        elif t == 0x0002:  # Address
            if len(value) >= 4:
                count = int.from_bytes(value[:4], "big")
                cursor = 4
                for _ in range(count):
                    if cursor + 2 > len(value):
                        break
                    ptype = value[cursor]
                    plen = value[cursor + 1]
                    cursor += 2 + plen
                    if cursor + 2 > len(value):
                        break
                    addr_len = int.from_bytes(value[cursor:cursor+2], "big")
                    cursor += 2
                    if cursor + addr_len > len(value):
                        break
                    addr = value[cursor:cursor+addr_len]
                    cursor += addr_len
                    if ptype == 0x01 and addr_len == 4:
                        result["ip"] = ".".join(str(b) for b in addr)
                        break
        offset += l
    if result:
        result["protocol"] = "cdp"
    return result


def parse_dtp(payload: bytes, start: int) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    data = payload[start:]
    marker = b"\x00\x04\x00\x0a"
    idx = data.find(marker)
    if idx == -1 or idx + 4 + 6 > len(data):
        return result
    mac_bytes = data[idx + 4:idx + 10]
    mac = ":".join(f"{b:02x}" for b in mac_bytes)
    result["dtp_neighbor_mac"] = mac
    result["device_id"] = normalize_mac(mac)
    result["protocol"] = "dtp"
    return result


def _parse_pcap_raw(path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    stats = {
        "format": "pcap",
        "linktype": None,
        "packets": 0,
        "snap_hits": 0,
        "dest_hits": 0,
        "first_packet_hex": "",
        "first_snap_packet": None,
        "first_snap_offset": None
        ,
        "first_dest_packet": None,
        "first_dest_offset": None
    }
    try:
        first_dtp = None
        with open(path, "rb") as f:
            header = f.read(24)
            if len(header) < 24:
                return None, stats
            magic_bytes = header[:4]
            if magic_bytes in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
                endian = ">"
            elif magic_bytes in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
                endian = "<"
            else:
                return None, stats
            stats["linktype"] = struct.unpack(endian + "I", header[20:24])[0]
            while True:
                pkt_hdr = f.read(16)
                if len(pkt_hdr) < 16:
                    break
                ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + "IIII", pkt_hdr)
                data = f.read(incl_len)
                if len(data) < incl_len:
                    break
                stats["packets"] += 1
                if stats["packets"] == 1:
                    stats["first_packet_hex"] = data[:64].hex()
                dest_idx = data.find(b"\x01\x00\x0c\xcc\xcc\xcc")
                if dest_idx != -1:
                    stats["dest_hits"] += 1
                    if stats["first_dest_packet"] is None:
                        stats["first_dest_packet"] = stats["packets"]
                        stats["first_dest_offset"] = dest_idx
                snap_idx = data.find(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00")
                if snap_idx != -1:
                    stats["snap_hits"] += 1
                    if stats["first_snap_packet"] is None:
                        stats["first_snap_packet"] = stats["packets"]
                        stats["first_snap_offset"] = snap_idx
                parsed = parse_cdp(data)
                if parsed:
                    if parsed.get("protocol") == "cdp":
                        return parsed, stats
                    if parsed.get("protocol") == "dtp" and first_dtp is None:
                        first_dtp = parsed
    except Exception:
        return None, stats
    return first_dtp, stats


def _parse_pcapng_raw(path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    stats = {
        "format": "pcapng",
        "linktype": None,
        "packets": 0,
        "snap_hits": 0,
        "dest_hits": 0,
        "first_packet_hex": "",
        "first_snap_packet": None,
        "first_snap_offset": None
        ,
        "first_dest_packet": None,
        "first_dest_offset": None
    }
    try:
        first_dtp = None
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return None, stats
            block_type = struct.unpack("<I", header[:4])[0]
            if block_type != 0x0A0D0D0A:
                return None, stats
            bom = header[8:12]
            if bom == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif bom == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                return None, stats
            f.seek(0)
            while True:
                block_header = f.read(8)
                if len(block_header) < 8:
                    break
                btype, blen = struct.unpack(endian + "II", block_header)
                if blen < 12:
                    break
                body = f.read(blen - 8)
                if len(body) < blen - 8:
                    break
                if btype == 0x00000001 and len(body) >= 8:  # Interface Description Block
                    stats["linktype"] = struct.unpack(endian + "H", body[:2])[0]
                if btype == 0x00000006:  # Enhanced Packet Block
                    if len(body) < 28:
                        continue
                    cap_len = struct.unpack(endian + "I", body[12:16])[0]
                    packet_data = body[20:20 + cap_len]
                    stats["packets"] += 1
                    if stats["packets"] == 1:
                        stats["first_packet_hex"] = packet_data[:64].hex()
                    dest_idx = packet_data.find(b"\x01\x00\x0c\xcc\xcc\xcc")
                    if dest_idx != -1:
                        stats["dest_hits"] += 1
                        if stats["first_dest_packet"] is None:
                            stats["first_dest_packet"] = stats["packets"]
                            stats["first_dest_offset"] = dest_idx
                    snap_idx = packet_data.find(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00")
                    if snap_idx != -1:
                        stats["snap_hits"] += 1
                        if stats["first_snap_packet"] is None:
                            stats["first_snap_packet"] = stats["packets"]
                            stats["first_snap_offset"] = snap_idx
                    parsed = parse_cdp(packet_data)
                    if parsed:
                        if parsed.get("protocol") == "cdp":
                            return parsed, stats
                        if parsed.get("protocol") == "dtp" and first_dtp is None:
                            first_dtp = parsed
                elif btype == 0x00000003:  # Simple Packet Block
                    if len(body) < 8:
                        continue
                    packet_data = body[4:-4]
                    stats["packets"] += 1
                    if stats["packets"] == 1:
                        stats["first_packet_hex"] = packet_data[:64].hex()
                    dest_idx = packet_data.find(b"\x01\x00\x0c\xcc\xcc\xcc")
                    if dest_idx != -1:
                        stats["dest_hits"] += 1
                        if stats["first_dest_packet"] is None:
                            stats["first_dest_packet"] = stats["packets"]
                            stats["first_dest_offset"] = dest_idx
                    snap_idx = packet_data.find(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00")
                    if snap_idx != -1:
                        stats["snap_hits"] += 1
                        if stats["first_snap_packet"] is None:
                            stats["first_snap_packet"] = stats["packets"]
                            stats["first_snap_offset"] = snap_idx
                    parsed = parse_cdp(packet_data)
                    if parsed:
                        if parsed.get("protocol") == "cdp":
                            return parsed, stats
                        if parsed.get("protocol") == "dtp" and first_dtp is None:
                            first_dtp = parsed
    except Exception:
        return None, stats
    return first_dtp, stats


def parse_cdp_from_pcap(path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    parsed, stats = _parse_pcap_raw(path)
    if parsed and parsed.get("protocol") == "cdp":
        return parsed, stats
    parsed_ng, ng_stats = _parse_pcapng_raw(path)
    if parsed_ng and parsed_ng.get("protocol") == "cdp":
        return parsed_ng, ng_stats
    try:
        from scapy.utils import PcapReader
    except Exception:
        return parsed or parsed_ng, stats
    try:
        first_dtp = parsed or parsed_ng
        with PcapReader(path) as reader:
            for pkt in reader:
                payload = bytes(pkt)
                parsed = parse_cdp(payload)
                if parsed:
                    if parsed.get("protocol") == "cdp":
                        return parsed, stats
                    if parsed.get("protocol") == "dtp" and not first_dtp:
                        first_dtp = parsed
    except Exception:
        return None, stats
    return first_dtp, stats


def _scan_file_bytes(path: str, pattern: bytes) -> Tuple[int, Optional[int], str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return 0, None, ""
    count = data.count(pattern)
    if count == 0:
        return 0, None, ""
    first = data.find(pattern)
    start = max(0, first - 16)
    end = min(len(data), first + len(pattern) + 16)
    snippet = data[start:end].hex()
    return count, first, snippet


def _collect_nic_names(payload: Any) -> List[str]:
    names: List[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            names.extend(_collect_nic_names(value))
    elif isinstance(payload, list):
        for item in payload:
            names.extend(_collect_nic_names(item))
    elif isinstance(payload, str):
        if "NIC" in payload.upper():
            names.append(payload)
    return names


def _split_csv_values(value: Any) -> List[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\s,;]+", str(value or ""))
    items: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _extract_ip_from_text(value: str) -> str:
    match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", value or "")
    return match.group(0) if match else ""


def _collect_nic_ip_pairs(payload: Any) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []

    def walk(value: Any, current_nic: str = "") -> None:
        if isinstance(value, dict):
            nic_info_list = value.get("astNICInfoList")
            if isinstance(nic_info_list, list):
                for idx, item in enumerate(nic_info_list):
                    if not isinstance(item, dict):
                        continue
                    ip_addr = _extract_ip_from_text(str(item.get("stIpAddr") or ""))
                    if ip_addr:
                        pair = (f"eth{idx}", ip_addr)
                        if pair not in pairs:
                            pairs.append(pair)
            nic = current_nic
            for key, item in value.items():
                key_l = str(key).lower()
                if key_l in ("nic", "nicname", "name", "ifname", "interface", "portname") and isinstance(item, str):
                    if item.lower().startswith("eth") or "nic" in item.lower():
                        nic = item
            ips: List[str] = []
            for key, item in value.items():
                key_l = str(key).lower()
                if "ip" in key_l and isinstance(item, str):
                    found = _extract_ip_from_text(item)
                    if found:
                        ips.append(found)
            if nic and ips:
                for ip_addr in ips:
                    pair = (nic, ip_addr)
                    if pair not in pairs:
                        pairs.append(pair)
            for item in value.values():
                walk(item, nic)
        elif isinstance(value, list):
            for item in value:
                walk(item, current_nic)

    walk(payload)
    return pairs


def _same_ipv4_24(ip_a: str, ip_b: str) -> bool:
    try:
        addr_a = ipaddress.ip_address(ip_a)
        addr_b = ipaddress.ip_address(ip_b)
        if addr_a.version != 4 or addr_b.version != 4:
            return False
        return ipaddress.ip_network(f"{addr_a}/24", strict=False) == ipaddress.ip_network(f"{addr_b}/24", strict=False)
    except Exception:
        return False


def _choose_nic_for_target(target_ip: str, pairs: List[Tuple[str, str]]) -> str:
    for nic, nic_ip in pairs:
        if nic_ip == target_ip:
            return nic
    for nic, nic_ip in pairs:
        if _same_ipv4_24(target_ip, nic_ip):
            return nic
    return ""


def _find_or_create_switch(data: Dict[str, Any], site: str, cdp: Dict[str, Any], now: str) -> Optional[Dict[str, Any]]:
    switch_ip = cdp.get("ip")
    switch_name = (cdp.get("device_id") or "").strip()
    switch_mac = (cdp.get("dtp_neighbor_mac") or cdp.get("mac") or cdp.get("switch_mac") or "").lower()
    has_name = bool(switch_name) and not _is_mac_like(switch_name)
    has_ip = bool(switch_ip)
    has_mac = bool(switch_mac)
    platform = cdp.get("platform") or ""
    vendor = "cisco" if "cisco" in platform.lower() else ""
    if not platform and cdp.get("protocol") == "dtp":
        platform = "Cisco (DTP)"
        vendor = "cisco"

    match = None
    match_reason = ""
    for device in data.get("devices", []):
        if device.get("site") != site:
            continue
        if has_mac and (device.get("mac") or "").lower() == switch_mac:
            match = device
            match_reason = "mac"
            break
        if has_ip and device.get("ip") == switch_ip:
            match = device
            match_reason = "ip"
            break
        if has_name and (device.get("name") == switch_name or device.get("id") == switch_name):
            match = device
            match_reason = "name"
            break
    if match:
        if match.get("locked"):
            return match
        updated = False
        if has_name and match.get("name") != switch_name:
            match["name"] = switch_name
            updated = True
        if has_ip and (match_reason == "mac" or not match.get("ip")) and match.get("ip") != switch_ip:
            match["ip"] = switch_ip
            updated = True
        if has_mac:
            normalized = normalize_mac(switch_mac)
            if normalized and (match.get("mac") or "").lower() != normalized.lower():
                match["mac"] = normalized
                updated = True
        if platform and match.get("platform") != platform:
            match["platform"] = platform
            updated = True
        if vendor and match.get("vendor") != vendor:
            match["vendor"] = vendor
            updated = True
        if updated:
            match["last_modified"] = now
            match["last_seen"] = now
        return match
    if not has_name and not has_ip:
        return None
    display_name = switch_name if has_name else (switch_ip or switch_name)
    new_device = {
        "id": f"dev_{os.urandom(4).hex()}",
        "site": site,
        "name": display_name,
        "ip": switch_ip or "",
        "mac": normalize_mac(switch_mac) if switch_mac else "",
        "type": "switch",
        "model": "",
        "platform": platform,
        "vendor": vendor,
        "os": "",
        "discovered_by": "uniview_nvr_capture",
        "discovered_at": now,
        "last_seen": now,
        "last_modified": now,
        "status": "unknown",
        "reachable": False,
        "config_backup": {"enabled": False},
        "connections": [],
        "credentials_used": None,
        "modules_successful": [],
        "modules_failed": [],
        "locked": False,
        "notes": "Placeholder from NVR capture"
    }
    data.setdefault("devices", []).append(new_device)
    return new_device


def _upsert_connection(device: Dict[str, Any], remote_device_id: str, local_interface: str, remote_interface: str, now: str, remote_device: Optional[Dict[str, Any]] = None) -> None:
    connections = device.setdefault("connections", [])
    for conn in connections:
        if conn.get("remote_device") == remote_device_id and conn.get("protocol") == "cdp":
            if remote_interface and conn.get("remote_interface") == remote_interface:
                conn["local_interface"] = local_interface or conn.get("local_interface", "")
                if remote_device:
                    conn["remote_name"] = remote_device.get("name") or conn.get("remote_name", "")
                    conn["remote_ip"] = remote_device.get("ip") or conn.get("remote_ip", "")
                    conn["remote_mac"] = remote_device.get("mac") or conn.get("remote_mac", "")
                conn["status"] = "up"
                conn["discovered_at"] = now
                return
    connections.append({
        "id": f"conn_{os.urandom(4).hex()}",
        "local_interface": local_interface or "",
        "remote_device": remote_device_id,
        "remote_name": (remote_device.get("name") if remote_device else "") or "",
        "remote_ip": (remote_device.get("ip") if remote_device else "") or "",
        "remote_mac": (remote_device.get("mac") if remote_device else "") or "",
        "remote_interface": remote_interface or "",
        "protocol": "cdp",
        "discovered_at": now,
        "status": "up"
    })


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_config(sys.argv[1])
    params = config.get("parameters", {})
    log_file = config.get("log_file")

    username = (params.get("username") or "").strip()
    password = params.get("password") or ""
    capture_seconds = int(params.get("capture_seconds", 60) or 60)
    capture_window_seconds = int(params.get("capture_window_seconds", 45) or 45)
    capture_window_seconds = max(1, capture_window_seconds)
    nic_choice = (params.get("nic") or "NIC1").strip().upper()
    nic_name = "eth1" if nic_choice == "NIC2" else "eth0"
    nic_label = (params.get("nic_label") or "").strip()
    packet_size = int(params.get("packet_size", 1500) or 1500)
    ip_mode_raw = (params.get("ip_mode") or "filter").strip().lower()
    port_mode_raw = (params.get("port_mode") or "all").strip().lower()
    mode_map = {"all": 0, "specify": 1, "filter": 2}
    ip_mode = mode_map.get(ip_mode_raw, 0)
    port_mode = mode_map.get(port_mode_raw, 0)
    ip_values = _split_csv_values(params.get("ip_values") or params.get("ip_list") or "")

    db_path = config.get("database_path")
    site_name = (config.get("site_name") or "").strip()
    targets = params.get("targets") or {}
    device_ids = targets.get("device_ids") or []
    manual_devices = targets.get("manual_devices") or []
    auto_targets = bool(targets.get("auto"))
    auto_on_empty = bool(targets.get("auto_on_empty"))

    if not username or password is None:
        print(json.dumps({"status": "error", "message": "Missing username/password"}))
        return

    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"requests is required: {exc}"}))
        return

    devices = []
    data = {}
    if db_path and os.path.exists(db_path):
        data = load_db(db_path)
        site_devices = [d for d in data.get("devices", []) if d.get("site") == site_name and d.get("ip")]
        if device_ids:
            wanted = set(device_ids)
            devices = [d for d in site_devices if d.get("id") in wanted]
            if not devices and (auto_targets or auto_on_empty) and not manual_devices:
                devices = [d for d in site_devices if (d.get("type") or "").lower() == "nvr"]
        else:
            devices = [d for d in site_devices if (d.get("type") or "").lower() == "nvr"]

    for entry in manual_devices:
        ip = (entry.get("ip") or "").strip()
        if not ip:
            continue
        devices.append({
            "id": f"manual_{ip}",
            "ip": ip,
            "name": entry.get("name") or ip
        })

    if not devices:
        if auto_targets:
            print(json.dumps({"status": "success", "message": "No NVR devices found", "site": site_name, "captures": []}))
        else:
            print(json.dumps({"status": "error", "message": "No NVR devices selected"}))
        return

    results = []
    failures = []
    summary = {"cdp_ok": 0, "dtp_ok": 0, "no_cdp": 0, "switch_skipped": 0, "capture_fail": 0, "auth_fail": 0}

    if log_file:
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"MODULE START {datetime.now().isoformat()}\n")
        except Exception:
            _append_log(log_file, f"MODULE START {datetime.now().isoformat()}")

    def capture_device(ip: str, name: str) -> Tuple[str, str, Optional[Dict[str, Any]], Optional[str]]:
        base = f"http://{ip}"
        session = requests.Session()
        session.trust_env = False
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive"
        })
        _append_log(log_file, f"CONNECT {ip} direct")
        auth = HTTPDigestAuth(username, password)

        def _url(path: str) -> str:
            return f"{base}{path}"

        def _auth_nonce() -> str:
            thread_local = getattr(auth, "_thread_local", None)
            chal = getattr(thread_local, "chal", {}) if thread_local else {}
            return str(chal.get("nonce") or "")

        def _request_digest(method: str, url: str, *, headers: Optional[Dict[str, str]] = None,
                            data: Any = None, timeout: int = 10, uri_override: Optional[str] = None):
            return session.request(method, url, headers=headers, data=data, auth=auth, timeout=timeout)

        login_url = _url("/LAPI/V1.0/System/Security/Login")
        session.cookies.set("langInfo_", "1", domain=ip, path="/")
        session.cookies.set("len", "9", domain=ip, path="/")
        login_ok = False

        def _login(reason: str = "LOGIN") -> bool:
            nonlocal login_ok
            _append_log(log_file, f"{reason} {ip} {login_url}")
            login_headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": base,
                "Referer": f"{base}/"
            }
            try:
                login_resp = session.put(login_url, headers=login_headers, auth=auth, timeout=10)
                login_ok = login_resp.status_code in (200, 204)
                if not login_ok:
                    _append_log(log_file, f"LOGIN FAIL {ip} {login_resp.status_code}")
                    _append_log(log_file, f"LOGIN BODY {ip} {login_resp.text[:300]}")
                else:
                    _append_log(log_file, f"LOGIN OK {ip}")
                return login_ok
            except Exception as exc:
                _append_log(log_file, f"LOGIN ERROR {ip} {exc}")
                return False

        try:
            if not _login("LOGIN"):
                _append_log(log_file, f"LOGIN FALLBACK {ip}: trying packet capture start directly")
        except Exception as exc:
            _append_log(log_file, f"LOGIN ERROR {ip} {exc}")
            _append_log(log_file, f"LOGIN FALLBACK {ip}: trying packet capture start directly")

        def _keepalive() -> None:
            if not login_ok:
                return
            try:
                keepalive_url = _url("/LAPI/V1.0/System/Security/KeepAlive")
                _request_digest("PUT", keepalive_url, headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": base,
                    "Referer": f"{base}/"
                }, timeout=10)
            except Exception:
                return

        common_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{base}/page/config.html"
        }

        existing_capture_running = False
        status_url = _url(f"/LAPI/V1.0/Network/PacketCapture/Status?_={int(time.time() * 1000)}")
        try:
            status_resp = _request_digest("GET", status_url, headers=common_headers, timeout=10)
            _append_log(log_file, f"STATUS {ip} {status_resp.status_code}")
            if status_resp.text:
                _append_log(log_file, f"STATUS BODY {ip} {status_resp.text[:500]}")
            if status_resp.status_code == 200 and status_resp.text:
                try:
                    status_data = status_resp.json().get("Response", {}).get("Data", {})
                    existing_capture_running = int(status_data.get("IsTcpdump") or 0) == 1
                except Exception:
                    existing_capture_running = False
        except Exception:
            pass

        nic_value = nic_label or nic_name
        try:
            link_url = _url(f"/LAPI/V1.0/Network/LinkInfo?_={int(time.time() * 1000)}")
            link_resp = _request_digest("GET", link_url, headers=common_headers, timeout=10)
            _append_log(log_file, f"LINKINFO {ip} {link_resp.status_code}")
            if link_resp.text:
                _append_log(log_file, f"LINKINFO BODY {ip} {link_resp.text[:500]}")
            if link_resp.status_code == 200 and link_resp.text:
                try:
                    pairs = _collect_nic_ip_pairs(link_resp.json())
                except Exception:
                    pairs = _collect_nic_ip_pairs(json.loads(link_resp.text))
                _append_log(log_file, f"LINKINFO PAIRS {ip} {pairs}")
                selected_nic = _choose_nic_for_target(ip, pairs)
                if selected_nic:
                    nic_value = selected_nic
                    _append_log(log_file, f"NIC AUTO {ip}: selected {nic_value} from LinkInfo")
        except Exception as exc:
            _append_log(log_file, f"LINKINFO ERROR {ip} {exc}")

        if login_ok:
            try:
                cgi_url = _url("/cgi-bin/main-cgi")
                cgi_headers = {
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "text/plain;charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": base,
                    "Referer": f"{base}/page/config.html"
                }
                nic_body = "json=" + json.dumps({
                    "cmd": 29,
                    "bFour": False,
                    "szUserName": username,
                    "u32UserLoginHandle": _auth_nonce()
                }, separators=(",", ":"))
                nic_resp = _request_digest("POST", cgi_url, headers=cgi_headers, data=nic_body, timeout=10)
                _append_log(log_file, f"NICINFO {ip} {nic_resp.status_code}")
                if nic_resp.text:
                    _append_log(log_file, f"NICINFO BODY {ip} {nic_resp.text[:500]}")
                if nic_resp.status_code == 200 and nic_resp.text:
                    try:
                        pairs = _collect_nic_ip_pairs(nic_resp.json())
                    except Exception:
                        pairs = _collect_nic_ip_pairs(json.loads(nic_resp.text))
                    _append_log(log_file, f"NICINFO PAIRS {ip} {pairs}")
                    selected_nic = _choose_nic_for_target(ip, pairs)
                    if selected_nic:
                        nic_value = selected_nic
                        _append_log(log_file, f"NIC AUTO {ip}: selected {nic_value} from NICINFO")
            except Exception as exc:
                _append_log(log_file, f"NICINFO ERROR {ip} {exc}")

        start_urls = [
            _url("/LAPI/V1.1/Network/PacketCapture/Start"),
            _url("/LAPI/V1.0/Network/PacketCapture/Start")
        ]
        stop_url = _url("/LAPI/V1.0/Network/PacketCapture/Stop")
        download_url = _url("/LAPI/V1.0/Network/PacketCapture/File/DownLoad")

        start_headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": base,
            "Referer": f"{base}/page/config.html"
        }
        if existing_capture_running:
            try:
                _append_log(log_file, f"STOP EXISTING {ip}")
                stop_resp = _request_digest("PUT", stop_url, headers=start_headers, timeout=10)
                _append_log(log_file, f"STOP EXISTING STATUS {ip} {stop_resp.status_code}")
                if stop_resp.text:
                    _append_log(log_file, f"STOP EXISTING BODY {ip} {stop_resp.text[:300]}")
                time.sleep(2)
            except Exception as exc:
                _append_log(log_file, f"STOP EXISTING ERROR {ip} {exc}")
        base_start_payload = {
            "PacketSize": packet_size,
            "PortMode": port_mode,
            "IPMode": ip_mode,
            "NicName": nic_value
        }
        if ip_mode in (1, 2):
            capture_ip_values = list(ip_values)
            if not capture_ip_values and ip_mode == 2:
                capture_ip_values = [_extract_ip_from_text(nic_label) or ip]
            if capture_ip_values:
                base_start_payload["IPList"] = {
                    "AddressType": 0,
                    "List": capture_ip_values,
                    "Num": len(capture_ip_values)
                }

        last_start_ts = 0.0

        def start_capture() -> bool:
            nonlocal last_start_ts
            now = time.time()
            wait_for = 10.0 - (now - last_start_ts)
            if wait_for > 0:
                time.sleep(wait_for)
            nic_candidates = [nic_value]
            if nic_value in ("eth0", "eth1"):
                alt = "eth1" if nic_value == "eth0" else "eth0"
                nic_candidates.append(alt)
            for candidate_nic in nic_candidates:
                start_payload = dict(base_start_payload)
                start_payload["NicName"] = candidate_nic
                body = json.dumps(start_payload, separators=(",", ":")) + "\r\n"
                for url in start_urls:
                    _append_log(log_file, f"START {ip} {start_payload}")
                    resp = _request_digest("PUT", url, data=body, headers=start_headers, timeout=20)
                    if resp.status_code in (200, 204):
                        last_start_ts = time.time()
                        _append_log(log_file, f"START OK {ip} nic={candidate_nic}")
                        return True
                    _append_log(log_file, f"START FAIL {ip} {resp.status_code}")
                    _append_log(log_file, f"START URL {ip} {url}")
                    _append_log(log_file, f"START HEADERS {ip} content-type={resp.headers.get('Content-Type','')} server={resp.headers.get('Server','')}")
                    _append_log(log_file, f"START BODY {ip} {resp.text[:300]}")
            return False

        def _ensure_min_capture_window() -> None:
            if last_start_ts <= 0:
                return
            elapsed = time.time() - last_start_ts
            if elapsed < 10.0:
                time.sleep(10.0 - elapsed)

        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "generated_maps", "nvr_captures")
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)

        deadline = time.time() + max(1, capture_seconds)
        attempt = 0

        while time.time() < deadline:
            attempt += 1
            if not start_capture():
                return ip, name, None, "start_fail"

            _append_log(log_file, f"CAPTURE {ip} running (attempt {attempt})...")
            time.sleep(capture_window_seconds)

            _append_log(log_file, f"STOP {ip}")
            try:
                stop_resp = _request_digest("PUT", stop_url, headers=start_headers, timeout=10)
                _append_log(log_file, f"STOP STATUS {ip} {stop_resp.status_code}")
                if stop_resp.text:
                    _append_log(log_file, f"STOP BODY {ip} {stop_resp.text[:300]}")
            except Exception:
                pass

            _append_log(log_file, f"DOWNLOAD {ip}")
            download_resp = _request_digest("GET", download_url, headers={
                "Accept": "*/*",
                "Referer": f"{base}/page/config.html"
            }, timeout=30)
            if download_resp.status_code != 200 or not download_resp.content:
                if download_resp.status_code != 200:
                    _append_log(log_file, f"DOWNLOAD FAIL {ip} {download_resp.status_code}")
                    if download_resp.text:
                        _append_log(log_file, f"DOWNLOAD BODY {ip} {download_resp.text[:500]}")
                _keepalive()
                continue

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_ip = ip.replace(".", "_")
            out_path = os.path.join(out_dir, f"uniview_capture_{safe_ip}_{ts}_{attempt}.pcap")
            with open(out_path, "wb") as f:
                f.write(download_resp.content)
            _append_log(log_file, f"SAVED {ip} {out_path}")

            snap_pattern = b"\xaa\xaa\x03\x00\x00\x0c\x20\x00"
            snap_alt_pattern = b"\xaa\xaa\x03\x00\x00\x0c\x20\x02"
            snap_dtp_pattern = b"\xaa\xaa\x03\x00\x00\x0c\x20\x04"
            dest_pattern = b"\x01\x00\x0c\xcc\xcc\xcc"
            snap_count, snap_offset, snap_snippet = _scan_file_bytes(out_path, snap_pattern)
            snap_alt_count, snap_alt_offset, snap_alt_snippet = _scan_file_bytes(out_path, snap_alt_pattern)
            snap_dtp_count, snap_dtp_offset, snap_dtp_snippet = _scan_file_bytes(out_path, snap_dtp_pattern)
            dest_count, dest_offset, dest_snippet = _scan_file_bytes(out_path, dest_pattern)
            if snap_count or snap_alt_count or snap_dtp_count or dest_count:
                _append_log(
                    log_file,
                    (
                        f"FILE SCAN {ip}: snap_count={snap_count}, snap_offset={snap_offset}, "
                        f"snap_alt_count={snap_alt_count}, snap_alt_offset={snap_alt_offset}, "
                        f"snap_dtp_count={snap_dtp_count}, snap_dtp_offset={snap_dtp_offset}, "
                        f"dest_count={dest_count}, dest_offset={dest_offset}, "
                        f"snap_snippet={snap_snippet}, snap_alt_snippet={snap_alt_snippet}, "
                        f"snap_dtp_snippet={snap_dtp_snippet}, "
                        f"dest_snippet={dest_snippet}"
                    )
                )

            cdp, stats = parse_cdp_from_pcap(out_path)
            if not cdp:
                _append_log(
                    log_file,
                    (
                        f"PARSE FAIL {ip}: no CDP in {stats.get('format')} "
                        f"(linktype={stats.get('linktype')}, packets={stats.get('packets')}, "
                        f"snap_hits={stats.get('snap_hits')}, dest_hits={stats.get('dest_hits')}, "
                        f"first_snap_packet={stats.get('first_snap_packet')}, "
                        f"first_snap_offset={stats.get('first_snap_offset')}, "
                        f"first_dest_packet={stats.get('first_dest_packet')}, "
                        f"first_dest_offset={stats.get('first_dest_offset')}, "
                        f"first_packet_hex={stats.get('first_packet_hex')})"
                    )
                )
            else:
                _append_log(log_file, f"PARSED {ip}: {cdp}")

            try:
                os.remove(out_path)
                _append_log(log_file, f"DELETED {out_path}")
            except Exception:
                pass

            if cdp:
                return ip, name, cdp, "ok"

        return ip, name, None, "no_cdp"

    updated = 0
    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        future_map = {}
        for device in devices:
            ip = (device.get("ip") or "").strip()
            if not ip:
                continue
            name = device.get("name") or ip
            future_map[pool.submit(capture_device, ip, name)] = device

        for future in as_completed(future_map):
            try:
                ip, name, cdp, reason = future.result()
            except Exception as exc:
                device = future_map.get(future) or {}
                ip = (device.get("ip") or "").strip()
                _append_log(log_file, f"ERROR {ip} {exc}")
                failures.append({"ip": ip, "name": ip or "unknown", "reason": "exception"})
                continue

            if cdp:
                if cdp.get("protocol") == "cdp":
                    summary["cdp_ok"] += 1
                else:
                    summary["dtp_ok"] += 1
                if data:
                    now = datetime.now().isoformat()
                    dev_rec = None
                    device = future_map.get(future) or {}
                    incoming_mac = (device.get("mac") or "").lower()
                    for d in data.get("devices", []):
                        if d.get("site") == site_name and d.get("ip") == ip:
                            dev_rec = d
                            break
                        if incoming_mac and (d.get("mac") or "").lower() == incoming_mac:
                            dev_rec = d
                            break
                    if dev_rec:
                        if dev_rec.get("locked"):
                            failures.append({"ip": ip, "name": name, "reason": "locked"})
                            continue
                        dev_rec["type"] = "nvr"
                        if name:
                            dev_rec["name"] = name
                        if incoming_mac and not dev_rec.get("mac"):
                            dev_rec["mac"] = normalize_mac(incoming_mac)
                        if dev_rec.get("ip") != ip:
                            dev_rec["ip"] = ip
                        switch_name = (cdp.get("device_id") or "").strip()
                        switch_ip = (cdp.get("ip") or "").strip()
                        has_name = bool(switch_name) and not _is_mac_like(switch_name)
                        has_ip = bool(switch_ip)
                        if has_name:
                            dev_rec["parent_switch_name"] = switch_name
                        if has_ip:
                            dev_rec["parent_switch_ip"] = switch_ip
                        if cdp.get("port_id"):
                            dev_rec["parent_switch_port"] = cdp.get("port_id")
                        if "vlan" in cdp:
                            dev_rec["vlan"] = str(cdp["vlan"])
                        if cdp.get("platform"):
                            dev_rec["parent_switch_platform"] = cdp.get("platform")
                        switch_device = _find_or_create_switch(data, site_name, cdp, now)
                        if switch_device:
                            if not has_ip:
                                dev_rec["parent_switch_ip"] = switch_device.get("ip") or dev_rec.get("parent_switch_ip")
                            if not has_name:
                                dev_rec["parent_switch_name"] = switch_device.get("name") or dev_rec.get("parent_switch_name")
                            _upsert_connection(dev_rec, switch_device["id"], nic_name, cdp.get("port_id") or "", now, remote_device=switch_device)
                            _upsert_connection(
                                switch_device,
                                dev_rec["id"],
                                cdp.get("port_id") or "",
                                nic_name,
                                now,
                                remote_device=dev_rec
                            )
                        else:
                            if not has_name and not has_ip:
                                summary["switch_skipped"] += 1
                                _append_log(log_file, f"SKIP {ip}: switch has no name/ip (protocol={cdp.get('protocol')})")
                        dev_rec["last_modified"] = now
                        updated += 1
                results.append({"ip": ip, "name": name, "cdp_found": True})
            else:
                if reason == "no_cdp":
                    summary["no_cdp"] += 1
                elif reason and ("login_fail_401" in reason or "login_fail_403" in reason):
                    summary["auth_fail"] += 1
                else:
                    summary["capture_fail"] += 1
                failures.append({"ip": ip, "name": name, "reason": reason or "capture_failed"})

    if data and db_path:
        try:
            data.setdefault("meta", {})["last_modified"] = datetime.now().isoformat()
            save_json(db_path, data)
        except Exception:
            pass

    if log_file:
        _append_log(log_file, ("SUMMARY: cdp_ok={cdp_ok}, dtp_ok={dtp_ok}, no_cdp={no_cdp}, switch_skipped={switch_skipped}, capture_fail={capture_fail}, auth_fail={auth_fail}, updated={updated}").format(**summary, updated=updated))
        if failures:
            for failure in failures:
                _append_log(log_file, f"FAIL {failure.get('ip')} {failure.get('reason')}")

    print(json.dumps({"status": "success", "captures": results, "failures": failures, "updated_devices": updated}))


if __name__ == "__main__":
    main()
