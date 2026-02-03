#!/usr/bin/env python3
"""
Ubiquiti CDP Reader Module

Runs tcpdump on Ubiquiti devices, parses CDP TLVs, and updates device
with parent switch name/IP/port and VLAN. Overrides fields because CDP is authoritative.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, Optional, Tuple


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def normalize_mac(mac: str) -> str:
    mac = mac.strip().replace("-", ":").replace(".", "")
    if len(mac) == 12:
        mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
    return mac.upper()


def run_paramiko_cmd(host: str, username: str, password: str, cmd: str, timeout: int) -> Tuple[int, str, str]:
    try:
        import paramiko
    except Exception as exc:
        raise RuntimeError("paramiko is required for SSH") from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=username,
        password=password,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10
    )
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out_text = stdout.read().decode(errors="ignore")
    err_text = stderr.read().decode(errors="ignore")
    exit_status = stdout.channel.recv_exit_status()
    client.close()
    return exit_status, out_text, err_text


def _append_log(path: Optional[str], message: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def extract_hex_blocks(output: str) -> list[bytes]:
    """
    Extract all hex dump blocks (0x0000: lines) into raw byte arrays.
    """
    blocks: list[bytes] = []
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
        hex_part = hex_part.split("  ")[0].strip()
        hex_part = hex_part.replace(" ", "")
        if len(hex_part) < 2:
            continue
        for i in range(0, len(hex_part), 2):
            chunk = hex_part[i:i+2]
            if len(chunk) == 2:
                current.append(int(chunk, 16))
    if current:
        blocks.append(bytes(current))
    return blocks


def parse_cdp(payload: bytes) -> Dict[str, Any]:
    """
    Parse CDP TLVs from payload, return device_id, port_id, platform, vlan, ip.
    """
    result: Dict[str, Any] = {}
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


def short_port(port: Optional[str]) -> str:
    if not port:
        return ""
    match = re.search(r"(\d+/\d+/\d+)$", port)
    if match:
        return match.group(1)
    match = re.search(r"(\d+/\d+)$", port)
    if match:
        return match.group(1)
    return port


def is_ubiquiti_device(device: Dict[str, Any]) -> bool:
    vendor = (device.get("vendor") or "").lower()
    dtype = (device.get("type") or "").lower()
    platform = (device.get("platform") or "").lower()
    return "ubiquiti" in vendor or "ubiquiti" in platform or dtype == "ap"


def _is_mac_like(value: str) -> bool:
    if not value:
        return False
    cleaned = re.sub(r"[^0-9a-fA-F]", "", value)
    return len(cleaned) == 12


def _switch_name_from_cdp(cdp: Dict[str, Any]) -> str:
    device_id = (cdp.get("device_id") or "").strip()
    if device_id and not _is_mac_like(device_id):
        return device_id
    ip = cdp.get("ip") or ""
    if ip:
        return f"Switch {ip}"
    return "Switch"


def _vendor_from_platform(platform: str) -> str:
    if "cisco" in platform.lower():
        return "cisco"
    return ""


def _parse_ap_info(output: str) -> Dict[str, str]:
    info: Dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "hostname":
            info["hostname"] = value
        elif key == "model":
            info["model"] = value
        elif key == "mac address":
            info["mac"] = value
        elif key == "ip address":
            info["ip"] = value
    return info


def _find_or_create_switch(
    data: Dict[str, Any],
    site_name: str,
    cdp: Dict[str, Any],
    now: str,
    override_existing: bool
) -> Optional[Dict[str, Any]]:
    switch_ip = cdp.get("ip")
    switch_name = _switch_name_from_cdp(cdp)
    if not switch_ip and not switch_name:
        return None
    for device in data.get("devices", []):
        if device.get("site") != site_name:
            continue
        if switch_ip and device.get("ip") == switch_ip:
            if override_existing:
                device["name"] = switch_name or device.get("name")
                if switch_ip:
                    device["ip"] = switch_ip
                if cdp.get("platform"):
                    device["platform"] = cdp.get("platform")
                    device["vendor"] = _vendor_from_platform(cdp.get("platform") or "")
                device["last_modified"] = now
            return device
        if switch_name and (device.get("name") == switch_name or device.get("id") == switch_name):
            if override_existing:
                if switch_ip:
                    device["ip"] = switch_ip
                if cdp.get("platform"):
                    device["platform"] = cdp.get("platform")
                    device["vendor"] = _vendor_from_platform(cdp.get("platform") or "")
                device["last_modified"] = now
            return device
    platform = cdp.get("platform") or ""
    vendor = _vendor_from_platform(platform)
    new_device = {
        "id": f"dev_{uuid.uuid4().hex[:8]}",
        "site": site_name,
        "name": switch_name,
        "ip": switch_ip or "",
        "type": "switch",
        "model": "",
        "platform": platform,
        "vendor": vendor,
        "os": "",
        "discovered_by": "ubiquiti_cdp_reader",
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
        "notes": "Placeholder from CDP capture"
    }
    data.setdefault("devices", []).append(new_device)
    return new_device


def _upsert_connection(
    device: Dict[str, Any],
    remote_device_id: str,
    local_interface: str,
    remote_interface: str,
    now: str
) -> None:
    connections = device.setdefault("connections", [])
    for conn in connections:
        if conn.get("remote_device") == remote_device_id and conn.get("protocol") == "cdp":
            if remote_interface and conn.get("remote_interface") == remote_interface:
                conn["local_interface"] = local_interface or conn.get("local_interface", "")
                conn["status"] = "up"
                conn["discovered_at"] = now
                return
    connections.append({
        "id": f"conn_{uuid.uuid4().hex[:8]}",
        "local_interface": local_interface or "",
        "remote_device": remote_device_id,
        "remote_interface": remote_interface or "",
        "protocol": "cdp",
        "discovered_at": now,
        "status": "up"
    })


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_json(sys.argv[1])
    params = config.get("parameters", {})
    db_path = config.get("database_path")
    site_name = config.get("site_name")
    log_file = config.get("log_file")

    username = params.get("username")
    password = params.get("password")
    interface = params.get("interface") or "eth0"
    capture_seconds = int(params.get("capture_seconds", 12) or 12)
    batch_size = int(params.get("batch_size", 30) or 30)
    trace_output = str(params.get("trace_output", "false")).strip().lower() in ("1", "true", "yes")
    override_existing = str(params.get("override_existing_switch", "false")).strip().lower() in ("1", "true", "yes")
    targets = params.get("targets") or {}
    device_ids = targets.get("device_ids") or []
    manual_devices = targets.get("manual_devices") or []

    if not db_path or not site_name:
        print(json.dumps({"status": "error", "message": "Missing database_path or site_name"}))
        return
    if not username or password is None:
        print(json.dumps({"status": "error", "message": "Missing username/password"}))
        return

    try:
        data = load_json(db_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to read database: {e}"}))
        return

    now = datetime.now().isoformat()
    devices = [d for d in data.get("devices", []) if d.get("site") == site_name and d.get("ip")]
    if device_ids:
        devices = [d for d in devices if d.get("id") in set(device_ids)]

    if manual_devices:
        existing_by_ip = {d.get("ip"): d for d in data.get("devices", []) if d.get("site") == site_name}
        for entry in manual_devices:
            name = (entry.get("name") or "").strip()
            ip = (entry.get("ip") or "").strip()
            if not ip:
                continue
            if ip in existing_by_ip:
                if name:
                    existing_by_ip[ip]["name"] = name
                if existing_by_ip[ip] not in devices:
                    devices.append(existing_by_ip[ip])
                continue
            placeholder = {
                "id": f"dev_{os.urandom(4).hex()}",
                "site": site_name,
                "name": name or ip,
                "ip": ip,
                "type": "ap",
                "model": "",
                "platform": "",
                "vendor": "",
                "os": "",
                "discovered_by": "ubiquiti_cdp_reader",
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
                "notes": "Manual entry from CDP module"
            }
            data.setdefault("devices", []).append(placeholder)
            devices.append(placeholder)

    updated = 0
    if not devices:
        print(json.dumps({"status": "error", "message": "No target devices selected"}))
        return

    cmd = (
        "sh -c '"
        "tmp=/tmp/cdp_capture_$$.log; "
        "echo START $(date) > $tmp; "
        f"tcpdump -i {interface} -nn -v -s 1500 ether dst 01:00:0c:cc:cc:cc >> $tmp 2>&1 & "
        "pid=$!; "
        f"end=$(( $(date +%s) + {capture_seconds} )); "
        "while [ $(date +%s) -lt $end ]; do sleep 1; done; "
        "kill -INT $pid >/dev/null 2>&1; "
        "wait $pid >/dev/null 2>&1; "
        "echo END $(date) >> $tmp; "
        "cat $tmp; rm -f $tmp'"
    )

    def process_device(dev: Dict[str, Any]) -> Tuple[str, Optional[str], bool, str]:
        host = dev.get("ip")
        name = dev.get("name") or dev.get("id") or host or "unknown"
        if not host:
            return name, None, False, "missing_ip"
        try:
            info_text = ""
            ap_info = {}
            for info_cmd in ("info", "mca-cli-op info", "ubnt-device-info", "/usr/bin/ubnt-device-info"):
                info_code, info_out, info_err = run_paramiko_cmd(
                    host, username, password, info_cmd, timeout=10
                )
                info_text = info_out + "\n" + info_err
                ap_info = _parse_ap_info(info_text)
                if ap_info:
                    break
            if trace_output and info_text:
                _append_log(log_file, f"INFO {host}:\n{info_text[:1200]}")
            code, out, err = run_paramiko_cmd(host, username, password, cmd, timeout=capture_seconds + 5)
        except Exception:
            return name, host, False, "ssh_failed"
        hostname = ap_info.get("hostname")
        if hostname:
            current_name = (dev.get("name") or "").strip()
            name_lower = current_name.lower()
            vendor_lower = (dev.get("vendor") or "").strip().lower()
            platform_lower = (dev.get("platform") or "").strip().lower()
            placeholder_names = {"apunifi", "ubiquiti", "unifiap", "ap", "apubiquiti"}
            if (not current_name or _is_mac_like(current_name) or current_name == host
                    or name_lower in placeholder_names
                    or (vendor_lower and name_lower == vendor_lower)
                    or (platform_lower and name_lower == platform_lower)):
                dev["name"] = hostname
        model = ap_info.get("model")
        if model:
            dev["model"] = model
        output = out + "\n" + err
        if trace_output:
            _append_log(log_file, f"RAW {host}:\n{output[:2000]}")
        if "pid CDP" not in output:
            if trace_output:
                _append_log(log_file, f"PARSE FAIL {host} ({name}): no CDP packet")
            return name, host, False, "no_cdp_packet"

        blocks = extract_hex_blocks(output)
        if not blocks:
            if trace_output:
                _append_log(log_file, f"PARSE FAIL {host} ({name}): no hex payload")
            return name, host, False, "no_hex"
        cdp = None
        for payload in blocks:
            parsed = parse_cdp(payload)
            if parsed:
                cdp = parsed
                break
        if not cdp:
            if trace_output:
                _append_log(log_file, f"PARSE FAIL {host} ({name}): no CDP TLVs")
            return name, host, False, "no_cdp_tlv"

        dev["parent_switch_name"] = cdp.get("device_id") or dev.get("parent_switch_name")
        dev["parent_switch_ip"] = cdp.get("ip") or dev.get("parent_switch_ip")
        port_id = cdp.get("port_id") or dev.get("parent_switch_port")
        dev["parent_switch_port"] = port_id
        if "vlan" in cdp:
            dev["vlan"] = str(cdp["vlan"])
        if cdp.get("platform"):
            dev["parent_switch_platform"] = cdp.get("platform")
        switch_device = _find_or_create_switch(data, site_name, cdp, now, override_existing)
        if switch_device:
            _upsert_connection(dev, switch_device["id"], interface, port_id or "", now)
        dev["last_modified"] = now
        if trace_output:
            _append_log(log_file, f"PARSED {host}: {cdp}")
        else:
            port_display = short_port(port_id)
            _append_log(
                log_file,
                f"FOUND {name} ({host}) -> switch={cdp.get('device_id')} port={port_display} vlan={cdp.get('vlan')}"
            )
        return name, host, True, "ok"

    total = len(devices)
    _append_log(log_file, f"Starting CDP capture for {total} devices (batch size {batch_size}).")
    ok_list = []
    fail_list = []
    for start in range(0, total, batch_size):
        batch = devices[start:start + batch_size]
        _append_log(log_file, f"Batch {start + 1}-{start + len(batch)} starting...")
        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            future_map = {pool.submit(process_device, dev): dev for dev in batch}
            for future in as_completed(future_map):
                name, host, ok, reason = future.result()
                if ok:
                    updated += 1
                    _append_log(log_file, f"OK: {name}")
                    ok_list.append((name, host))
                else:
                    label = f"{name} ({host})" if host else name
                    _append_log(log_file, f"FAIL: {label} ({reason})")
                    fail_list.append((name, host, reason))
        _append_log(log_file, f"Batch {start + 1}-{start + len(batch)} complete.")

    if ok_list:
        _append_log(log_file, "Success summary:")
        for name, host in ok_list:
            label = f"{name} ({host})" if host else name
            _append_log(log_file, f"  OK: {label}")
        _append_log(log_file, f"Updated devices: {updated}")
    if fail_list:
        _append_log(log_file, "Failure summary:")
        for name, host, reason in fail_list:
            label = f"{name} ({host})" if host else name
            _append_log(log_file, f"  FAIL: {label} ({reason})")

    data.setdefault("meta", {})["last_modified"] = now
    try:
        save_json(db_path, data)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to write database: {e}"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "updated": updated
    }))


if __name__ == "__main__":
    main()
