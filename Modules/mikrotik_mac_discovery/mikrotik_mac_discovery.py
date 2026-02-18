#!/usr/bin/env python3
"""
MikroTik MAC Discovery

Uses RouterOS `/tool/ip/scan` (or `/tool/mac-scan` fallback) via SSH to gather IP/MAC/identity.
Maps vendor using local OUI ranges file (range-based).
Outputs device list to be merged into devices.db by the backend module runner.
"""

from __future__ import annotations

import ipaddress
import json
import portalocker
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
OUI_FILE = os.path.join(MODULE_DIR, "oui_ranges.txt")

def is_mac_name(name: str) -> bool:
    if not name:
        return False
    value = name.strip()
    if re.fullmatch(r"([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}", value):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{12}", value):
        return True
    return False


@dataclass
class DeviceRecord:
    mac: str
    ip: Optional[str]
    name: str
    scan_name: str
    dhcp_name: str
    iface: Optional[str]
    vendor: Optional[str]
    oui: Optional[str]
    note: Optional[str]
    catch_ip_thief: bool = False

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
                vendor_label = vendor.split(",", 1)[0].strip()
                start = mac_to_int(normalize_mac(start_str))
                end = mac_to_int(normalize_mac(end_str))
                ranges.append((start, end, vendor_label))
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
        netbios = rec.get("netbios") or rec.get("netbios-name") or rec.get("netbios_name") or ""
        identity = netbios or rec.get("identity") or rec.get("host-name") or ""
        iface = rec.get("interface")
        if mac:
            devices.append({
                "ip": ip,
                "mac": normalize_mac(mac),
                "identity": identity if identity else mac,
                "netbios": netbios,
                "interface": iface
            })
    if devices:
        return devices

    # RouterOS table output fallback
    ip_mac_re = re.compile(r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})")
    header_seen = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("flags:"):
            continue
        if "ADDRESS" in line.upper() and "NETBIOS" in line.upper():
            header_seen = True
            continue
        if line.lower().startswith("address") and not header_seen:
            continue
        if header_seen:
            parts = line.split()
            if len(parts) >= 3 and re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]):
                ip = parts[0]
                mac = parts[1] if re.match(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", parts[1]) else ""
                netbios = " ".join(parts[3:]).strip() if len(parts) > 3 else ""
                if mac:
                    devices.append({
                        "ip": ip,
                        "mac": normalize_mac(mac),
                        "identity": netbios if netbios else mac,
                        "netbios": netbios,
                        "interface": None
                    })
                continue
        match = ip_mac_re.search(line)
        if not match:
            continue
        devices.append({
            "ip": match.group("ip"),
            "mac": normalize_mac(match.group("mac")),
            "identity": match.group("mac"),
            "netbios": "",
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
    router_device_id = params.get("router_device_id")
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
    use_dhcp_hostname = str(params.get("use_dhcp_hostname", "true")).strip().lower() in ("1", "true", "yes")
    catch_ip_thieves = bool(params.get("catch_ip_thieves", False))

    if address_range:
        range_match = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}$", address_range.strip())
        if not range_match:
            print(json.dumps({
                "status": "error",
                "message": "Invalid address range format. Use start-end like 10.192.111.1-10.192.111.126."
            }))
            return

    if not interface:
        interface = "ether1"
    if not router_ip and router_device_id and db_path:
        try:
            with portalocker.Lock(db_path, "r", timeout=5, encoding="utf-8") as f:
                data = json.load(f)
            for dev in data.get("devices", []):
                if dev.get("id") == router_device_id:
                    router_ip = dev.get("ip")
                    if not interface:
                        interface = dev.get("interface") or interface
                    break
        except Exception:
            pass
    if not router_ip and db_path and site_name:
        try:
            with portalocker.Lock(db_path, "r", timeout=5, encoding="utf-8") as f:
                data = json.load(f)
            for dev in data.get("devices", []):
                if dev.get("site") != site_name:
                    continue
                if (dev.get("type") or "").lower() != "server":
                    continue
                name = (dev.get("name") or "").lower()
                vendor = (dev.get("vendor") or "").lower()
                if "mikrotik" in name or "mikrotik" in vendor:
                    router_ip = dev.get("ip")
                    if router_ip:
                        break
        except Exception:
            pass
    if not router_ip or not username or password is None:
        print(json.dumps({"status": "error", "message": "Missing router_ip/username/password"}))
        return
    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return

    oui_ranges = load_oui_ranges(OUI_FILE)

    def normalize_address_range(value: str) -> str:
        value = (value or "").strip()
        if not value:
            return value
        if "/" in value:
            try:
                net = ipaddress.ip_network(value, strict=False)
                hosts = list(net.hosts())
                if not hosts:
                    return value
                start = hosts[0]
                end = hosts[-1]
                return f"{start}-{end}"
            except Exception:
                return value
        return value

    def build_scan_command(path: str, target_range: str, use_kv: bool = True, use_proplist: bool = True, include_interface: bool = True) -> str:
        cmd = f"{path} duration={duration}"
        if use_kv:
            cmd += " as-value without-paging"
        if target_range:
            cmd += f" address-range={normalize_address_range(target_range)}"
        if include_interface and interface:
            cmd += f" interface={interface}"
        if use_proplist:
            cmd += " proplist=address,mac-address,netbios,interface"
        return cmd

    def parse_ip_address_ranges() -> List[str]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip address print", timeout=20)
        except Exception:
            return []
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        ranges = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";;;"):
                continue
            parts = line.split()
            if parts and parts[0].isdigit():
                parts = parts[1:]
            if len(parts) < 2:
                continue
            addr = parts[0]
            iface = parts[-1]
            if interface and iface != interface:
                continue
            if "/" not in addr:
                continue
            network = None
            if len(parts) >= 3:
                net_or_mask = parts[1]
                if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", net_or_mask):
                    try:
                        network = ipaddress.ip_network(f"{addr.split('/')[0]}/{net_or_mask}", strict=False)
                    except ValueError:
                        network = None
            if network is None:
                try:
                    network = ipaddress.ip_interface(addr).network
                except ValueError:
                    continue
            ranges.append(str(network))
        deduped = []
        seen = set()
        for item in ranges:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def parse_ip_pool_ranges() -> List[str]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip pool print", timeout=20)
        except Exception:
            return []
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        ranges: List[str] = []
        current_ranges = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.lower().startswith("flags:"):
                continue
            if line.lower().startswith("columns:"):
                continue
            if line[0].isdigit():
                if current_ranges:
                    ranges.extend(current_ranges)
                    current_ranges = []
                parts = line.split()
                if len(parts) >= 3:
                    current_ranges.extend(parts[2:])
            else:
                tokens = line.split()
                current_ranges.extend(tokens)
        if current_ranges:
            ranges.extend(current_ranges)
        cleaned = []
        for item in ranges:
            token = item.strip().strip(",")
            if not token:
                continue
            if re.match(r"^\d+\.\d+\.\d+\.\d+(?:/\d+)?$", token) or "-" in token:
                cleaned.append(token)
        return cleaned

    def parse_dhcp_leases() -> Dict[str, Any]:
        hostnames_by_ip: Dict[str, str] = {}
        hostnames_by_mac: Dict[str, str] = {}
        status_by_ip: Dict[str, str] = {}
        active_ips: set[str] = set()
        lease_pairs: set[tuple[str, str]] = set()
        try:
            detail_cmd = "ip dhcp-server lease print detail without-paging"
            result = run_ssh_command(router_ip, username, password, detail_cmd, timeout=30)
        except Exception:
            result = None
        if result and result.returncode == 0:
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            if trace_output:
                print("=== dhcp lease command ===", file=sys.stderr)
                print(detail_cmd, file=sys.stderr)
                print("=== dhcp lease output (detail) ===", file=sys.stderr)
                print(output, file=sys.stderr)
            for rec in _parse_detail_records(output):
                ip = rec.get("address") or rec.get("ip-address")
                name = rec.get("host-name") or rec.get("host-name")
                mac = rec.get("mac-address")
                status = (rec.get("status") or "").strip().lower()
                active_addr = rec.get("active-address")
                if ip and name:
                    hostnames_by_ip[ip] = name
                if ip and status:
                    status_by_ip[ip] = status
                if active_addr:
                    active_ips.add(active_addr)
                if status == "bound" and ip:
                    active_ips.add(ip)
                if ip and mac:
                    try:
                        lease_pairs.add((ip, normalize_mac(mac)))
                    except Exception:
                        pass
                if mac and name:
                    try:
                        hostnames_by_mac[normalize_mac(mac)] = name
                    except Exception:
                        pass
            if hostnames_by_ip or hostnames_by_mac:
                if trace_output:
                    print(
                        f"=== dhcp lease count (detail): ip={len(hostnames_by_ip)} mac={len(hostnames_by_mac)} active={len(active_ips)} pairs={len(lease_pairs)} ===",
                        file=sys.stderr
                    )
                return {
                    "by_ip": hostnames_by_ip,
                    "by_mac": hostnames_by_mac,
                    "status_by_ip": status_by_ip,
                    "active_ips": active_ips,
                    "lease_pairs": lease_pairs
                }

        try:
            result = run_ssh_command(router_ip, username, password, "ip dhcp-server lease print", timeout=30)
        except Exception:
            return {
                "by_ip": hostnames_by_ip,
                "by_mac": hostnames_by_mac,
                "status_by_ip": status_by_ip,
                "active_ips": active_ips,
                "lease_pairs": lease_pairs
            }
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if trace_output:
            print("=== dhcp lease output (table) ===", file=sys.stderr)
            print(output, file=sys.stderr)
        header = None
        for line in output.splitlines():
            if "ADDRESS" in line and "HOST-NAME" in line:
                header = line
                break
        if header:
            addr_idx = header.find("ADDRESS")
            mac_idx = header.find("MAC-ADDRESS")
            host_idx = header.find("HOST-NAME")
            for raw_line in output.splitlines():
                if not raw_line.strip() or raw_line.strip().startswith(";;;"):
                    continue
                if raw_line.lstrip().startswith(("Flags:", "#")):
                    continue
                if addr_idx >= len(raw_line):
                    continue
                ip = raw_line[addr_idx:mac_idx].strip() if mac_idx != -1 else raw_line[addr_idx:].strip().split()[0]
                name = raw_line[host_idx:].strip() if host_idx != -1 else ""
            if ip and name:
                hostnames[ip] = name
            if trace_output:
                print(f"=== dhcp lease count (table): {len(hostnames)} ===", file=sys.stderr)
            return hostnames

        ip_re = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";;;") or line.lower().startswith("flags:"):
                continue
            parts = line.split()
            if not parts:
                continue
            ip_idx = None
            for idx, token in enumerate(parts):
                if ip_re.match(token):
                    ip_idx = idx
                    break
            if ip_idx is None:
                continue
            ip = parts[ip_idx]
            hostname = parts[ip_idx + 2] if ip_idx + 2 < len(parts) else ""
            if hostname and not hostname.lower().startswith("dhcp"):
                hostnames_by_ip[ip] = hostname
        if trace_output:
            print(f"=== dhcp lease count (fallback): ip={len(hostnames_by_ip)} ===", file=sys.stderr)
        return {
            "by_ip": hostnames_by_ip,
            "by_mac": hostnames_by_mac,
            "status_by_ip": status_by_ip,
            "active_ips": active_ips,
            "lease_pairs": lease_pairs
        }

    def resolve_address_ranges() -> List[str]:
        if address_range:
            return [address_range]

        addr_ranges = parse_ip_address_ranges()
        if addr_ranges:
            return addr_ranges
        return parse_ip_pool_ranges()

    devices: List[Dict[str, str]] = []
    scan_paths = ["tool ip-scan"]
    dhcp_hostnames = parse_dhcp_leases() if use_dhcp_hostname else {
        "by_ip": {},
        "by_mac": {},
        "status_by_ip": {},
        "active_ips": set(),
        "lease_pairs": set()
    }
    if trace_output and use_dhcp_hostname:
        print(
            f"=== dhcp hostnames loaded: ip={len(dhcp_hostnames.get('by_ip', {}))} "
            f"mac={len(dhcp_hostnames.get('by_mac', {}))} active={len(dhcp_hostnames.get('active_ips', set()))} "
            f"pairs={len(dhcp_hostnames.get('lease_pairs', set()))} ===",
            file=sys.stderr
        )
    address_ranges = resolve_address_ranges()
    if not address_ranges:
        print(json.dumps({"status": "error", "message": "No address ranges found for scan."}))
        return
    scan_errors: List[str] = []
    for path in scan_paths:
        for target_range in address_ranges:
            attempts = [
                {"use_kv": True, "use_proplist": True, "include_interface": True},
                {"use_kv": False, "use_proplist": True, "include_interface": True},
                {"use_kv": False, "use_proplist": False, "include_interface": True},
                {"use_kv": False, "use_proplist": False, "include_interface": False},
            ]
            result = None
            output = ""
            combined_cmd = ""
            for attempt in attempts:
                combined_cmd = build_scan_command(
                    path,
                    target_range,
                    use_kv=attempt["use_kv"],
                    use_proplist=attempt["use_proplist"],
                    include_interface=attempt["include_interface"]
                )
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
                if result.returncode == 0:
                    break
                if ("Permission denied" in output or "Authentication failed" in output):
                    print(json.dumps({
                        "status": "error",
                        "message": "SSH authentication failed. Use correct credentials or install PuTTY/plink on Windows."
                    }))
                    return
                # If interface is invalid, retry without it
                if "value-name" in output and attempt["include_interface"]:
                    continue
                # Try next attempt for unsupported params
                if ("expected end of command" in output or "unknown parameter" in output):
                    continue
            if result is None or result.returncode != 0:
                err_text = output.strip()
                if err_text:
                    scan_errors.append(err_text[:1000])
            dhcp_hits = 0
            for item in parse_ip_scan(output):
                ip = item.get("ip")
                mac = item.get("mac")
                ip_match = dhcp_hostnames.get("by_ip", {}).get(ip) if ip else None
                mac_key = normalize_mac(mac) if mac else None
                mac_match = dhcp_hostnames.get("by_mac", {}).get(mac_key) if mac_key else None
                if ip_match:
                    item["dhcp_hostname"] = ip_match
                    dhcp_hits += 1
                elif mac_match:
                    item["dhcp_hostname"] = mac_match
                    dhcp_hits += 1
                elif catch_ip_thieves and ip:
                    active_ips = dhcp_hostnames.get("active_ips", set())
                    lease_pairs = dhcp_hostnames.get("lease_pairs", set())
                    lease_match = False
                    if mac:
                        try:
                            lease_match = (ip, normalize_mac(mac)) in lease_pairs
                        except Exception:
                            lease_match = False
                    if ip not in active_ips and not lease_match:
                        item["catch_ip_thief"] = True
                devices.append(item)
            if trace_output and use_dhcp_hostname:
                print(f"=== dhcp hostname matches: {dhcp_hits} ===", file=sys.stderr)

    # Only ip-scan is used; mac-scan disabled by request.

    records: List[DeviceRecord] = []
    for d in devices:
        mac = d.get("mac")
        if not mac:
            continue
        ip = d.get("ip")
        vendor = lookup_vendor(mac, oui_ranges)
        scan_name = d.get("netbios") or d.get("identity") or vendor or mac
        name = scan_name
        dhcp_name = d.get("dhcp_hostname") or ""
        if dhcp_name:
            name = dhcp_name
        catch_ip_thief = bool(d.get("catch_ip_thief"))
        oui = normalize_mac(mac)[:8]
        rec = DeviceRecord(
            mac=mac,
            ip=ip,
            name=name,
            scan_name=scan_name,
            dhcp_name=dhcp_name,
            iface=d.get("interface"),
            vendor=vendor,
            oui=oui,
            note=note,
            catch_ip_thief=catch_ip_thief
        )
        records.append(rec)

    # Merge into devices.db
    devices_added = 0
    devices_updated = 0
    try:
        with portalocker.Lock(db_path, "r", timeout=5, encoding="utf-8") as f:
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
            dtype = (existing.get("type") or "").strip().lower()
            if rec.dhcp_name:
                existing["name"] = rec.dhcp_name
            else:
                if dtype not in ("pc", "pda", "unknown", "") and existing_name.startswith("Catched-"):
                    existing["name"] = existing_name[len("Catched-"):]
                    existing_name = existing.get("name") or ""
                if rec.catch_ip_thief and dtype in ("pc", "pda", "unknown", ""):
                    thief_name = f"Catched-{rec.scan_name}"
                    existing["name"] = thief_name
                elif dtype in ("pc", "pda"):
                    if rec.name:
                        existing["name"] = rec.name
                else:
                    if not existing_name:
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
            name_value = rec.name
            if rec.dhcp_name:
                name_value = rec.dhcp_name
            elif rec.catch_ip_thief:
                name_value = f"Catched-{rec.scan_name}"
            database["devices"].append({
                "id": device_id,
                "site": site_name,
                "name": name_value,
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
        with portalocker.Lock(db_path, "w", timeout=5, encoding="utf-8") as f:
            json.dump(database, f, indent=2)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"Failed to write database: {e}"}))
        return

    result_payload = {
        "status": "success",
        "site": site_name,
        "devices_found": len(records),
        "devices_added": devices_added,
        "devices_updated": devices_updated,
        "devices": [r.to_output() for r in records],
        "ran_at": datetime.now().isoformat()
    }
    if not records and scan_errors:
        result_payload["warnings"] = scan_errors[:3]
        print("=== ip-scan errors ===", file=sys.stderr)
        for err in scan_errors[:3]:
            print(err, file=sys.stderr)
    print(json.dumps(result_payload))


if __name__ == "__main__":
    main()
