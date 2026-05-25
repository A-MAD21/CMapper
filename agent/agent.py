#!/usr/bin/env python3
"""
Lightweight Windows agent: scan IP range (ping + ARP + DNS), export CSV,
post results to server.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import subprocess
import threading
import time
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from datetime import datetime, timedelta
from ipaddress import ip_network
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DEFAULT_CONFIG = "agent_config.json"
LOG_FILE = "agent.log"
STATE_FILE = "agent_state.json"
CSV_HEADER = [
    "Status", "Name", "IP", "Radmin", "Http", "Https", "Ftp", "Rdp",
    "Shared folders", "Shared printers", "NetBIOS group", "Manufacturer",
    "MAC address", "User", "Date", "Comments"
]


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state(base_dir: str) -> dict:
    path = os.path.join(base_dir, STATE_FILE)
    if not os.path.exists(path):
        return {"devices": [], "modules": {}, "config": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"devices": [], "modules": {}, "config": {}}


def save_state(base_dir: str, state: dict) -> None:
    path = os.path.join(base_dir, STATE_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def log(base_dir: str, message: str):
    ts = datetime.now().isoformat()
    line = f"[{ts}] {message}"
    print(line)
    try:
        with open(os.path.join(base_dir, LOG_FILE), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def auto_find_config(base_dir: str, preferred: str) -> str:
    preferred_path = os.path.join(base_dir, preferred)
    if os.path.exists(preferred_path):
        return preferred_path
    candidates = [f for f in os.listdir(base_dir) if f.lower().endswith(".json")]
    if len(candidates) == 1:
        return os.path.join(base_dir, candidates[0])
    return preferred_path


def resolve_server_url(server_url: str) -> str:
    if not server_url:
        return ""
    parsed = urlparse(server_url)
    if not parsed.scheme or not parsed.netloc:
        # allow host:port
        server_url = "http://" + server_url
        parsed = urlparse(server_url)
    host = parsed.hostname
    if host and not _is_ip(host):
        try:
            resolved = socket.gethostbyname(host)
            netloc = resolved
            if parsed.port:
                netloc += f":{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
        except Exception:
            return server_url
    return server_url


def get_local_identity(server_url: str) -> dict:
    hostname = socket.gethostname()
    ip = ""
    try:
        parsed = urlparse(server_url)
        target_host = parsed.hostname or server_url
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)
        sock.connect((target_host, parsed.port or 80))
        ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = ""
    mac = ""
    try:
        import uuid
        mac_int = uuid.getnode()
        mac = ":".join(f"{(mac_int >> ele) & 0xff:02x}" for ele in range(40, -1, -8)).upper()
    except Exception:
        mac = ""
    return {"name": hostname, "ip": ip, "mac": mac}


def _is_ip(value: str) -> bool:
    try:
        socket.inet_aton(value)
        return True
    except OSError:
        return False


def parse_range(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if "/" in value:
        try:
            return [str(ip) for ip in ip_network(value, strict=False).hosts()]
        except Exception:
            return []
    if "-" in value:
        start, end = value.split("-", 1)
        start = start.strip()
        end = end.strip()
        if not start or not end:
            return []
        try:
            start_int = int.from_bytes(socket.inet_aton(start), "big")
            end_int = int.from_bytes(socket.inet_aton(end), "big")
        except OSError:
            return []
        if end_int < start_int:
            start_int, end_int = end_int, start_int
        return [socket.inet_ntoa(i.to_bytes(4, "big")) for i in range(start_int, end_int + 1)]
    # single ip
    if _is_ip(value):
        return [value]
    return []


def parse_ranges(values) -> list[str]:
    ips = []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return ips
    for entry in values:
        if not entry:
            continue
        if isinstance(entry, str):
            ips.extend(parse_range(entry))
    # de-dupe while preserving order
    seen = set()
    unique = []
    for ip in ips:
        if ip in seen:
            continue
        seen.add(ip)
        unique.append(ip)
    return unique


def get_local_network_ranges() -> list[str]:
    ranges = []
    try:
        result = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.splitlines()
        current_ip = None
        current_mask = None
        for line in lines:
            line = line.strip()
            if line.lower().startswith("ipv4 address"):
                parts = line.split(":")
                if len(parts) > 1:
                    current_ip = parts[1].strip()
            elif line.lower().startswith("subnet mask"):
                parts = line.split(":")
                if len(parts) > 1:
                    current_mask = parts[1].strip()
            if current_ip and current_mask:
                try:
                    net = ip_network(f"{current_ip}/{current_mask}", strict=False)
                    ranges.append(str(net))
                except Exception:
                    pass
                current_ip = None
                current_mask = None
    except Exception:
        return []
    # de-dupe
    seen = set()
    unique = []
    for cidr in ranges:
        if cidr in seen:
            continue
        seen.add(cidr)
        unique.append(cidr)
    return unique


def ping_ip(ip: str, timeout_ms: int = 200) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            capture_output=True,
            text=True,
            timeout=(timeout_ms / 1000 + 1)
        )
        return result.returncode == 0
    except Exception:
        return False


def ping_sweep(ips: list[str], workers: int = 64, on_progress=None) -> set[str]:
    alive = set()
    lock = threading.Lock()
    idx = 0
    total = len(ips)

    def worker():
        nonlocal idx
        while True:
            with lock:
                if idx >= len(ips):
                    return
                ip = ips[idx]
                idx += 1
            if ping_ip(ip):
                with lock:
                    alive.add(ip)
            if on_progress:
                on_progress(idx, total, len(alive))

    threads = []
    for _ in range(min(workers, max(1, len(ips)))):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return alive


def read_arp_table() -> dict:
    entries = {}
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
        output = result.stdout.splitlines()
        for line in output:
            parts = line.split()
            if len(parts) >= 3 and _is_ip(parts[0]):
                ip = parts[0]
                mac = parts[1]
                entries[ip] = mac
    except Exception:
        pass
    return entries


def resolve_hostname(ip: str) -> str:
    # Prefer NetBIOS (similar to MikroTik ip-scan netbios)
    try:
        result = subprocess.run(
            ["nbtstat", "-A", ip],
            capture_output=True,
            text=True,
            timeout=2
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "<00>" in line and "UNIQUE" in line:
                parts = re.split(r"\s+", line)
                if parts:
                    name = parts[0].strip()
                    if name and name.upper() != "WORKGROUP":
                        return name
    except Exception:
        pass
    return ""


def build_devices(ips: list[str], base_dir: str = "", set_state=None) -> list[dict]:
    # Ping sweep to populate ARP cache
    def progress(done, total, alive_count):
        if total == 0:
            return
        if done % 25 == 0 or done == total:
            log(base_dir, f"Pinged {done}/{total} (alive {alive_count})")

    if set_state:
        set_state("pinging")
    alive = ping_sweep(ips, on_progress=progress)
    arp = read_arp_table()
    # Only report hosts that responded or are in ARP
    report_ips = sorted(set(alive) | set(arp.keys()))
    # Resolve hostnames in parallel
    hostnames = {}
    to_resolve = [ip for ip in report_ips if ip in ips and (ip in alive or arp.get(ip))]
    if to_resolve:
        log(base_dir, f"Resolving hostnames for {len(to_resolve)} hosts...")
        if set_state:
            set_state("resolving_hostnames")
        with ThreadPoolExecutor(max_workers=32) as pool:
            future_map = {pool.submit(resolve_hostname, ip): ip for ip in to_resolve}
            completed = 0
            total = len(to_resolve)
            for future in as_completed(future_map):
                ip = future_map[future]
                try:
                    hostnames[ip] = future.result()
                except Exception:
                    hostnames[ip] = ""
                completed += 1
                if completed % 25 == 0 or completed == total:
                    log(base_dir, f"Resolved {completed}/{total} hostnames")

    devices = []
    for ip in report_ips:
        if ip not in ips:
            continue
        mac = arp.get(ip, "")
        hostname = hostnames.get(ip, "")
        devices.append({
            "ip": ip,
            "mac": mac.upper() if mac else "",
            "name": hostname or (mac.upper() if mac else "") or ip,
            "hostname": hostname
        })
    return devices


def save_csv(path: str, devices: list[dict]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-16", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(CSV_HEADER)
        for dev in devices:
            writer.writerow([
                "On",
                dev.get("name") or dev.get("hostname") or dev.get("ip") or "",
                dev.get("ip") or "",
                "", "", "", "", "", "", "", "",
                dev.get("manufacturer") or "",
                dev.get("mac") or "",
                "", "", ""
            ])


def cleanup_old_scans(scan_dir: str, retention_days: int):
    cutoff = time.time() - retention_days * 86400
    if not os.path.isdir(scan_dir):
        return
    for root, _, files in os.walk(scan_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except Exception:
                pass


def write_state_snapshot(config: dict, state: dict, local_scan_dir: str) -> dict:
    scan_time = datetime.now().isoformat()
    safe_site = (config.get("site") or "site").replace("/", "_").replace("\\", "_")
    ts = scan_time.replace(":", "").replace("-", "").replace("T", "_").split(".")[0]
    csv_path = os.path.join(local_scan_dir, f"{safe_site}_{ts}.csv")
    save_csv(csv_path, state.get("devices") or [])
    cleanup_old_scans(local_scan_dir, int(config.get("retention_days") or 180))
    return {"scan_time": scan_time, "csv_path": csv_path}


def post_report(server_url: str, token: str, payload: dict):
    url = server_url.rstrip("/") + "/api/agent/report"
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Agent-Token", token)
    with urlopen(req, timeout=20) as resp:
        return resp.getcode(), resp.read().decode("utf-8")


def poll_config(server_url: str, agent_id: str, token: str) -> dict:
    url = server_url.rstrip("/") + f"/api/agent/config/{agent_id}"
    req = Request(url, method="GET")
    req.add_header("X-Agent-Token", token)
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_scan(config: dict, local_scan_dir: str, base_dir: str = "", set_state=None) -> dict:
    target_range = config.get("target_range", "")
    target_ranges = config.get("target_ranges") or []
    ips = parse_ranges(target_ranges) if target_ranges else parse_range(target_range)
    if not ips:
        return {"scan_time": datetime.now().isoformat(), "devices": [], "csv_path": ""}
    if set_state:
        set_state("scanning")
    devices = build_devices(ips, base_dir=base_dir, set_state=set_state)
    scan_time = datetime.now().isoformat()
    safe_site = (config.get("site") or "site").replace("/", "_").replace("\\", "_")
    ts = scan_time.replace(":", "").replace("-", "").replace("T", "_").split(".")[0]
    csv_path = os.path.join(local_scan_dir, f"{safe_site}_{ts}.csv")
    save_csv(csv_path, devices)
    cleanup_old_scans(local_scan_dir, int(config.get("retention_days") or 180))
    return {"scan_time": scan_time, "devices": devices, "csv_path": csv_path}


def update_state_from_scan(state: dict, scan_result: dict) -> dict:
    now = datetime.now().isoformat()
    devices = state.get("devices") or []
    by_ip = {d.get("ip"): d for d in devices if d.get("ip")}
    by_mac = {d.get("mac"): d for d in devices if d.get("mac")}
    def is_mac_like(value: str) -> bool:
        if not value:
            return False
        v = value.replace("-", ":").replace(".", "").upper()
        if len(v) == 12:
            return True
        return v.count(":") == 5
    def can_update_name(dev: dict, name: str) -> bool:
        if not name:
            return False
        dev_type = (dev.get("type") or "").lower()
        if dev_type and dev_type not in ("unknown", "pc", "pda"):
            return False
        current = (dev.get("name") or "").strip()
        ip = (dev.get("ip") or "").strip()
        if not current:
            return True
        if ip and current == ip:
            return True
        if is_mac_like(current):
            return True
        return False
    for item in scan_result.get("devices", []):
        ip = item.get("ip") or ""
        mac = (item.get("mac") or "").upper()
        scan_name = (item.get("hostname") or "").strip()
        name = scan_name or item.get("name") or ip or mac
        matched_by_mac = False
        dev = by_ip.get(ip)
        if not dev and mac:
            dev = by_mac.get(mac)
            matched_by_mac = dev is not None
        if not dev:
            dev = {
                "ip": ip,
                "mac": mac,
                "name": name,
                "type": "unknown",
                "last_seen": now,
                "last_ping": None,
                "last_mac": mac,
                "online": True,
                "last_module_update": now
            }
            devices.append(dev)
        else:
            dev["ip"] = ip or dev.get("ip")
            dev["mac"] = mac or dev.get("mac")
            if matched_by_mac:
                if scan_name:
                    dev["name"] = scan_name
                else:
                    dev["name"] = mac or name
            elif can_update_name(dev, name):
                dev["name"] = name
            dev["last_seen"] = now
            dev["online"] = True
            dev["last_module_update"] = now
    state["devices"] = devices
    return state


def ping_status_update(state: dict, base_dir: str, config: dict) -> dict:
    devices = state.get("devices") or []
    ips = [d.get("ip") for d in devices if d.get("ip")]
    if not ips:
        return state
    state["current_state"] = "pinging"
    alive = ping_sweep(ips)
    arp = read_arp_table()
    now = datetime.now().isoformat()
    for dev in devices:
        ip = dev.get("ip") or ""
        mac = (dev.get("mac") or "").upper()
        online = ip in alive
        if not online and mac:
            # L3 check: if MAC seen, resolve IP and ping again
            alt_ip = None
            for arp_ip, arp_mac in arp.items():
                if arp_mac.upper() == mac:
                    alt_ip = arp_ip
                    break
            if alt_ip:
                if ping_ip(alt_ip):
                    online = True
                    dev["ip"] = alt_ip
        dev["online"] = online
        dev["last_ping"] = now
        if mac:
            dev["last_mac"] = mac
    state["devices"] = devices
    return state


def _parse_ap_info(text: str) -> dict:
    info = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in ("name", "hostname"):
            info["hostname"] = value
        elif key in ("model", "product"):
            info["model"] = value
    return info


def _parse_cdp_text(output: str) -> dict:
    result = {}
    for line in output.splitlines():
        line = line.strip()
        if line.lower().startswith("device-id"):
            result["device_id"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("port-id"):
            result["port_id"] = line.split(":", 1)[1].strip()
        elif "platform" in line.lower():
            m = re.search(r"platform\\s*[:=]\\s*(.+)", line, re.I)
            if m:
                result["platform"] = m.group(1).strip()
        elif "ip address" in line.lower():
            m = re.search(r"ip address\\s*[:=]\\s*([0-9.]+)", line, re.I)
            if m:
                result["ip"] = m.group(1).strip()
        elif "native vlan" in line.lower():
            m = re.search(r"native vlan\\s*[:=]\\s*(\\d+)", line, re.I)
            if m:
                result["vlan"] = m.group(1).strip()
    return result


def _clean_cdp_field(value: str) -> str:
    if not value:
        return ""
    m = re.search(r"'([^']+)'", value)
    if m:
        return m.group(1).strip()
    m = re.search(r"([A-Za-z0-9_.-]+(?:/[0-9]+){0,3})", value)
    if m:
        return m.group(1).strip()
    return value.strip()


def extract_hex_blocks(output: str) -> list[bytes]:
    blocks = []
    current = bytearray()
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("0x"):
            if current:
                blocks.append(bytes(current))
                current = bytearray()
            continue
        try:
            hex_part = line.split(":", 1)[1].strip()
        except IndexError:
            continue
        hex_part = hex_part.split("  ")[0].strip().replace(" ", "")
        if len(hex_part) < 2:
            continue
        for i in range(0, len(hex_part), 2):
            chunk = hex_part[i:i+2]
            if len(chunk) == 2:
                current.append(int(chunk, 16))
    if current:
        blocks.append(bytes(current))
    return blocks


def parse_cdp(payload: bytes) -> dict:
    result = {}
    dest_pattern = b"\x01\x00\x0c\xcc\xcc\xcc"
    idx_dest = payload.find(dest_pattern)
    if idx_dest >= 6:
        src = payload[idx_dest - 6:idx_dest]
        result["switch_mac"] = ":".join(f"{b:02x}" for b in src)
    snap = b"\xaa\xaa\x03\x00\x00\x0c\x20\x00"
    idx = payload.find(snap)
    if idx == -1:
        return result
    cdp = payload[idx + len(snap):]
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
    return result


def _parse_pcap_raw(path: str) -> tuple[dict | None, dict]:
    stats = {"format": "pcap", "packets": 0}
    try:
        import struct
        with open(path, "rb") as f:
            header = f.read(24)
            if len(header) < 24:
                return None, stats
            magic = header[:4]
            if magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
                endian = ">"
            elif magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
                endian = "<"
            else:
                return None, stats
            while True:
                pkt_hdr = f.read(16)
                if len(pkt_hdr) < 16:
                    break
                _, _, incl_len, _ = struct.unpack(endian + "IIII", pkt_hdr)
                data = f.read(incl_len)
                if len(data) < incl_len:
                    break
                stats["packets"] += 1
                parsed = parse_cdp(data)
                if parsed and parsed.get("device_id"):
                    return parsed, stats
    except Exception:
        return None, stats
    return None, stats


def _parse_pcapng_raw(path: str) -> tuple[dict | None, dict]:
    stats = {"format": "pcapng", "packets": 0}
    try:
        import struct
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
                if btype == 0x00000006:  # Enhanced Packet Block
                    if len(body) < 28:
                        continue
                    cap_len = struct.unpack(endian + "I", body[12:16])[0]
                    packet_data = body[20:20 + cap_len]
                    stats["packets"] += 1
                    parsed = parse_cdp(packet_data)
                    if parsed and parsed.get("device_id"):
                        return parsed, stats
                elif btype == 0x00000003:  # Simple Packet Block
                    if len(body) < 8:
                        continue
                    packet_data = body[4:-4]
                    stats["packets"] += 1
                    parsed = parse_cdp(packet_data)
                    if parsed and parsed.get("device_id"):
                        return parsed, stats
    except Exception:
        return None, stats
    return None, stats


def parse_cdp_from_pcap(path: str) -> tuple[dict | None, dict]:
    parsed, stats = _parse_pcap_raw(path)
    if parsed:
        return parsed, stats
    parsed_ng, stats_ng = _parse_pcapng_raw(path)
    if parsed_ng:
        return parsed_ng, stats_ng
    return None, stats


def run_uniview_nvr_capture(job: dict, merged: dict, state: dict, base_dir: str) -> dict:
    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except Exception:
        log(base_dir, "Uniview NVR: requests not available")
        return {"status": "error", "message": "requests_required"}

    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    creds = (merged.get("credentials") or {}).get("uniview_nvr_capture") or {}
    username = (params.get("username") or creds.get("username") or "").strip()
    password = (params.get("password") or creds.get("password") or "")
    if not username or password is None:
        return {"status": "error", "message": "missing_credentials"}

    capture_seconds = int(params.get("capture_seconds") or 60)
    capture_window_seconds = int(params.get("capture_window_seconds") or 45)
    packet_size = int(params.get("packet_size") or 1500)
    nic_choice = (params.get("nic") or "NIC1").strip().upper()
    nic_name = "eth1" if nic_choice == "NIC2" else "eth0"

    targets = job.get("targets") if isinstance(job.get("targets"), list) else []
    if not targets:
        targets = [
            {"ip": d.get("ip"), "name": d.get("name"), "mac": d.get("mac")}
            for d in (state.get("devices") or [])
            if (d.get("type") or "").lower() == "nvr" and d.get("ip")
        ]
    if not targets:
        return {"status": "success", "message": "no_targets", "updated": 0}

    devices = state.get("devices") or []

    def find_or_create_switch(cdp: dict) -> dict | None:
        switch_ip = (cdp.get("ip") or "").strip()
        device_id = _clean_cdp_field((cdp.get("device_id") or "").strip())
        switch_mac = (cdp.get("switch_mac") or "").upper()
        if not switch_ip and not device_id and not switch_mac:
            return None
        for d in devices:
            if d.get("type") == "switch" and switch_ip and d.get("ip") == switch_ip:
                if device_id:
                    d["name"] = device_id
                if switch_mac and not d.get("mac"):
                    d["mac"] = switch_mac
                return d
            if d.get("type") == "switch" and switch_mac and (d.get("mac") or "").upper() == switch_mac:
                if device_id:
                    d["name"] = device_id
                if switch_ip:
                    d["ip"] = switch_ip
                return d
            if d.get("type") == "switch" and device_id and d.get("name") == device_id:
                if switch_ip and not d.get("ip"):
                    d["ip"] = switch_ip
                if switch_mac and not d.get("mac"):
                    d["mac"] = switch_mac
                return d
        new_switch = {
            "id": f"dev_{uuid.uuid4().hex[:8]}",
            "ip": switch_ip,
            "name": device_id or (f"Switch {switch_ip}" if switch_ip else "Switch"),
            "type": "switch",
            "last_seen": datetime.now().isoformat()
        }
        if switch_mac:
            new_switch["mac"] = switch_mac
        devices.append(new_switch)
        return new_switch

    def upsert_connection(dev: dict, remote_id: str, local_interface: str, remote_interface: str, remote_dev: dict | None = None) -> None:
        connections = dev.setdefault("connections", [])
        for conn in connections:
            if conn.get("remote_device") == remote_id and conn.get("protocol") == "cdp":
                conn["local_interface"] = local_interface or conn.get("local_interface", "")
                conn["remote_interface"] = remote_interface or conn.get("remote_interface", "")
                if remote_dev:
                    conn["remote_name"] = remote_dev.get("name") or conn.get("remote_name", "")
                    conn["remote_ip"] = remote_dev.get("ip") or conn.get("remote_ip", "")
                    conn["remote_mac"] = remote_dev.get("mac") or conn.get("remote_mac", "")
                conn["status"] = "up"
                conn["discovered_at"] = datetime.now().isoformat()
                return
        connections.append({
            "id": f"conn_{uuid.uuid4().hex[:8]}",
            "local_interface": local_interface or "",
            "remote_device": remote_id,
            "remote_name": (remote_dev.get("name") if remote_dev else "") or "",
            "remote_ip": (remote_dev.get("ip") if remote_dev else "") or "",
            "remote_mac": (remote_dev.get("mac") if remote_dev else "") or "",
            "remote_interface": remote_interface or "",
            "protocol": "cdp",
            "discovered_at": datetime.now().isoformat(),
            "status": "up"
        })

    updated = 0
    failures = 0
    log(base_dir, f"Uniview NVR: starting {len(targets)} targets (capture={capture_seconds}s size={packet_size})")
    for target in targets:
        ip = (target.get("ip") or "").strip()
        if not ip:
            continue
        name = target.get("name") or ip
        base = f"http://{ip}"
        session = requests.Session()
        auth = HTTPDigestAuth(username, password)
        login_url = base + "/LAPI/V1.0/System/Security/Login"
        try:
            login_resp = session.put(login_url, auth=auth, timeout=10)
        except Exception:
            failures += 1
            continue
        if login_resp.status_code not in (200, 204):
            failures += 1
            continue
        start_urls = [
            base + "/LAPI/V1.1/Network/PacketCapture/Start",
            base + "/LAPI/V1.0/Network/PacketCapture/Start"
        ]
        stop_url = base + "/LAPI/V1.0/Network/PacketCapture/Stop"
        download_url = base + "/LAPI/V1.0/Network/PacketCapture/File/DownLoad"
        start_payload = {
            "PacketSize": packet_size,
            "PortMode": 0,
            "IPMode": 0,
            "NicName": nic_name
        }
        started = False
        for url in start_urls:
            try:
                resp = session.put(url, data=json.dumps(start_payload) + "\r\n", headers={"Content-Type": "text/plain;charset=UTF-8"}, auth=auth, timeout=20)
                if resp.status_code in (200, 204):
                    started = True
                    break
            except Exception:
                continue
        if not started:
            failures += 1
            continue
        time.sleep(max(1, capture_window_seconds))
        try:
            session.put(stop_url, auth=auth, timeout=10)
        except Exception:
            pass
        try:
            download_resp = session.get(download_url, auth=auth, timeout=30)
        except Exception:
            failures += 1
            continue
        if download_resp.status_code != 200 or not download_resp.content:
            failures += 1
            continue
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_dir = os.path.join(base_dir, "captures")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"nvr_capture_{ip.replace('.', '_')}_{ts}.pcap")
        with open(tmp_path, "wb") as f:
            f.write(download_resp.content)
        cdp, _stats = parse_cdp_from_pcap(tmp_path)
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        if not cdp:
            failures += 1
            continue
        now = datetime.now().isoformat()
        mac = (target.get("mac") or "").upper()
        dev = None
        for d in devices:
            if (d.get("mac") or "").upper() == mac and mac:
                dev = d
                break
            if d.get("ip") == ip:
                dev = d
                break
        if not dev:
            dev = {"ip": ip, "mac": mac, "name": name, "type": "nvr"}
            devices.append(dev)
        dev["type"] = "nvr"
        dev["name"] = name or dev.get("name") or ip
        dev["parent_switch_name"] = _clean_cdp_field(cdp.get("device_id") or "") or dev.get("parent_switch_name")
        dev["parent_switch_ip"] = cdp.get("ip") or dev.get("parent_switch_ip")
        dev["parent_switch_port"] = _clean_cdp_field(cdp.get("port_id") or "") or dev.get("parent_switch_port")
        if cdp.get("vlan"):
            dev["vlan"] = cdp.get("vlan")
        dev["last_module_update"] = now
        switch_dev = find_or_create_switch(cdp)
        if switch_dev:
            upsert_connection(dev, switch_dev.get("id"), nic_name, _clean_cdp_field(cdp.get("port_id") or ""), remote_dev=switch_dev)
        updated += 1

    state["devices"] = devices
    return {"status": "success", "updated": updated, "failures": failures}


def run_ubiquiti_cdp(job: dict, merged: dict, state: dict, base_dir: str) -> dict:
    try:
        import paramiko
    except Exception as exc:
        log(base_dir, "Ubiquiti CDP: paramiko not available")
        return {"status": "error", "message": "paramiko_required"}

    creds = (merged.get("credentials") or {}).get("ubiquiti") or (merged.get("credentials") or {}).get("ubiquiti_cdp_reader") or {}
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    username = (params.get("username") or creds.get("username") or "").strip()
    password = (params.get("password") or creds.get("password") or "")
    if not username or password is None:
        log(base_dir, "Ubiquiti CDP: missing credentials")
        return {"status": "error", "message": "missing_credentials"}

    interface = params.get("interface") or "eth0"
    capture_seconds = int(params.get("capture_seconds") or 60)
    packet_size = int(params.get("packet_size") or params.get("batch_size") or 1500)
    concurrency = int(params.get("concurrency") or 3)
    if concurrency < 1:
        concurrency = 1

    targets = job.get("targets") if isinstance(job.get("targets"), list) else []
    if not targets:
        # fallback to state devices marked as ap
        targets = [
            {"ip": d.get("ip"), "name": d.get("name"), "mac": d.get("mac")}
            for d in (state.get("devices") or [])
            if (d.get("type") or "").lower() == "ap" and d.get("ip")
        ]
    if not targets:
        return {"status": "success", "message": "no_targets", "updated": 0}

    log(base_dir, f"Ubiquiti CDP: starting {len(targets)} targets (capture={capture_seconds}s size={packet_size})")

    cmd = (
        "sh -c '"
        "tmp=/tmp/cdp_capture_$$.log; "
        "echo START $(date) > $tmp; "
        f"tcpdump -i {interface} -nn -v -s {packet_size} ether dst 01:00:0c:cc:cc:cc >> $tmp 2>&1 & "
        "pid=$!; "
        f"end=$(( $(date +%s) + {capture_seconds} )); "
        "while [ $(date +%s) -lt $end ]; do sleep 1; done; "
        "kill -INT $pid >/dev/null 2>&1; "
        "wait $pid >/dev/null 2>&1; "
        "echo END $(date) >> $tmp; "
        "cat $tmp; rm -f $tmp'"
    )

    updated = 0
    failures = 0
    failure_details = []
    devices = state.get("devices") or []

    def find_or_create_switch(cdp: dict) -> dict | None:
        switch_ip = (cdp.get("ip") or "").strip()
        device_id = _clean_cdp_field((cdp.get("device_id") or "").strip())
        switch_mac = (cdp.get("switch_mac") or "").upper()
        if not switch_ip and not device_id and not switch_mac:
            return None
        for d in devices:
            if d.get("type") == "switch" and switch_ip and d.get("ip") == switch_ip:
                if device_id:
                    d["name"] = device_id
                if switch_mac and not d.get("mac"):
                    d["mac"] = switch_mac
                if cdp.get("platform"):
                    d["platform"] = cdp.get("platform")
                if cdp.get("vendor"):
                    d["vendor"] = cdp.get("vendor")
                return d
            if d.get("type") == "switch" and switch_mac and (d.get("mac") or "").upper() == switch_mac:
                if device_id:
                    d["name"] = device_id
                if switch_ip:
                    d["ip"] = switch_ip
                if cdp.get("platform"):
                    d["platform"] = cdp.get("platform")
                if cdp.get("vendor"):
                    d["vendor"] = cdp.get("vendor")
                return d
            if d.get("type") == "switch" and device_id and d.get("name") == device_id:
                if switch_ip and not d.get("ip"):
                    d["ip"] = switch_ip
                if switch_mac and not d.get("mac"):
                    d["mac"] = switch_mac
                if cdp.get("platform"):
                    d["platform"] = cdp.get("platform")
                if cdp.get("vendor"):
                    d["vendor"] = cdp.get("vendor")
                return d
        new_switch = {
            "id": f"dev_{uuid.uuid4().hex[:8]}",
            "ip": switch_ip,
            "name": device_id or (f"Switch {switch_ip}" if switch_ip else "Switch"),
            "type": "switch",
            "platform": cdp.get("platform") or "",
            "vendor": "cisco" if "cisco" in (cdp.get("platform") or "").lower() else "",
            "last_seen": datetime.now().isoformat()
        }
        if switch_mac:
            new_switch["mac"] = switch_mac
        devices.append(new_switch)
        return new_switch

    def upsert_connection(dev: dict, remote_id: str, local_interface: str, remote_interface: str, remote_dev: dict | None = None) -> None:
        connections = dev.setdefault("connections", [])
        for conn in connections:
            if conn.get("remote_device") == remote_id and conn.get("protocol") == "cdp":
                conn["local_interface"] = local_interface or conn.get("local_interface", "")
                conn["remote_interface"] = remote_interface or conn.get("remote_interface", "")
                if remote_dev:
                    conn["remote_name"] = remote_dev.get("name") or conn.get("remote_name", "")
                    conn["remote_ip"] = remote_dev.get("ip") or conn.get("remote_ip", "")
                conn["status"] = "up"
                conn["discovered_at"] = datetime.now().isoformat()
                return
        connections.append({
            "id": f"conn_{uuid.uuid4().hex[:8]}",
            "local_interface": local_interface or "",
            "remote_device": remote_id,
            "remote_name": (remote_dev.get("name") if remote_dev else "") or "",
            "remote_ip": (remote_dev.get("ip") if remote_dev else "") or "",
            "remote_interface": remote_interface or "",
            "protocol": "cdp",
            "discovered_at": datetime.now().isoformat(),
            "status": "up"
        })

    def process_target(target: dict) -> dict:
        host = (target.get("ip") or "").strip()
        if not host:
            return {"status": "skip", "ip": host, "error": "no_ip"}
        try:
            log(base_dir, f"Ubiquiti CDP: connecting {host}")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(host, username=username, password=password, timeout=10, banner_timeout=10, auth_timeout=10)
            ap_info = {}
            for info_cmd in ("info", "mca-cli-op info", "ubnt-device-info", "/usr/bin/ubnt-device-info"):
                _, stdout, stderr = client.exec_command(info_cmd, timeout=10)
                info_text = stdout.read().decode(errors="ignore") + "\n" + stderr.read().decode(errors="ignore")
                ap_info = _parse_ap_info(info_text)
                if ap_info:
                    break
            _, stdout, stderr = client.exec_command(cmd, timeout=capture_seconds + 10)
            output = stdout.read().decode(errors="ignore") + "\n" + stderr.read().decode(errors="ignore")
            client.close()
        except Exception:
            return {"status": "error", "ip": host, "error": "ssh_failed"}

        log(base_dir, f"Ubiquiti CDP RAW {host}:")
        for line in output.splitlines()[:200]:
            log(base_dir, line)

        if "pid CDP" not in output and "CDPv" not in output:
            return {"status": "error", "ip": host, "error": "no_cdp_packet"}
        blocks = extract_hex_blocks(output)
        cdp = {}
        for block in blocks:
            cdp = parse_cdp(block)
            if cdp:
                break
        if not cdp:
            cdp = _parse_cdp_text(output)
        if not cdp:
            return {"status": "error", "ip": host, "error": "cdp_not_found"}
        for line in output.splitlines():
            m = re.search(r"^([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s*>\s*01:00:0c:cc:cc:cc", line.strip(), re.I)
            if m:
                cdp["switch_mac"] = m.group(1).upper()
                break
        return {"status": "ok", "ip": host, "cdp": cdp, "ap_info": ap_info, "target": target}

    parsed_rows = []
    with ThreadPoolExecutor(max_workers=min(concurrency, len(targets))) as pool:
        futures = [pool.submit(process_target, t) for t in targets]
        for future in as_completed(futures):
            result = future.result()
            if result.get("status") != "ok":
                if result.get("error") not in ("no_ip",):
                    failures += 1
                    failure_details.append({"ip": result.get("ip"), "error": result.get("error")})
                continue
            cdp = result.get("cdp") or {}
            ap_info = result.get("ap_info") or {}
            target = result.get("target") or {}
            host = result.get("ip")
            now = datetime.now().isoformat()
            mac = (target.get("mac") or "").upper()
            dev = None
            for d in devices:
                if (d.get("mac") or "").upper() == mac and mac:
                    dev = d
                    break
                if d.get("ip") == host:
                    dev = d
                    break
            if not dev:
                dev = {"ip": host, "mac": mac, "name": target.get("name") or host, "type": "ap"}
                devices.append(dev)
            dev["type"] = "ap"
            if ap_info.get("hostname"):
                dev["name"] = ap_info.get("hostname")
            dev["parent_switch_name"] = _clean_cdp_field(cdp.get("device_id") or "") or dev.get("parent_switch_name")
            dev["parent_switch_ip"] = cdp.get("ip") or dev.get("parent_switch_ip")
            dev["parent_switch_port"] = _clean_cdp_field(cdp.get("port_id") or "") or dev.get("parent_switch_port")
            dev["parent_switch_platform"] = cdp.get("platform") or dev.get("parent_switch_platform")
            if cdp.get("vlan"):
                dev["vlan"] = cdp.get("vlan")
            dev["last_module_update"] = now
            switch_dev = find_or_create_switch(cdp)
            if switch_dev:
                upsert_connection(dev, switch_dev.get("id"), interface, _clean_cdp_field(cdp.get("port_id") or ""), remote_dev=switch_dev)
            updated += 1
            parsed_rows.append({
                "device": dev.get("name") or dev.get("ip") or "",
                "ip": dev.get("ip") or "",
                "switch": _clean_cdp_field(cdp.get("device_id") or ""),
                "switch_ip": cdp.get("ip") or "",
                "port": _clean_cdp_field(cdp.get("port_id") or ""),
                "vlan": cdp.get("vlan") or ""
            })

    state["devices"] = devices

    if parsed_rows:
        log(base_dir, "Parsed CDP Summary:")
        log(base_dir, "Device\tIP\tSwitch\tSwitchIP\tPort\tVLAN")
        log(base_dir, "-" * 80)
        for row in parsed_rows:
            log(base_dir, f"{row['device']}\t{row['ip']}\t{row['switch']}\t{row['switch_ip']}\t{row['port']}\t{row['vlan']}")

    return {"status": "success", "updated": updated, "failures": failures, "failure_details": failure_details}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(base_dir, config_path)
    config_path = auto_find_config(base_dir, os.path.basename(config_path))
    local_config = load_config(config_path)
    if not local_config:
        log(base_dir, f"Missing config: {config_path}")
        return

    agent_id = local_config.get("agent_id") or local_config.get("id")
    token = local_config.get("token")
    server_url = resolve_server_url(local_config.get("server_url") or "")
    if not agent_id or not token or not server_url:
        log(base_dir, "Missing agent_id/token/server_url")
        return

    scan_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), "scans")
    last_run = None
    identity = get_local_identity(server_url)
    log(base_dir, f"Loaded config for agent {agent_id}. Server={server_url}")
    if identity.get("name") or identity.get("ip") or identity.get("mac"):
        log(base_dir, f"Agent identity name={identity.get('name')} ip={identity.get('ip')} mac={identity.get('mac')}")

    state = load_state(base_dir)

    try:
        while True:
            try:
                server_cfg = poll_config(server_url, agent_id, token)
            except Exception:
                server_cfg = {}

            merged = {**local_config, **server_cfg}
            if not merged.get("enabled", True):
                log(base_dir, "Agent disabled. Waiting...")
                time.sleep(10)
                continue

            run_now = bool(merged.get("run_now")) and merged.get("allow_on_demand", True)
            interval_min = int(merged.get("interval_min") or 0)
            allow_interval = bool(merged.get("allow_interval", True))
            ip_scan_min = int(merged.get("ip_scan_min") or 10)
            ping_min = int(merged.get("ping_min") or 2)
            trust_mode = (merged.get("trust_mode") or "augment").strip().lower()
            if trust_mode not in ("augment", "replace"):
                trust_mode = "augment"

            detected_ranges = get_local_network_ranges()

            queued_modules = merged.get("queued_modules") if isinstance(merged.get("queued_modules"), list) else []
            scan_override = None
            if queued_modules:
                log(base_dir, f"Queued modules: {queued_modules}")
                results = {}
                run_scan_for_module = False
                module_changed = False
                for job in queued_modules:
                    if isinstance(job, str):
                        job = {"id": job}
                    module_id = job.get("id")
                    if module_id == "agent_ip_scan":
                        run_scan_for_module = True
                        results[module_id] = {"status": "queued"}
                        continue
                    if module_id == "ubiquiti_cdp_reader":
                        state["current_state"] = "ubiquiti_cdp"
                        results[module_id] = run_ubiquiti_cdp(job, merged, state, base_dir)
                        module_changed = True
                        state["current_state"] = "done"
                        save_state(base_dir, state)
                    elif module_id == "uniview_nvr_capture":
                        state["current_state"] = "uniview_nvr_capture"
                        results[module_id] = run_uniview_nvr_capture(job, merged, state, base_dir)
                        module_changed = True
                        state["current_state"] = "done"
                        save_state(base_dir, state)
                    else:
                        results[module_id or "unknown"] = {"status": "not_implemented"}
                if run_scan_for_module:
                    log(base_dir, f"Starting scan for range {merged.get('target_range')}")
                    def set_state(value):
                        state["current_state"] = value
                    scan_override = run_scan(merged, scan_dir, base_dir=base_dir, set_state=set_state)
                    if not scan_override.get("csv_path"):
                        log(base_dir, "No IPs in range. Nothing to scan.")
                    else:
                        log(base_dir, f"Scan complete. Devices={len(scan_override['devices'])}. CSV={scan_override['csv_path']}")
                    state = update_state_from_scan(state, scan_override)
                    state["current_state"] = "done"
                    now_dt = datetime.now()
                    sched = state.get("config") or {}
                    sched["next_ip_scan"] = (now_dt + timedelta(minutes=ip_scan_min)).isoformat()
                    state["config"] = sched
                    save_state(base_dir, state)
                    results["agent_ip_scan"] = {"status": "success", "devices": len(scan_override.get("devices") or [])}
                    module_changed = True
                if module_changed and not scan_override:
                    snapshot = write_state_snapshot(merged, state, scan_dir)
                    scan_override = {"scan_time": snapshot["scan_time"]}
                payload = {
                    "agent_id": agent_id,
                    "token": token,
                    "site": merged.get("site"),
                    "scan_time": (scan_override or {}).get("scan_time") or datetime.now().isoformat(),
                    "devices": state.get("devices", []),
                    "mode": "full_sync" if (scan_override or module_changed) else "module_result",
                    "trust_mode": trust_mode,
                    "module_results": results,
                    "agent_state": state.get("current_state") or "module",
                    "agent_device": identity,
                    "network_ranges": detected_ranges
                }
                try:
                    status, body = post_report(server_url, token, payload)
                    log(base_dir, f"Module status posted. Status={status}")
                    if body:
                        log(base_dir, f"Server response: {body}")
                except Exception:
                    log(base_dir, "Module status post failed.")
            should_interval = False

            if args.once:
                run_now = True

            now_dt = datetime.now()
            sched = state.get("config") or {}
            if not sched.get("next_ip_scan"):
                sched["next_ip_scan"] = (now_dt + timedelta(minutes=ip_scan_min)).isoformat()
            if not sched.get("next_ping"):
                sched["next_ping"] = (now_dt + timedelta(minutes=ping_min)).isoformat()

            # ping job
            try:
                next_ping = datetime.fromisoformat(sched.get("next_ping"))
            except Exception:
                next_ping = now_dt
            if now_dt >= next_ping:
                log(base_dir, "Running ping status update")
                state["current_state"] = "pinging"
                state = ping_status_update(state, base_dir, merged)
                state["current_state"] = "idle"
                sched["next_ping"] = (now_dt + timedelta(minutes=ping_min)).isoformat()
                state["config"] = sched
                save_state(base_dir, state)
                payload = {
                    "agent_id": agent_id,
                    "token": token,
                    "site": merged.get("site"),
                    "scan_time": datetime.now().isoformat(),
                    "devices": state.get("devices", []),
                    "mode": "ping_update",
                    "trust_mode": trust_mode,
                    "agent_state": state.get("current_state") or "pinging",
                    "agent_device": identity,
                    "network_ranges": detected_ranges
                }
                try:
                    status, body = post_report(server_url, token, payload)
                    log(base_dir, f"Ping update posted. Status={status}")
                    if body:
                        log(base_dir, f"Server response: {body}")
                except Exception:
                    log(base_dir, "Ping update failed.")

            # ip scan job
            try:
                next_scan = datetime.fromisoformat(sched.get("next_ip_scan"))
            except Exception:
                next_scan = now_dt
            if allow_interval and now_dt >= next_scan:
                should_interval = True
            run_scan_now = run_now or should_interval
            if run_scan_now and not scan_override:
                log(base_dir, f"Starting scan for range {merged.get('target_range')}")
                def set_state(value):
                    state["current_state"] = value
                result = run_scan(merged, scan_dir, base_dir=base_dir, set_state=set_state)
                if not result.get("csv_path"):
                    log(base_dir, "No IPs in range. Nothing to scan.")
                else:
                    log(base_dir, f"Scan complete. Devices={len(result['devices'])}. CSV={result['csv_path']}")
                state = update_state_from_scan(state, result)
                state["current_state"] = "done"
                sched["next_ip_scan"] = (now_dt + timedelta(minutes=ip_scan_min)).isoformat()
                state["config"] = sched
                save_state(base_dir, state)
                payload = {
                    "agent_id": agent_id,
                    "token": token,
                    "site": merged.get("site"),
                    "scan_time": result["scan_time"],
                    "devices": state.get("devices", []),
                    "mode": "full_sync",
                    "trust_mode": trust_mode,
                    "agent_state": state.get("current_state"),
                    "agent_device": identity,
                    "network_ranges": detected_ranges
                }
                try:
                    status, body = post_report(server_url, token, payload)
                    log(base_dir, f"Report posted successfully. Status={status}")
                    if body:
                        log(base_dir, f"Server response: {body}")
                except HTTPError as exc:
                    try:
                        body = exc.read().decode("utf-8", errors="ignore")
                    except Exception:
                        body = ""
                    log(base_dir, f"Report post failed. Status={exc.code}. {body}")
                except Exception as exc:
                    log(base_dir, f"Report post failed. {exc}")
                last_run = datetime.now()
            else:
                log(base_dir, "Scan not due; waiting.")

            if args.once:
                break

            time.sleep(10)
    except KeyboardInterrupt:
        log(base_dir, "Stopped.")


if __name__ == "__main__":
    main()
