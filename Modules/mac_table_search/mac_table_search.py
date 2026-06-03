#!/usr/bin/env python3
"""Trace a MAC address through Cisco switch trunk links to an access port."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import uuid
import warnings
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)
from sqlite_store import read_json_store, write_json_store


def log(path: Optional[str], message: str) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def normalize_mac(value: str) -> str:
    raw = re.sub(r"[^0-9a-fA-F]", "", value or "").lower()
    return raw if len(raw) == 12 else ""


def cisco_mac(value: str) -> str:
    return f"{value[0:4]}.{value[4:8]}.{value[8:12]}"


def device_by_id(devices: list[Dict[str, Any]], device_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in devices if item.get("id") == device_id), None)


def device_by_ip(devices: list[Dict[str, Any]], ip: str) -> Optional[Dict[str, Any]]:
    return next((item for item in devices if item.get("ip") == ip), None)


def device_by_mac(devices: list[Dict[str, Any]], mac: str) -> Optional[Dict[str, Any]]:
    wanted = normalize_mac(mac)
    return next((item for item in devices if normalize_mac(str(item.get("mac") or "")) == wanted), None)


def display_mac(value: str) -> str:
    return ":".join(value[index:index + 2] for index in range(0, 12, 2)).upper()


def display_port(value: str) -> str:
    return re.sub(
        r"^(?:TenGigabitEthernet|GigabitEthernet|FastEthernet|Te|Gi|Fa)",
        "",
        str(value or ""),
        flags=re.IGNORECASE,
    ) or str(value or "")


class CiscoSession:
    def __init__(self, host: str, username: str, password: str, port: int, telnet_port: int = 23) -> None:
        self.host = host
        self.kind = ""
        self.connection = None
        self.channel = None
        self.socket = None
        errors = []
        try:
            from netmiko import ConnectHandler
            self.connection = ConnectHandler(
                device_type="cisco_ios",
                host=host,
                port=port,
                username=username,
                password=password,
                timeout=15,
                global_delay_factor=2,
            )
            self.kind = "netmiko"
            return
        except ImportError:
            pass
        except Exception as exc:
            errors.append(f"SSH-Netmiko: {str(exc)[:120]}")

        try:
            warnings.filterwarnings("ignore", message=r"TripleDES has been moved.*")
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )
            self.connection = client
            self.channel = client.invoke_shell()
            time.sleep(0.6)
            self._read_available()
            self.channel.send("terminal length 0\n")
            time.sleep(0.3)
            self._read_available()
            self.kind = "paramiko"
            return
        except Exception as exc:
            errors.append(f"SSH: {str(exc)[:120]}")
            try:
                client.close()
            except Exception:
                pass

        try:
            self._connect_telnet(username, password, telnet_port)
            self.kind = "telnet"
            return
        except Exception as exc:
            errors.append(f"Telnet: {str(exc)[:120]}")
            self.close()
            raise RuntimeError("; ".join(errors)) from exc

    @property
    def transport_label(self) -> str:
        return "Telnet" if self.kind == "telnet" else "SSH"

    def _telnet_decode(self, data: bytes) -> str:
        clean = bytearray()
        index = 0
        while index < len(data):
            value = data[index]
            if value != 255:
                clean.append(value)
                index += 1
                continue
            if index + 1 >= len(data):
                break
            command = data[index + 1]
            if command == 255:
                clean.append(255)
                index += 2
            elif command in (251, 252, 253, 254) and index + 2 < len(data):
                option = data[index + 2]
                if command == 251:
                    self.socket.sendall(bytes((255, 254, option)))
                elif command == 253:
                    self.socket.sendall(bytes((255, 252, option)))
                index += 3
            elif command == 250:
                end = data.find(bytes((255, 240)), index + 2)
                index = len(data) if end == -1 else end + 2
            else:
                index += 2
        return clean.decode("utf-8", errors="ignore")

    def _telnet_send(self, text: str) -> None:
        if not self.socket:
            raise RuntimeError("Telnet socket is not connected")
        self.socket.sendall((text + "\n").encode("utf-8"))

    def _telnet_read_until(self, pattern: str, timeout: float = 10.0) -> str:
        if not self.socket:
            return ""
        output = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(pattern, output, flags=re.IGNORECASE | re.MULTILINE):
                break
            self.socket.settimeout(min(0.5, max(0.1, deadline - time.time())))
            try:
                chunk = self.socket.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                break
            output += self._telnet_decode(chunk)
        return output

    def _connect_telnet(self, username: str, password: str, port: int) -> None:
        self.socket = socket.create_connection((self.host, port), timeout=15)
        login_pattern = r"(?:username|login)\s*:|password\s*:|[^\r\n]+[>#]\s*$"
        output = self._telnet_read_until(login_pattern, timeout=5)
        if not output:
            self._telnet_send("")
            output = self._telnet_read_until(login_pattern, timeout=5)
        if re.search(r"(?:username|login)\s*:", output, flags=re.IGNORECASE):
            self._telnet_send(username)
            output += self._telnet_read_until(r"password\s*:|[^\r\n]+[>#]\s*$", timeout=5)
        if re.search(r"password\s*:", output, flags=re.IGNORECASE):
            self._telnet_send(password)
            output += self._telnet_read_until(r"[^\r\n]+[>#]\s*$|invalid|failed|denied", timeout=8)
        if re.search(r"invalid|failed|denied", output, flags=re.IGNORECASE):
            raise RuntimeError("authentication failed")
        if not re.search(r"[^\r\n]+[>#]\s*$", output, flags=re.MULTILINE):
            raise RuntimeError("no Cisco prompt after login")
        self._telnet_send("terminal length 0")
        self._telnet_read_until(r"[^\r\n]+[>#]\s*$", timeout=5)

    def _read_available(self) -> str:
        output = ""
        if not self.channel:
            return output
        while self.channel.recv_ready():
            output += self.channel.recv(8192).decode("utf-8", errors="ignore")
        return output

    def command(self, command: str) -> str:
        if self.kind == "netmiko":
            return self.connection.send_command(command, delay_factor=2, expect_string=r"[#>]")
        if self.kind == "telnet":
            self._telnet_send(command)
            return self._telnet_read_until(r"[^\r\n]+[>#]\s*$", timeout=10)
        self.channel.send(command + "\n")
        output = ""
        deadline = time.time() + 10
        time.sleep(0.3)
        while time.time() < deadline:
            if self.channel.recv_ready():
                output += self.channel.recv(8192).decode("utf-8", errors="ignore")
                if re.search(r"[A-Za-z0-9_.()-]+[#>]\s*$", output):
                    break
            time.sleep(0.1)
        return output

    def hostname(self) -> str:
        if self.kind == "netmiko":
            return (self.connection.find_prompt() or self.host).strip().rstrip("#>")
        output = self.command("show running-config | include ^hostname")
        match = re.search(r"(?mi)^hostname\s+(\S+)", output)
        return match.group(1) if match else self.host

    def close(self) -> None:
        try:
            if self.kind == "netmiko":
                self.connection.disconnect()
            elif self.kind == "telnet":
                self.socket.close()
            else:
                if self.channel:
                    self.channel.close()
                if self.connection:
                    self.connection.close()
                if self.socket:
                    self.socket.close()
        except Exception:
            pass


def parse_mac_entry(output: str, wanted: str) -> Optional[Dict[str, str]]:
    wanted_flat = normalize_mac(wanted)
    for line in output.splitlines():
        match = re.search(
            r"^\s*[*+ -]?\s*(?P<vlan>\d+)\s+(?P<mac>[0-9a-fA-F.:-]+)\s+(?P<entry_type>\S+)\s+(?P<port>\S+)\s*$",
            line,
        )
        if match and normalize_mac(match.group("mac")) == wanted_flat:
            return match.groupdict()
    return None


def port_is_trunk(output: str, port: str) -> bool:
    port_key = port.lower().replace("gigabitethernet", "gi").replace("fastethernet", "fa")
    for line in output.splitlines():
        normalized = line.strip().lower()
        if normalized.startswith(port_key) and re.search(r"\btrunk\b", normalized):
            return True
    return False


def cdp_neighbor(output: str) -> Tuple[str, str, str]:
    name_match = re.search(r"(?mi)^Device ID:\s*(.+?)\s*$", output)
    ip_match = re.search(r"(?mi)(?:IP address|IPv4 Address):\s*(\d+\.\d+\.\d+\.\d+)", output)
    port_match = re.search(r"(?mi)^Port ID \(outgoing port\):\s*(.+?)\s*$", output)
    return (
        name_match.group(1).strip() if name_match else "",
        ip_match.group(1) if ip_match else "",
        port_match.group(1).strip() if port_match else "",
    )


def database_neighbor(devices: list[Dict[str, Any]], switch: Dict[str, Any], port: str) -> Tuple[str, str, str]:
    port_key = port.lower()
    for connection in switch.get("connections") or []:
        local = str(connection.get("local_interface") or "").lower()
        if port_key not in local and local not in port_key:
            continue
        remote = device_by_id(devices, connection.get("remote_device") or "")
        if remote:
            return (
                str(remote.get("name") or ""),
                str(remote.get("ip") or ""),
                str(connection.get("remote_interface") or ""),
            )
    switch_id = str(switch.get("id") or "")
    if switch_id:
        for remote in devices:
            for connection in remote.get("connections") or []:
                if str(connection.get("remote_device") or "") != switch_id:
                    continue
                remote_port = str(connection.get("remote_interface") or "").lower()
                if port_key in remote_port or remote_port in port_key:
                    return (
                        str(remote.get("name") or ""),
                        str(remote.get("ip") or ""),
                        str(connection.get("local_interface") or ""),
                    )
    return "", "", ""


def ensure_switch(
    devices_data: Dict[str, Any],
    site: str,
    ip: str,
    name: str,
    now: str,
    source_module: str = "mac_table_search",
    write_notes: bool = True,
) -> Dict[str, Any]:
    devices = devices_data.setdefault("devices", [])
    switch = next((item for item in devices if item.get("site") == site and item.get("ip") == ip), None)
    if switch:
        if switch.get("locked"):
            return switch
        if name and (not switch.get("name") or str(switch.get("name")).startswith("Device-")):
            switch["name"] = name
        if not switch.get("type") or switch.get("type") == "unknown":
            switch["type"] = "switch"
        switch["last_modified"] = now
        return switch
    switch = {
        "id": f"dev_{str(uuid.uuid4())[:8]}",
        "site": site,
        "name": name or f"Switch-{ip}",
        "ip": ip,
        "mac": "",
        "type": "switch",
        "model": "",
        "platform": "Cisco",
        "discovered_by": source_module,
        "discovered_at": now,
        "last_seen": now,
        "last_modified": now,
        "status": "unknown",
        "reachable": True,
        "config_backup": {"enabled": False},
        "connections": [],
        "credentials_used": None,
        "modules_successful": [source_module],
        "modules_failed": [],
        "locked": False,
        "notes": "Discovered while tracing a MAC address" if write_notes else ""
    }
    devices.append(switch)
    return switch


def ensure_target(
    devices_data: Dict[str, Any],
    site: str,
    selected: Optional[Dict[str, Any]],
    target_mac: str,
    now: str,
    source_module: str = "mac_table_search",
) -> Dict[str, Any]:
    devices = devices_data.setdefault("devices", [])
    site_devices = [item for item in devices if item.get("site") == site]
    target = selected or device_by_mac(site_devices, target_mac)
    if target:
        if target.get("locked"):
            return target
        target["mac"] = display_mac(target_mac)
        target["last_modified"] = now
        return target
    target = {
        "id": f"dev_{str(uuid.uuid4())[:8]}",
        "site": site,
        "name": f"MAC-{display_mac(target_mac)}",
        "ip": "",
        "mac": display_mac(target_mac),
        "type": "unknown",
        "model": "",
        "platform": "",
        "discovered_by": source_module,
        "discovered_at": now,
        "last_seen": now,
        "last_modified": now,
        "status": "unknown",
        "reachable": False,
        "config_backup": {"enabled": False},
        "connections": [],
        "credentials_used": None,
        "modules_successful": [source_module],
        "modules_failed": [],
        "locked": False,
        "notes": ""
    }
    devices.append(target)
    return target


def upsert_connection(
    device: Dict[str, Any],
    remote: Dict[str, Any],
    local_interface: str,
    remote_interface: str,
    now: str,
) -> None:
    connections = device.setdefault("connections", [])
    existing = next((
        conn for conn in connections
        if conn.get("remote_device") == remote.get("id")
        and (
            conn.get("protocol") == "mac_search"
            or (local_interface and conn.get("local_interface") == local_interface)
            or (remote_interface and conn.get("remote_interface") == remote_interface)
        )
    ), None)
    if existing:
        if local_interface:
            existing["local_interface"] = local_interface
        if remote_interface:
            existing["remote_interface"] = remote_interface
        existing["remote_name"] = remote.get("name") or existing.get("remote_name", "")
        existing["remote_ip"] = remote.get("ip") or existing.get("remote_ip", "")
        existing["remote_mac"] = remote.get("mac") or existing.get("remote_mac", "")
        existing["discovered_at"] = now
        existing["status"] = "up"
        return
    connections.append({
        "id": f"conn_{str(uuid.uuid4())[:8]}",
        "local_interface": local_interface,
        "remote_device": remote["id"],
        "remote_name": remote.get("name") or "",
        "remote_ip": remote.get("ip") or "",
        "remote_mac": remote.get("mac") or "",
        "remote_interface": remote_interface,
        "protocol": "mac_search",
        "discovered_at": now,
        "status": "up"
    })


def update_target_notes(target: Dict[str, Any], mac_display: str, trace: list[Dict[str, Any]], now: str) -> None:
    lines = ["[MAC SEARCH]", f"Updated: {now}", f"MAC: {mac_display}", "Path:"]
    for hop, row in enumerate(trace, 1):
        mode = "trunk" if row.get("trunk") else "access"
        lines.append(f"{hop}. {row['switch_name']} ({row['switch_ip']}) Port {display_port(row['port'])} [{mode}]")
    final = trace[-1]
    lines.append(f"Found in: {final['switch_name']} ({final['switch_ip']}) Port {display_port(final['port'])}")
    lines.append("[/MAC SEARCH]")
    block = "\n".join(lines)
    notes = str(target.get("notes") or "").strip()
    marker = re.compile(r"\n?\[MAC SEARCH\].*?\[/MAC SEARCH\]", re.DOTALL)
    notes = marker.sub("", notes).strip()
    target["notes"] = f"{notes}\n\n{block}".strip() if notes else block


def save_trace(
    db_path: str,
    devices_data: Dict[str, Any],
    site: str,
    selected_target: Optional[Dict[str, Any]],
    target_mac: str,
    mac_display: str,
    trace: list[Dict[str, Any]],
    write_notes: bool = True,
    source_module: str = "mac_table_search",
) -> Tuple[Dict[str, Any], int]:
    now = datetime.now().isoformat()
    target = ensure_target(devices_data, site, selected_target, target_mac, now, source_module)
    if target.get("locked"):
        return target, 0
    switches = [
        ensure_switch(devices_data, site, row["switch_ip"], row["switch_name"], now, source_module, write_notes)
        for row in trace
    ]
    connection_count = 0
    for index, row in enumerate(trace[:-1]):
        local_switch = switches[index]
        next_switch = switches[index + 1]
        remote_port = str(row.get("next_port") or "")
        upsert_connection(local_switch, next_switch, row["port"], remote_port, now)
        upsert_connection(next_switch, local_switch, remote_port, row["port"], now)
        connection_count += 1
    final_switch = switches[-1]
    final_port = str(trace[-1]["port"])
    target_id = target["id"]
    for device in devices_data.get("devices", []):
        if device.get("id") == target_id:
            device["connections"] = [
                conn for conn in device.get("connections") or []
                if conn.get("protocol") != "mac_search"
            ]
        else:
            device["connections"] = [
                conn for conn in device.get("connections") or []
                if not (
                    conn.get("remote_device") == target_id
                    and conn.get("protocol") == "mac_search"
                )
            ]
    upsert_connection(final_switch, target, final_port, "", now)
    upsert_connection(target, final_switch, "", final_port, now)
    connection_count += 1
    target["last_seen"] = now
    target["last_modified"] = now
    target.setdefault("modules_successful", [])
    if source_module not in target["modules_successful"]:
        target["modules_successful"].append(source_module)
    if write_notes:
        update_target_notes(target, mac_display, trace, now)
    devices_data.setdefault("meta", {})["last_modified"] = now
    write_json_store(db_path, "devices", devices_data)
    return target, connection_count


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        config = json.load(handle)
    params = config.get("parameters") or {}
    log_file = config.get("log_file")
    site = str(config.get("site_name") or "")
    db_path = str(config.get("database_path") or "")
    devices_data = read_json_store(db_path, "devices") or {}
    devices = [d for d in devices_data.get("devices", []) if d.get("site") == site]

    target = device_by_id(devices, str(params.get("target_device_id") or ""))
    raw_mac = str(params.get("manual_mac") or "").strip() or str((target or {}).get("mac") or "")
    target_mac = normalize_mac(raw_mac)
    if not target_mac:
        print(json.dumps({"status": "error", "message": "Select a device with a MAC or enter a valid MAC address"}))
        return

    start = device_by_id(devices, str(params.get("start_switch_id") or ""))
    current_ip = str(params.get("start_switch_ip") or "").strip() or str((start or {}).get("ip") or "")
    if not current_ip:
        print(json.dumps({"status": "error", "message": "Select a starting switch or enter its IP address"}))
        return

    username = str(params.get("username") or "").strip()
    password = str(params.get("password") or "")
    port = int(params.get("ssh_port") or 22)
    telnet_port = int(params.get("telnet_port") or 23)
    max_hops = max(1, min(int(params.get("max_hops") or 10), 30))
    mac_display = cisco_mac(target_mac)
    target_name = (target or {}).get("name") or raw_mac
    visited = set()
    trace = []

    if log_file:
        with open(log_file, "w", encoding="utf-8") as handle:
            handle.write(f"MAC SEARCH - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    log(log_file, f"Device: {target_name} ({display_mac(target_mac)})")
    log(log_file, f"Searching from: {current_ip}")
    log(log_file, "")
    log(log_file, "Trace:")

    for hop in range(1, max_hops + 1):
        if current_ip in visited:
            log(log_file, f"STOP: loop detected at {current_ip}")
            break
        visited.add(current_ip)
        inventory_switch = device_by_ip(devices, current_ip) or {}
        expected_name = str(inventory_switch.get("name") or "").strip()
        switch_label = f"{expected_name} ({current_ip})" if expected_name else current_ip
        session = None
        try:
            session = CiscoSession(current_ip, username, password, port, telnet_port)
            discovered_name = session.hostname()
            hostname = (
                discovered_name
                if discovered_name and discovered_name != current_ip
                else inventory_switch.get("name") or current_ip
            )
            output = session.command(f"show mac address-table | include {mac_display}")
            found = parse_mac_entry(output, mac_display)
            if not found:
                log(log_file, f"{hop}. {hostname} ({current_ip}): MAC not found")
                print(json.dumps({"status": "success", "found": False, "trace": trace, "message": "MAC not found on current path"}))
                return
            switch_port = found["port"]
            status = session.command(f"show interfaces {switch_port} status")
            is_trunk = port_is_trunk(status, switch_port)
            row = {
                "switch_name": hostname,
                "switch_ip": current_ip,
                "port": switch_port,
                "vlan": found["vlan"],
                "entry_type": found["entry_type"],
                "trunk": is_trunk,
                "transport": session.transport_label,
            }
            trace.append(row)
            mode = "trunk" if is_trunk else "access"
            log(log_file, f"{hop}. {hostname} ({current_ip}) Port {display_port(switch_port)} [{mode}; via {session.transport_label}]")
            if not is_trunk:
                log(log_file, "")
                log(log_file, f"Found in: {hostname} ({current_ip}) Port {display_port(switch_port)}")
                target_record, links_written = save_trace(
                    db_path,
                    devices_data,
                    site,
                    target,
                    target_mac,
                    mac_display,
                    trace,
                )
                log(log_file, f"Mapped: {links_written} connection(s)")
                log(log_file, f"Saved in notes: {target_record.get('name')}")
                print(json.dumps({
                    "status": "success",
                    "found": True,
                    "location": row,
                    "trace": trace,
                    "mapped_connections": links_written,
                    "target_device_id": target_record.get("id"),
                }))
                return

            cdp_output = session.command(f"show cdp neighbors interface {switch_port} detail")
            next_name, next_ip, next_port = cdp_neighbor(cdp_output)
            if not next_ip:
                cdp_output = session.command(f"show cdp neighbor {switch_port} detail")
                next_name, next_ip, next_port = cdp_neighbor(cdp_output)
            if not next_ip:
                next_name, next_ip, next_port = database_neighbor(devices, inventory_switch, switch_port)
            if not next_ip:
                log(log_file, f"STOP: {switch_port} is trunk, but no CDP neighbor IP was found")
                break
            row["next_switch_name"] = next_name
            row["next_switch_ip"] = next_ip
            row["next_port"] = next_port
            remote_label = f" Port {display_port(next_port)}" if next_port else ""
            log(log_file, f"   -> {next_name or 'Next switch'} ({next_ip}){remote_label}")
            current_ip = next_ip
        except Exception as exc:
            message = str(exc)[:240]
            if "authentication failed" in message.lower():
                log(log_file, f"AUTH FAIL: {switch_label} rejected credentials for user {username} over SSH and Telnet")
            else:
                log(log_file, f"ERROR: could not query {current_ip}: {message}")
            break
        finally:
            if session:
                session.close()

    print(json.dumps({"status": "success", "found": False, "trace": trace, "message": "MAC trace did not reach an access port"}))


if __name__ == "__main__":
    main()
