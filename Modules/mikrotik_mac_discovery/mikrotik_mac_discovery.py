#!/usr/bin/env python3
"""
MikroTik MAC Discovery

Uses RouterOS `/tool/ip/scan` (or `/tool/mac-scan` fallback) via SSH to gather IP/MAC/identity.
Maps vendor using local OUI ranges file (range-based).
Outputs device list to be merged into devices.db by the backend module runner.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
OUI_FILE = os.path.join(MODULE_DIR, "oui_ranges.txt")


@dataclass
class DeviceRecord:
    mac: str
    ip: Optional[str]
    name: str
    iface: Optional[str]
    vendor: Optional[str]
    oui: Optional[str]
    note: Optional[str]

    def to_output(self) -> Dict[str, Any]:
        return {
            "id": self.mac.lower(),
            "name": self.name,
            "ip": self.ip,
            "mac": self.mac.lower(),
            "vendor": self.vendor,
            "oui": self.oui,
            "interface": self.iface,
            "notes": self.note,
        }


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_mac(mac: str) -> str:
    mac = mac.strip().replace("-", ":").replace(".", "")
    if len(mac) == 12:
        mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
    return mac.upper()


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def load_oui_ranges(path: str) -> List[Tuple[int, int, str]]:
    ranges: List[Tuple[int, int, str]] = []
    if not os.path.exists(path):
        return ranges
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line or "-" not in line:
                continue
            try:
                left, vendor = line.split("=", 1)
                start_str, end_str = left.split("-", 1)
                start = mac_to_int(normalize_mac(start_str))
                end = mac_to_int(normalize_mac(end_str))
                ranges.append((start, end, vendor.strip()))
            except Exception:
                continue
    return ranges


def lookup_vendor(mac: str, ranges: List[Tuple[int, int, str]]) -> Optional[str]:
    try:
        mac_int = mac_to_int(normalize_mac(mac))
    except Exception:
        return None
    for start, end, vendor in ranges:
        if start <= mac_int <= end:
            return vendor
    return None


def _find_executable(names: List[str]) -> Optional[str]:
    for name in names:
        if not name:
            continue
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(path_dir, name)
            if os.path.isfile(candidate):
                return candidate
        if os.name == "nt":
            win_candidates = [
                os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "OpenSSH", name),
                os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", name),
            ]
            for candidate in win_candidates:
                if os.path.isfile(candidate):
                    return candidate
    return None


def _run_paramiko(router_ip: str, username: str, password: str, cmd: str, timeout: int) -> Optional[subprocess.CompletedProcess]:
    try:
        import paramiko
    except Exception:
        return None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        router_ip,
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
    return subprocess.CompletedProcess(args=["paramiko", cmd], returncode=exit_status, stdout=out_text, stderr=err_text)


def run_ssh_command(router_ip: str, username: str, password: str, cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """
    Prefer plink on Windows (supports -pw), else sshpass, else ssh with keys.
    If no password-capable client is available, fail fast with a clear error.
    """
    paramiko_result = _run_paramiko(router_ip, username, password, cmd, timeout)
    if paramiko_result is not None:
        return paramiko_result

    if os.name == "nt":
        plink_bin = os.environ.get("PLINK_BIN") or _find_executable(["plink.exe", "plink"])
        if plink_bin:
            full_cmd = [
                plink_bin, "-ssh", "-batch", "-pw", password,
                f"{username}@{router_ip}", cmd
            ]
            return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)

    sshpass_bin = os.environ.get("SSHPASS_BIN") or _find_executable(["sshpass"])
    if sshpass_bin:
        ssh_base = ["ssh", "-o", "StrictHostKeyChecking=no", f"{username}@{router_ip}", cmd]
        full_cmd = [sshpass_bin, "-p", password, *ssh_base]
        return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)

    # Fallback: SSH with keys only (no password prompts)
    ssh_bin = _find_executable(["ssh"])
    if not ssh_bin:
        raise RuntimeError("No SSH client found. Install OpenSSH/PuTTY or add paramiko to Python environment.")
    full_cmd = [
        ssh_bin,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no",
        f"{username}@{router_ip}",
        cmd
    ]
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)


def _parse_detail_records(output: str) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line[0].isdigit() and " " in line:
            if current:
                records.append(current)
                current = {}
            line = line.split(" ", 1)[1]
        for token in line.split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            current[key.strip()] = value.strip()
    if current:
        records.append(current)
    return records


def parse_ip_scan(output: str) -> List[Dict[str, str]]:
    devices = []
    for rec in _parse_detail_records(output):
        ip = rec.get("address") or rec.get("ip-address") or rec.get("address-range")
        mac = rec.get("mac-address")
        identity = rec.get("identity") or rec.get("host-name") or ""
        iface = rec.get("interface")
        if mac:
            devices.append({
                "ip": ip,
                "mac": normalize_mac(mac),
                "identity": identity if identity else mac,
                "interface": iface
            })
    if devices:
        return devices

    # RouterOS table output fallback
    ip_mac_re = re.compile(r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})")
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("flags:") or line.lower().startswith("address"):
            continue
        match = ip_mac_re.search(line)
        if not match:
            continue
        devices.append({
            "ip": match.group("ip"),
            "mac": normalize_mac(match.group("mac")),
            "identity": match.group("mac"),
            "interface": None
        })
    return devices


def parse_mac_scan(output: str) -> List[Dict[str, str]]:
    devices = []
    for rec in _parse_detail_records(output):
        mac = rec.get("mac-address")
        identity = rec.get("identity") or rec.get("host-name") or ""
        iface = rec.get("interface")
        if mac:
            devices.append({
                "ip": None,
                "mac": normalize_mac(mac),
                "identity": identity if identity else mac,
                "interface": iface
            })
    if devices:
        return devices

    # Table output fallback
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("columns"):
            continue
        parts = line.split()
        if len(parts) < 1:
            continue
        mac = None
        iface = None
        identity = ""
        for idx, token in enumerate(parts):
            if ":" in token and len(token.split(":")) >= 3:
                mac = token
                if idx + 1 < len(parts):
                    iface = parts[idx + 1] if len(parts) > idx + 1 else None
                    identity = " ".join(parts[idx + 2:]) if len(parts) > idx + 2 else mac
                break
        if mac:
            devices.append({
                "ip": None,
                "mac": normalize_mac(mac),
                "identity": identity if identity else mac,
                "interface": iface
            })
    return devices


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config_path = sys.argv[1]
    config = load_config(config_path)

    params = config.get("parameters", config)
    router_ip = params.get("router_ip")
    username = params.get("username")
    password = params.get("password")
    interface = params.get("interface")
    address_range = params.get("address_range")
    duration = int(params.get("scan_duration_s", 30) or 30)
    note = params.get("note") or None
    site_name = config.get("site_name") or params.get("site_name") or "default"
    db_path = config.get("database_path")
    trace_output = str(params.get("trace_output", "false")).strip().lower() in ("1", "true", "yes")
    replace_on_ip = str(params.get("replace_on_ip", "false")).strip().lower() in ("1", "true", "yes")

    if address_range:
        range_match = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}$", address_range.strip())
        if not range_match:
            print(json.dumps({
                "status": "error",
                "message": "Invalid address range format. Use start-end like 10.192.111.1-10.192.111.126."
            }))
            return

    if not router_ip or not username or password is None:
        print(json.dumps({"status": "error", "message": "Missing router_ip/username/password"}))
        return
    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return

    oui_ranges = load_oui_ranges(OUI_FILE)

    def build_scan_command(path: str) -> str:
        cmd = f"{path} duration={duration}"
        if interface:
            cmd += f" interface={interface}"
        if address_range:
            cmd += f" address-range={address_range}"
        return cmd

    devices: List[Dict[str, str]] = []
    scan_paths = ["tool ip-scan"]
    for path in scan_paths:
        combined_cmd = build_scan_command(path)
        try:
            result = run_ssh_command(router_ip, username, password, combined_cmd, timeout=duration + 15)
        except subprocess.TimeoutExpired as exc:
            print(json.dumps({
                "status": "error",
                "message": f"SSH command timed out after {exc.timeout}s. Ensure password auth works (install PuTTY/plink or set SSH keys)."
            }))
            return
        except RuntimeError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}))
            return
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if trace_output:
            print(f"=== ip-scan command ===\n{combined_cmd}", file=sys.stderr)
            print(f"=== ip-scan output ({path}) ===\n{output}", file=sys.stderr)
        if result.returncode != 0 and ("Permission denied" in output or "Authentication failed" in output):
            print(json.dumps({
                "status": "error",
                "message": "SSH authentication failed. Use correct credentials or install PuTTY/plink on Windows."
            }))
            return
        devices = parse_ip_scan(output)
        if devices:
            break

    # Only ip-scan is used; mac-scan disabled by request.

    records: List[DeviceRecord] = []
    for d in devices:
        mac = d.get("mac")
        if not mac:
            continue
        ip = d.get("ip")
        vendor = lookup_vendor(mac, oui_ranges)
        name = vendor or d.get("identity") or mac
        oui = normalize_mac(mac)[:8]
        rec = DeviceRecord(
            mac=mac,
            ip=ip,
            name=name,
            iface=d.get("interface"),
            vendor=vendor,
            oui=oui,
            note=note
        )
        records.append(rec)

    # Merge into devices.db
    devices_added = 0
    devices_updated = 0
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            database = json.load(f)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to read database: {e}"}))
        return

    now = datetime.now().isoformat()
    if "devices" not in database:
        database["devices"] = []

    def safe_site(site: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in site.lower())

    for rec in records:
        mac_id = f"dev_mac_{rec.mac.replace(':', '').lower()}"
        device_id = f"{mac_id}_{safe_site(site_name)}"
        existing = next(
            (d for d in database["devices"] if d.get("mac", "").lower() == rec.mac.lower() and d.get("site") == site_name),
            None
        )
        if not existing and replace_on_ip and rec.ip:
            existing = next(
                (d for d in database["devices"] if d.get("ip") == rec.ip and d.get("site") == site_name),
                None
            )

        if existing:
            existing_name = existing.get("name") or ""
            if existing.get("discovered_by") == "mikrotik_mac_discovery" or existing_name in ("", rec.mac, rec.ip):
                existing["name"] = rec.name
            existing["ip"] = rec.ip or existing.get("ip")
            existing["vendor"] = rec.vendor or existing.get("vendor")
            existing["mac"] = rec.mac
            existing["oui"] = rec.oui or existing.get("oui")
            existing["discovered_by"] = "mikrotik_mac_discovery"
            if note:
                existing["notes"] = f"{existing.get('notes', '')} {note}".strip()
            existing["last_seen"] = now
            existing["last_modified"] = now
            devices_updated += 1
        else:
            database["devices"].append({
                "id": device_id,
                "site": site_name,
                "name": rec.name,
                "ip": rec.ip,
                "mac": rec.mac,
                "vendor": rec.vendor,
                "oui": rec.oui,
                "type": "unknown",
                "model": "",
                "platform": "",
                "capabilities": "",
                "discovered_by": "mikrotik_mac_discovery",
                "discovered_at": now,
                "last_seen": now,
                "last_modified": now,
                "notes": note or ""
            })
            devices_added += 1

    database.setdefault("meta", {})["last_modified"] = now
    try:
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump(database, f, indent=2)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to write database: {e}"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "devices_found": len(records),
        "devices_added": devices_added,
        "devices_updated": devices_updated,
        "devices": [r.to_output() for r in records],
        "ran_at": datetime.now().isoformat()
    }))


if __name__ == "__main__":
    main()
