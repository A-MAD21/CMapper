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
import os
import re
import shlex
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
OUI_FILE = os.path.join(MODULE_DIR, "oui_ranges.txt")
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store, write_json_store

def is_mac_name(name: str) -> bool:
    if not name:
        return False
    value = name.strip()
    if re.fullmatch(r"([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}", value):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{12}", value):
        return True
    return False


def is_truncated_name(name: str) -> bool:
    return bool(name) and name.strip().endswith("...")


def clean_scan_hostname(name: str) -> str:
    value = (name or "").strip().strip('"').strip("'").strip()
    if not value:
        return ""
    value = value.rstrip(".")
    if "/" in value:
        value = value.split("/", 1)[0].strip()
    if "." in value:
        value = value.split(".", 1)[0].strip()
    return value


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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


def _prompt_returned(text: str) -> bool:
    cleaned = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).rstrip()
    return bool(re.search(r"(?m)(?:\[[^\]\r\n]+\]\s*)?[>#]\s*$", cleaned))


def _scan_output_has_rows(text: str) -> bool:
    return bool(
        re.search(r"(?m)^\s*(?:\d+\s+)?address=", text)
        or re.search(r"(?m)^\s*\d{1,3}(?:\.\d{1,3}){3}\s+", text)
    )


def _routeros_command_error(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "expected end of command" in lowered
        or "bad command name" in lowered
        or "no such item" in lowered
        or "failure:" in lowered
        or "syntax error" in lowered
        or "value-name" in lowered
    )


def _routeros_interface_error(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "input does not match any value of interface" in lowered
        or "no such item" in lowered and "interface" in lowered
        or "value-name" in lowered and "interface" in lowered
    )


def _extract_seen_ips_from_scan(output: str) -> set[str]:
    seen: set[str] = set()
    for rec in _parse_detail_records(output):
        ip = rec.get("address") or rec.get("ip-address") or rec.get("address-range")
        if ip:
            seen.add(ip.strip())
    for raw_line in output.splitlines():
        if re.search(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", raw_line):
            for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", raw_line):
                seen.add(ip)
    return seen


def _run_paramiko(router_ip: str, username: str, password: str, cmd: str, timeout: int, port: int, wide_terminal: bool = False) -> Optional[subprocess.CompletedProcess]:
    try:
        warnings.filterwarnings("ignore", message=r"TripleDES has been moved.*")
        import paramiko
    except Exception:
        return None

    client = paramiko.SSHClient()
    channel = None
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            router_ip,
            port=port,
            username=username,
            password=password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10
        )
        if wide_terminal:
            channel = client.invoke_shell(term="vt100", width=2000, height=1000)
            channel.settimeout(2)
            deadline = time.time() + max(timeout, 1)
            initial_chunks: List[bytes] = []
            initial_deadline = min(deadline, time.time() + 5)
            while time.time() < initial_deadline:
                if channel.recv_ready():
                    initial_chunks.append(channel.recv(65535))
                    if _prompt_returned(b"".join(initial_chunks).decode(errors="ignore")):
                        break
                time.sleep(0.05)
            while channel.recv_ready():
                channel.recv(65535)
            channel.send(cmd + "\r")
            sent_at = time.time()
            last_recv_at = sent_at
            live_scan_cutoff = sent_at + max(5, min(timeout - 1, 30)) if "tool ip-scan" in cmd and "duration=" not in cmd else None
            out_chunks: List[bytes] = []
            while True:
                if channel.recv_ready():
                    out_chunks.append(channel.recv(65535))
                    last_recv_at = time.time()
                    out_text_so_far = b"".join(out_chunks).decode(errors="ignore")
                    if _prompt_returned(out_text_so_far) and (_scan_output_has_rows(out_text_so_far) or time.time() - sent_at > 1.0):
                        break
                    # Some RouterOS ip-scan variants run as a live view and do
                    # not return to the prompt unless interrupted. Once rows are
                    # visible and the output has settled, treat it as complete.
                    if _scan_output_has_rows(out_text_so_far) and time.time() - last_recv_at > 1.5:
                        break
                else:
                    out_text_so_far = b"".join(out_chunks).decode(errors="ignore")
                    if _scan_output_has_rows(out_text_so_far) and time.time() - last_recv_at > 1.5:
                        break
                    if live_scan_cutoff is not None and time.time() > live_scan_cutoff:
                        break
                if live_scan_cutoff is not None and time.time() > live_scan_cutoff:
                    break
                if time.time() > deadline:
                    raise subprocess.TimeoutExpired(cmd=["paramiko", cmd], timeout=timeout)
                time.sleep(0.05)
            try:
                channel.send("\x03")
                time.sleep(0.1)
                while channel.recv_ready():
                    out_chunks.append(channel.recv(65535))
            except Exception:
                pass
            out_text = b"".join(out_chunks).decode(errors="ignore")
            return subprocess.CompletedProcess(args=["paramiko", cmd], returncode=0, stdout=out_text, stderr="")

        channel = client.get_transport().open_session(timeout=10)
        channel.settimeout(2)
        channel.exec_command(cmd)
        deadline = time.time() + max(timeout, 1)
        out_chunks: List[bytes] = []
        err_chunks: List[bytes] = []
        while True:
            if channel.recv_ready():
                out_chunks.append(channel.recv(65535))
            if channel.recv_stderr_ready():
                err_chunks.append(channel.recv_stderr(65535))
            if channel.exit_status_ready():
                while channel.recv_ready():
                    out_chunks.append(channel.recv(65535))
                while channel.recv_stderr_ready():
                    err_chunks.append(channel.recv_stderr(65535))
                break
            if time.time() > deadline:
                raise subprocess.TimeoutExpired(cmd=["paramiko", cmd], timeout=timeout)
            time.sleep(0.05)
        exit_status = channel.recv_exit_status()
        out_text = b"".join(out_chunks).decode(errors="ignore")
        err_text = b"".join(err_chunks).decode(errors="ignore")
        return subprocess.CompletedProcess(args=["paramiko", cmd], returncode=exit_status, stdout=out_text, stderr=err_text)
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        client.close()


def run_ssh_command(router_ip: str, username: str, password: str, cmd: str, timeout: int = 30, port: int = 22, wide_terminal: bool = False) -> subprocess.CompletedProcess:
    """
    Prefer plink on Windows (supports -pw), else sshpass, else ssh with keys.
    If no password-capable client is available, fail fast with a clear error.
    """
    paramiko_result = _run_paramiko(router_ip, username, password, cmd, timeout, port, wide_terminal=wide_terminal)
    if paramiko_result is not None:
        return paramiko_result

    if os.name == "nt":
        plink_bin = os.environ.get("PLINK_BIN") or _find_executable(["plink.exe", "plink"])
        if plink_bin:
            full_cmd = [
                plink_bin, "-ssh", "-batch", "-P", str(port), "-pw", password,
                f"{username}@{router_ip}", cmd
            ]
            return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)

    sshpass_bin = os.environ.get("SSHPASS_BIN") or _find_executable(["sshpass"])
    if sshpass_bin:
        ssh_base = ["ssh", "-p", str(port), "-o", "StrictHostKeyChecking=no", f"{username}@{router_ip}", cmd]
        full_cmd = [sshpass_bin, "-p", password, *ssh_base]
        return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)

    # Fallback: SSH with keys only (no password prompts)
    ssh_bin = _find_executable(["ssh"])
    if not ssh_bin:
        raise RuntimeError("No SSH client found. Install OpenSSH/PuTTY or add paramiko to Python environment.")
    full_cmd = [
        ssh_bin,
        "-p", str(port),
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
        try:
            tokens = shlex.split(line)
        except ValueError:
            tokens = line.split()
        for token in tokens:
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
        scan_hostname = clean_scan_hostname(
            rec.get("dns")
            or rec.get("dns-name")
            or rec.get("identity")
            or rec.get("host-name")
            or ""
        )
        netbios = clean_scan_hostname(
            rec.get("netbios") or rec.get("netbios-name") or rec.get("netbios_name") or ""
        )
        iface = rec.get("interface")
        if mac:
            identity = scan_hostname or netbios or mac
            devices.append({
                "ip": ip,
                "mac": normalize_mac(mac),
                "identity": identity,
                "scan_hostname": scan_hostname,
                "netbios": netbios,
                "interface": iface
            })
    if devices:
        return devices

    # RouterOS table output fallback
    ip_mac_re = re.compile(r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})")
    header_seen = False
    header_columns: Dict[str, int] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("flags:"):
            continue
        if line.lower().startswith("columns:"):
            header_seen = True
            header_columns = {}
            continue
        upper_line = line.upper()
        if "ADDRESS" in upper_line and "MAC-ADDRESS" in upper_line and ("NETBIOS" in upper_line or "DNS" in upper_line):
            header_seen = True
            upper = raw_line.upper()
            header_columns = {
                "address": upper.find("ADDRESS"),
                "mac": upper.find("MAC-ADDRESS"),
                "time": upper.find("TIME"),
                "dns": upper.find("DNS"),
                "snmp": upper.find("SNMP"),
                "netbios": upper.find("NETBIOS")
            }
            continue
        if line.lower().startswith("address") and not header_seen:
            continue
        if header_seen:
            ip_match = re.search(r"\d{1,3}(?:\.\d{1,3}){3}", raw_line)
            if ip_match:
                ip = ip_match.group(0)
                mac_match = re.search(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", raw_line)
                mac = mac_match.group(0) if mac_match else ""
                scan_hostname = ""
                netbios = ""
                dns_idx = header_columns.get("dns", -1)
                netbios_idx = header_columns.get("netbios", -1)
                if dns_idx != -1 and len(raw_line) > dns_idx:
                    next_idxs = [idx for idx in (netbios_idx,) if idx != -1 and idx > dns_idx]
                    end_idx = min(next_idxs) if next_idxs else None
                    scan_hostname = clean_scan_hostname(raw_line[dns_idx:end_idx].strip() if end_idx else raw_line[dns_idx:].strip())
                if netbios_idx != -1 and len(raw_line) > netbios_idx:
                    netbios = clean_scan_hostname(raw_line[netbios_idx:].strip())
                elif dns_idx == -1 and netbios_idx == -1 and mac_match:
                    tail = raw_line[mac_match.end():].strip()
                    tail_parts = tail.split()
                    if len(tail_parts) > 1:
                        # Position-based fallback is usually NETBIOS. Treat it
                        # as the weakest hostname source so DHCP can override it.
                        netbios = clean_scan_hostname(tail_parts[-1])
                identity = scan_hostname or netbios or mac
                devices.append({
                    "ip": ip,
                    "mac": normalize_mac(mac) if mac else "",
                    "identity": identity,
                    "scan_hostname": scan_hostname,
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
    try:
        ssh_port = int(params.get("ssh_port") or 22)
    except Exception:
        ssh_port = 22
    interface = (params.get("interface") or "").strip() or None
    address_range = params.get("address_range")
    duration = int(params.get("scan_duration_s", 30) or 30)
    note = params.get("note") or None
    site_name = config.get("site_name") or params.get("site_name") or "default"
    db_path = config.get("database_path")
    trace_output = parse_bool(params.get("trace_output"), False)
    replace_on_ip = parse_bool(params.get("replace_on_ip"), False)
    use_dhcp_hostname = parse_bool(params.get("use_dhcp_hostname"), True)
    catch_ip_thieves = parse_bool(params.get("catch_ip_thieves"), False)

    if address_range:
        range_value = address_range.strip()
        valid_range = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}$", range_value)
        valid_single = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", range_value)
        valid_cidr = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}$", range_value)
        if not (valid_range or valid_single or valid_cidr):
            print(json.dumps({
                "status": "error",
                "message": "Invalid address range format. Use a single IP, CIDR, or start-end range like 10.192.111.1-10.192.111.126."
            }))
            return

    if address_range and not interface:
        interface = "ether1"
    if db_path:
        data = read_json_store(db_path, "devices") or {}
        if not router_ip and router_device_id:
            for dev in data.get("devices", []):
                if dev.get("id") == router_device_id:
                    router_ip = dev.get("ip")
                    if not interface:
                        interface = dev.get("interface") or interface
                    break
        if not router_ip and site_name:
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

    def build_scan_command(
        path: str,
        target_range: str,
        iface: Optional[str],
        include_interface: bool = True,
        include_duration: bool = False,
        scan_duration: Optional[int] = None
    ) -> str:
        cmd = path
        if target_range:
            cmd += f" address-range={normalize_address_range(target_range)}"
        if include_interface and iface:
            cmd += f" interface={iface}"
        if include_duration:
            cmd += f" duration={scan_duration if scan_duration is not None else duration}"
        return cmd

    def parse_ip_address_ranges(iface_filter: Optional[str]) -> List[str]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip address print", timeout=20, port=ssh_port)
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
            if iface_filter and iface != iface_filter:
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

    def parse_interface_subnets() -> Dict[str, List[str]]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip address print", timeout=20, port=ssh_port)
        except Exception:
            return {}
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        iface_map: Dict[str, List[str]] = {}
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
            if network.prefixlen == 32:
                continue
            network = str(network)
            iface_map.setdefault(iface, [])
            if network not in iface_map[iface]:
                iface_map[iface].append(network)
        return iface_map

    def parse_ip_pool_ranges() -> List[str]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip pool print", timeout=20, port=ssh_port)
        except Exception:
            return []
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        range_re = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?:-\d{1,3}(?:\.\d{1,3}){3}|/\d+)?")
        table_ranges = range_re.findall(output)
        if table_ranges:
            cleaned: List[str] = []
            seen_ranges: set[str] = set()
            for item in table_ranges:
                if item in seen_ranges:
                    continue
                seen_ranges.add(item)
                cleaned.append(item)
            return cleaned

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

    def parse_ip_pool_map() -> Dict[str, List[str]]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip pool print detail without-paging", timeout=20, port=ssh_port)
        except Exception:
            return {}
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        pools: Dict[str, List[str]] = {}
        range_re = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?:-\d{1,3}(?:\.\d{1,3}){3}|/\d+)?")
        for rec in _parse_detail_records(output):
            name = rec.get("name")
            ranges = rec.get("ranges") or rec.get("range") or ""
            if not name or not ranges:
                continue
            items = range_re.findall(ranges)
            if items:
                pools[name] = items
        header = None
        for line in output.splitlines():
            if "NAME" in line and "RANGES" in line:
                header = line
                break
        if header:
            name_idx = header.find("NAME")
            ranges_idx = header.find("RANGES")
            for raw_line in output.splitlines():
                if not raw_line.strip() or raw_line.strip().startswith((";", "Flags:", "#")):
                    continue
                if not re.match(r"^\s*\d+", raw_line):
                    continue
                name = raw_line[name_idx:ranges_idx].strip() if ranges_idx != -1 else ""
                name = re.sub(r"^\d+\s+", "", name).strip()
                ranges_text = raw_line[ranges_idx:].strip() if ranges_idx != -1 else raw_line
                items = range_re.findall(ranges_text)
                if name and items:
                    pools[name] = items
        return pools

    def parse_dhcp_servers() -> List[Dict[str, str]]:
        try:
            result = run_ssh_command(router_ip, username, password, "ip dhcp-server print", timeout=20, port=ssh_port)
        except Exception:
            return []
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        servers: List[Dict[str, str]] = []
        header = None
        for line in output.splitlines():
            if "NAME" in line and "INTERFACE" in line and "ADDRESS-POOL" in line:
                header = line
                break
        if header:
            name_idx = header.find("NAME")
            iface_idx = header.find("INTERFACE")
            relay_idx = header.find("RELAY")
            pool_idx = header.find("ADDRESS-POOL")
            lease_idx = header.find("LEASE-TIME")
            for raw_line in output.splitlines():
                if not raw_line.strip() or raw_line.strip().startswith(";;;"):
                    continue
                if raw_line.lstrip().startswith(("Flags:", "#")):
                    continue
                if not re.match(r"^\s*\d+", raw_line):
                    continue
                name = raw_line[name_idx:iface_idx].strip() if iface_idx != -1 else raw_line[name_idx:].strip().split()[0]
                name = re.sub(r"^\d+\s+", "", name).strip()
                iface_end = relay_idx if relay_idx != -1 else pool_idx
                iface = raw_line[iface_idx:iface_end].strip() if iface_idx != -1 and iface_end != -1 else ""
                pool_end = lease_idx if lease_idx != -1 else len(raw_line)
                pool = raw_line[pool_idx:pool_end].strip() if pool_idx != -1 else ""
                if name and iface:
                    servers.append({"name": name, "interface": iface, "pool": pool})
            return servers

        # Fallback: token parse
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or line.lower().startswith("flags:") or line.startswith(";;;"):
                continue
            parts = line.split()
            if not parts:
                continue
            if parts[0].isdigit():
                parts = parts[1:]
            if len(parts) < 3:
                continue
            servers.append({"name": parts[0], "interface": parts[1], "pool": parts[2]})
        return servers
    def parse_dhcp_leases() -> Dict[str, Any]:
        hostnames_by_ip: Dict[str, str] = {}
        hostnames_by_mac: Dict[str, str] = {}
        mac_by_ip: Dict[str, str] = {}
        status_by_ip: Dict[str, str] = {}
        active_ips: set[str] = set()
        lease_pairs: set[tuple[str, str]] = set()
        try:
            detail_cmd = "ip dhcp-server lease print detail without-paging"
            result = run_ssh_command(router_ip, username, password, detail_cmd, timeout=30, port=ssh_port)
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
                        normalized_mac = normalize_mac(mac)
                        mac_by_ip[ip] = normalized_mac
                        lease_pairs.add((ip, normalized_mac))
                    except Exception:
                        pass
                if mac and name:
                    try:
                        hostnames_by_mac[normalize_mac(mac)] = name
                    except Exception:
                        pass
            if hostnames_by_ip or hostnames_by_mac or mac_by_ip:
                if trace_output:
                    print(
                        f"=== dhcp lease count (detail): ip={len(hostnames_by_ip)} mac={len(hostnames_by_mac)} active={len(active_ips)} pairs={len(lease_pairs)} ===",
                        file=sys.stderr
                    )
                return {
                    "by_ip": hostnames_by_ip,
                    "by_mac": hostnames_by_mac,
                    "mac_by_ip": mac_by_ip,
                    "status_by_ip": status_by_ip,
                    "active_ips": active_ips,
                    "lease_pairs": lease_pairs
                }

        try:
            result = run_ssh_command(router_ip, username, password, "ip dhcp-server lease print", timeout=30, port=ssh_port)
        except Exception:
            return {
                "by_ip": hostnames_by_ip,
                "by_mac": hostnames_by_mac,
                "mac_by_ip": mac_by_ip,
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
            server_idx = header.find("SERVER")
            status_idx = header.find("STATUS")
            last_seen_idx = header.find("LAST-SEEN")
            for raw_line in output.splitlines():
                if not raw_line.strip() or raw_line.strip().startswith(";;;"):
                    continue
                if raw_line.lstrip().startswith(("Flags:", "#")):
                    continue
                if addr_idx >= len(raw_line):
                    continue
                ip = raw_line[addr_idx:mac_idx].strip() if mac_idx != -1 else raw_line[addr_idx:].strip().split()[0]
                mac_text = raw_line[mac_idx:host_idx].strip() if mac_idx != -1 and host_idx != -1 else ""
                host_end = server_idx if server_idx != -1 else status_idx
                name = raw_line[host_idx:host_end].strip() if host_idx != -1 and host_end != -1 else ""
                status_end = last_seen_idx if last_seen_idx != -1 else len(raw_line)
                status = raw_line[status_idx:status_end].strip().lower() if status_idx != -1 else ""
                if ip and name:
                    hostnames_by_ip[ip] = name
                if ip and status:
                    status_by_ip[ip] = status
                    if status == "bound":
                        active_ips.add(ip)
                if ip and mac_text:
                    try:
                        normalized_mac = normalize_mac(mac_text)
                        mac_by_ip[ip] = normalized_mac
                        lease_pairs.add((ip, normalized_mac))
                        if name:
                            hostnames_by_mac[normalized_mac] = name
                    except Exception:
                        pass
            if trace_output:
                print(
                    f"=== dhcp lease count (table): ip={len(hostnames_by_ip)} mac_by_ip={len(mac_by_ip)} "
                    f"active={len(active_ips)} pairs={len(lease_pairs)} ===",
                    file=sys.stderr
                )
            return {
                "by_ip": hostnames_by_ip,
                "by_mac": hostnames_by_mac,
                "mac_by_ip": mac_by_ip,
                "status_by_ip": status_by_ip,
                "active_ips": active_ips,
                "lease_pairs": lease_pairs
            }

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
            "mac_by_ip": mac_by_ip,
            "status_by_ip": status_by_ip,
            "active_ips": active_ips,
            "lease_pairs": lease_pairs
        }

    def resolve_scan_targets() -> List[Dict[str, str]]:
        if address_range:
            return [{"interface": interface, "range": address_range}]

        targets: List[Dict[str, str]] = []
        seen_targets: set[tuple[str, str]] = set()

        def add_target(iface: Optional[str], target_range: str) -> None:
            iface = (iface or interface or "ether1").strip()
            target_range = (target_range or "").strip()
            if not iface or not target_range:
                return
            key = (iface, target_range)
            if key in seen_targets:
                return
            seen_targets.add(key)
            targets.append({"interface": iface, "range": target_range})

        dhcp_servers = parse_dhcp_servers()
        pool_map = parse_ip_pool_map()
        iface_subnets = parse_interface_subnets()
        if dhcp_servers:
            for srv in dhcp_servers:
                iface = srv.get("interface")
                pool = srv.get("pool")
                if not iface:
                    continue
                if interface and iface != interface:
                    continue
                iface_nets = iface_subnets.get(iface, [])
                if iface_nets:
                    for net in iface_nets:
                        add_target(iface, net)
                    continue
                if pool:
                    ranges = pool_map.get(pool, [])
                    for r in ranges:
                        add_target(iface, r)

        # Scan the interface network, not only the DHCP pool. Static devices
        # outside the lease pool still need last_seen refreshed when ip-scan
        # sees them. DHCP lease names remain available as a naming fallback.
        if targets:
            return targets

        if interface:
            for net in iface_subnets.get(interface, []):
                add_target(interface, net)
        else:
            for iface, nets in iface_subnets.items():
                for net in nets:
                    add_target(iface, net)
        if targets:
            return targets

        # Last resort: ip address print filtered or pool ranges without interface mapping
        addr_ranges = parse_ip_address_ranges(interface)
        if addr_ranges:
            return [{"interface": interface or "ether1", "range": r} for r in addr_ranges]
        pool_ranges = parse_ip_pool_ranges()
        if pool_ranges:
            return [{"interface": interface or "ether1", "range": r} for r in pool_ranges]
        try:
            fallback_network = str(ipaddress.ip_interface(f"{router_ip}/24").network)
            return [{"interface": interface or "ether1", "range": fallback_network}]
        except Exception:
            return []

    devices: List[Dict[str, str]] = []
    seen_devices: set[tuple[str, str, str]] = set()
    seen_ips: set[str] = set()
    scan_paths = ["tool ip-scan"]
    dhcp_hostnames = parse_dhcp_leases() if use_dhcp_hostname else {
        "by_ip": {},
        "by_mac": {},
        "mac_by_ip": {},
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
    scan_targets = resolve_scan_targets()
    if not scan_targets:
        print(json.dumps({"status": "error", "message": "No address ranges found for scan."}))
        return
    scan_errors: List[str] = []

    for path in scan_paths:
        for target in scan_targets:
            target_range = target.get("range") or ""
            target_iface = target.get("interface") or None
            if not target_iface:
                continue
            attempts = [
                {"include_interface": True, "include_duration": True},
                {"include_interface": True, "include_duration": False},
            ]
            result = None
            output = ""
            combined_cmd = ""
            for attempt in attempts:
                combined_cmd = build_scan_command(
                    path,
                    target_range,
                    target_iface,
                    include_interface=attempt["include_interface"],
                    include_duration=attempt["include_duration"]
                )
                try:
                    result = run_ssh_command(router_ip, username, password, combined_cmd, timeout=duration + 15, port=ssh_port, wide_terminal=True)
                    output = (result.stdout or "") + "\n" + (result.stderr or "")
                    if result.returncode == 0 and not _routeros_command_error(output) and not _scan_output_has_rows(output):
                        result = run_ssh_command(router_ip, username, password, combined_cmd, timeout=duration + 15, port=ssh_port)
                except subprocess.TimeoutExpired as exc:
                    output = f"SSH command timed out after {exc.timeout}s while running: {combined_cmd}"
                    result = subprocess.CompletedProcess(args=["paramiko", combined_cmd], returncode=124, stdout="", stderr=output)
                except RuntimeError as exc:
                    print(json.dumps({"status": "error", "message": str(exc)}))
                    return

                output = (result.stdout or "") + "\n" + (result.stderr or "")
                if trace_output:
                    print(f"=== ip-scan command ===\n{combined_cmd}", file=sys.stderr)
                    print(f"=== ip-scan output ({path}) ===\n{output}", file=sys.stderr)
                if result.returncode == 0 and not _routeros_command_error(output):
                    break
                if ("Permission denied" in output or "Authentication failed" in output):
                    print(json.dumps({
                        "status": "error",
                        "message": "SSH authentication failed. Use correct credentials or install PuTTY/plink on Windows."
                    }))
                    return
                if _routeros_interface_error(output) and attempt["include_interface"]:
                    attempts.append({"include_interface": False, "include_duration": attempt["include_duration"]})
                    continue
                if _routeros_command_error(output):
                    continue
            if result is None or result.returncode != 0 or _routeros_command_error(output):
                err_text = output.strip()
                if err_text:
                    scan_errors.append(err_text[:1000])
                continue
            dhcp_hits = 0
            seen_ips.update(_extract_seen_ips_from_scan(output))
            for item in parse_ip_scan(output):
                if not item.get("interface") and target_iface:
                    item["interface"] = target_iface
                ip = item.get("ip")
                mac = item.get("mac")
                if ip:
                    seen_ips.add(ip)
                if ip and not mac:
                    lease_mac = dhcp_hostnames.get("mac_by_ip", {}).get(ip)
                    if lease_mac:
                        item["mac"] = lease_mac
                        item["identity"] = item.get("identity") or lease_mac
                        mac = lease_mac
                ip_key = (item.get("ip") or "").strip()
                mac_key = normalize_mac(item.get("mac") or "") if item.get("mac") else ""
                iface_key = (item.get("interface") or target_iface or "").strip()
                dedupe_key = ("mac", mac_key, iface_key) if mac_key else ("ip", ip_key, iface_key)
                if ip_key or mac_key:
                    if dedupe_key in seen_devices:
                        continue
                    seen_devices.add(dedupe_key)
                ip_match = dhcp_hostnames.get("by_ip", {}).get(ip) if ip else None
                mac_key = normalize_mac(mac) if mac else None
                mac_match = dhcp_hostnames.get("by_mac", {}).get(mac_key) if mac_key else None
                scan_hostname = (item.get("scan_hostname") or "").strip()
                netbios_label = (item.get("netbios") or "").strip()
                scan_has_real_name = bool(scan_hostname) and not is_mac_name(scan_hostname)
                scan_is_truncated = is_truncated_name(scan_hostname)
                is_catch_ip_thief = False
                if catch_ip_thieves and ip and not ip_match:
                    active_ips = dhcp_hostnames.get("active_ips", set())
                    lease_pairs = dhcp_hostnames.get("lease_pairs", set())
                    lease_match = False
                    if mac:
                        try:
                            lease_match = (ip, normalize_mac(mac)) in lease_pairs
                        except Exception:
                            lease_match = False
                    is_catch_ip_thief = ip not in active_ips and not lease_match
                if ip_match and (not scan_has_real_name or scan_is_truncated):
                    item["dhcp_hostname"] = ip_match
                    dhcp_hits += 1
                elif mac_match and not scan_has_real_name and not is_catch_ip_thief:
                    item["dhcp_hostname"] = mac_match
                    dhcp_hits += 1
                if not scan_has_real_name and netbios_label:
                    item["identity"] = item.get("dhcp_hostname") or netbios_label
                if is_catch_ip_thief:
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
        scan_name = d.get("scan_hostname") or ""
        netbios_name = d.get("netbios") or ""
        dhcp_name = d.get("dhcp_hostname") or ""
        name = scan_name or dhcp_name or netbios_name or vendor or mac
        record_scan_name = scan_name or netbios_name or name
        catch_ip_thief = bool(d.get("catch_ip_thief"))
        oui = normalize_mac(mac)[:8]
        rec = DeviceRecord(
            mac=mac,
            ip=ip,
            name=name,
            scan_name=record_scan_name,
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
    database = read_json_store(db_path, "devices")
    if database is None:
        print(json.dumps({"status": "error", "message": "Failed to read database"}))
        return

    now = datetime.now().isoformat()
    if "devices" not in database:
        database["devices"] = []

    def safe_site(site: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in site.lower())

    def locked_collision(rec: DeviceRecord) -> Optional[Dict[str, Any]]:
        rec_mac = (rec.mac or "").lower()
        for device in database["devices"]:
            if device.get("site") != site_name or not device.get("locked"):
                continue
            if rec_mac and (device.get("mac") or "").lower() == rec_mac:
                return device
            if rec.ip and device.get("ip") == rec.ip:
                return device
        return None

    def preferred_name_for_record(rec: DeviceRecord, dtype: str, existing_name: str = "") -> str:
        if rec.dhcp_name and not rec.catch_ip_thief:
            return rec.dhcp_name
        if rec.catch_ip_thief and dtype in ("pc", "pda", "unknown", ""):
            if not is_truncated_name(rec.scan_name):
                return f"Catched-{rec.scan_name}"
            return existing_name or rec.mac
        if dtype in ("pc", "pda"):
            if rec.name and not is_truncated_name(rec.name):
                return rec.name
            return existing_name
        if dtype not in ("pc", "pda", "unknown", "") and existing_name.startswith("Catched-"):
            return existing_name[len("Catched-"):]
        return existing_name or rec.name

    touched_device_ids: set[str] = set()
    for rec in records:
        mac_id = f"dev_mac_{rec.mac.replace(':', '').lower()}"
        device_id = f"{mac_id}_{safe_site(site_name)}"
        if locked_collision(rec):
            continue
        existing = next(
            (d for d in database["devices"] if d.get("mac", "").lower() == rec.mac.lower() and d.get("site") == site_name),
            None
        )
        matched_by_mac = existing is not None
        if not existing and replace_on_ip and rec.ip:
            existing = next(
                (d for d in database["devices"] if d.get("ip") == rec.ip and d.get("site") == site_name),
                None
        )

        if existing:
            if existing.get("locked"):
                continue
            touched_device_ids.add(existing.get("id") or "")
            existing_name = existing.get("name") or ""
            dtype = (existing.get("type") or "").strip().lower()
            changed = False
            if matched_by_mac or replace_on_ip:
                new_name = preferred_name_for_record(rec, dtype, existing_name)
                if new_name and existing.get("name") != new_name:
                    existing["name"] = new_name
                    changed = True
                updates = {
                    "ip": rec.ip or existing.get("ip"),
                    "vendor": rec.vendor or existing.get("vendor"),
                    "mac": rec.mac,
                    "oui": rec.oui or existing.get("oui"),
                    "discovered_by": "mikrotik_mac_discovery"
                }
            else:
                updates = {}
                if not existing.get("ip") and rec.ip:
                    updates["ip"] = rec.ip
                if not existing.get("vendor") and rec.vendor:
                    updates["vendor"] = rec.vendor
                if not existing.get("mac") and rec.mac:
                    updates["mac"] = rec.mac
                if not existing.get("oui") and rec.oui:
                    updates["oui"] = rec.oui
                if not existing.get("discovered_by"):
                    updates["discovered_by"] = "mikrotik_mac_discovery"
                if not existing_name:
                    new_name = preferred_name_for_record(rec, dtype, existing_name)
                    if new_name:
                        updates["name"] = new_name
            for key, value in updates.items():
                if value is not None and existing.get(key) != value:
                    existing[key] = value
                    changed = True
            if note and replace_on_ip:
                existing["notes"] = f"{existing.get('notes', '')} {note}".strip()
                changed = True
            existing["last_seen"] = now
            if changed:
                existing["last_modified"] = now
            devices_updated += 1
        else:
            name_value = rec.name
            if rec.dhcp_name and not rec.catch_ip_thief:
                name_value = rec.dhcp_name
            elif rec.catch_ip_thief:
                name_value = f"Catched-{rec.scan_name}" if not is_truncated_name(rec.scan_name) else rec.mac
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
            touched_device_ids.add(device_id)
            devices_added += 1

    availability_updated = 0
    if seen_ips:
        for device in database.get("devices", []):
            if device.get("site") != site_name:
                continue
            if device.get("locked"):
                continue
            device_id_existing = device.get("id") or ""
            if device_id_existing in touched_device_ids:
                continue
            if (device.get("ip") or "").strip() not in seen_ips:
                continue
            device["last_seen"] = now
            touched_device_ids.add(device_id_existing)
            availability_updated += 1
        devices_updated += availability_updated

    database.setdefault("meta", {})["last_modified"] = now
    try:
        write_json_store(db_path, "devices", database)
    except Exception:
        print(json.dumps({"status": "error", "message": "Failed to write database"}))
        return

    result_payload = {
        "status": "success",
        "site": site_name,
        "devices_found": len(records),
        "devices_added": devices_added,
        "devices_updated": devices_updated,
        "availability_updated": availability_updated,
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
