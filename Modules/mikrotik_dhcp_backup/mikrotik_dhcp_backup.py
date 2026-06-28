#!/usr/bin/env python3
"""
MikroTik DHCP Backup

Runs RouterOS `export` over SSH and stores the text output in
backups/mikrotik_dhcp under the CMapper program directory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import warnings
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", ".."))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

from sqlite_store import read_json_store


def _append_log(path: Optional[str], message: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _find_executable(names: list[str]) -> Optional[str]:
    for name in names:
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(path_dir, name)
            if os.path.isfile(candidate):
                return candidate
        if os.name == "nt":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            for candidate in (
                os.path.join(system_root, "System32", "OpenSSH", name),
                os.path.join(system_root, "System32", name),
            ):
                if os.path.isfile(candidate):
                    return candidate
    return None


def _run_paramiko(host: str, username: str, password: str, port: int, timeout: int) -> Optional[subprocess.CompletedProcess]:
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
            host,
            port=port,
            username=username,
            password=password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        channel = client.get_transport().open_session(timeout=10)
        channel.settimeout(2)
        channel.exec_command("export")
        deadline = time.time() + max(timeout, 1)
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
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
                raise subprocess.TimeoutExpired(cmd=["paramiko", "export"], timeout=timeout)
            time.sleep(0.05)
        return subprocess.CompletedProcess(
            args=["paramiko", "export"],
            returncode=channel.recv_exit_status(),
            stdout=b"".join(out_chunks).decode(errors="ignore"),
            stderr=b"".join(err_chunks).decode(errors="ignore"),
        )
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        client.close()


def _run_export(host: str, username: str, password: str, port: int, timeout: int) -> subprocess.CompletedProcess:
    paramiko_result = _run_paramiko(host, username, password, port, timeout)
    if paramiko_result is not None:
        return paramiko_result

    if os.name == "nt":
        plink_bin = os.environ.get("PLINK_BIN") or _find_executable(["plink.exe", "plink"])
        if plink_bin:
            return subprocess.run(
                [plink_bin, "-ssh", "-batch", "-P", str(port), "-pw", password, f"{username}@{host}", "export"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

    sshpass_bin = os.environ.get("SSHPASS_BIN") or _find_executable(["sshpass"])
    if sshpass_bin:
        return subprocess.run(
            [
                sshpass_bin,
                "-p",
                password,
                "ssh",
                "-p",
                str(port),
                "-o",
                "StrictHostKeyChecking=no",
                f"{username}@{host}",
                "export",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    ssh_bin = _find_executable(["ssh"])
    if not ssh_bin:
        raise RuntimeError("No SSH client found. Install paramiko, OpenSSH, sshpass, or PuTTY/plink.")
    return subprocess.run(
        [
            ssh_bin,
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=no",
            f"{username}@{host}",
            "export",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _clean_export_output(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", value)
    return value.strip() + "\n" if value.strip() else ""


def _safe_part(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    text = text.strip("._-")
    return text[:80] if text else fallback


def _delete_previous_backups(
    backup_dir: str,
    current_path: str,
    site_part: str,
    ip_part: str,
) -> Tuple[list[str], list[str]]:
    pattern = re.compile(
        rf"^\d{{8}}_\d{{6}}_{re.escape(site_part)}_.+_{re.escape(ip_part)}(?:_\d+)?\.txt$"
    )
    deleted: list[str] = []
    failures: list[str] = []
    current_path = os.path.abspath(current_path)
    for entry in os.scandir(backup_dir):
        if not entry.is_file() or not pattern.match(entry.name):
            continue
        if os.path.abspath(entry.path) == current_path:
            continue
        try:
            os.remove(entry.path)
            deleted.append(entry.path)
        except OSError as exc:
            failures.append(f"{entry.path}: {exc}")
    return deleted, failures


def _resolve_router(config: Dict[str, Any], params: Dict[str, Any]) -> Tuple[str, str]:
    router_ip = str(params.get("router_ip") or "").strip()
    router_name = router_ip
    router_device_id = str(params.get("router_device_id") or "").strip()
    db_path = config.get("database_path")
    site_name = str(config.get("site_name") or "").strip()

    data: Dict[str, Any] = {}
    if db_path and os.path.exists(db_path):
        data = read_json_store(db_path, "devices") or {}

    if router_device_id:
        for device in data.get("devices", []):
            if not isinstance(device, dict):
                continue
            if device.get("id") != router_device_id:
                continue
            if site_name and device.get("site") != site_name:
                continue
            router_ip = str(device.get("ip") or router_ip).strip()
            router_name = str(device.get("name") or router_ip).strip()
            break

    if not router_ip and site_name:
        for device in data.get("devices", []):
            if not isinstance(device, dict):
                continue
            if device.get("site") != site_name:
                continue
            if (device.get("type") or "").lower() not in ("server", "router"):
                continue
            name = (device.get("name") or "").lower()
            vendor = (device.get("vendor") or "").lower()
            if "mikrotik" not in name and "mikrotik" not in vendor and "dhcp" not in name:
                continue
            router_ip = str(device.get("ip") or "").strip()
            router_name = str(device.get("name") or router_ip).strip()
            if router_ip:
                break

    if not router_ip:
        raise ValueError("Router IP is required. Select a router device or enter Router IP.")
    if not router_name:
        router_name = router_ip
    return router_ip, router_name


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = _load_config(sys.argv[1])
    params = config.get("parameters", {}) if isinstance(config.get("parameters"), dict) else {}
    log_file = config.get("log_file")
    site_name = str(config.get("site_name") or "").strip()

    username = str(params.get("username") or "").strip()
    password = params.get("password")
    ssh_port = _parse_int(params.get("ssh_port"), 22, 1, 65535)
    timeout_s = _parse_int(params.get("timeout_s"), 90, 15, 600)

    if not username or password is None:
        print(json.dumps({"status": "error", "message": "Missing username/password"}))
        return

    try:
        router_ip, router_name = _resolve_router(config, params)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return

    started = datetime.now()
    _append_log(log_file, f"MIKROTIK DHCP BACKUP {started.isoformat()}")
    _append_log(log_file, f"Site: {site_name or 'N/A'}")
    _append_log(log_file, f"Router: {router_name} ({router_ip}) port={ssh_port}")
    _append_log(log_file, "Command: export")

    try:
        result = _run_export(router_ip, username, str(password), ssh_port, timeout_s)
    except subprocess.TimeoutExpired:
        message = f"Export timed out after {timeout_s}s"
        _append_log(log_file, f"ERROR: {message}")
        print(json.dumps({"status": "error", "message": message, "router_ip": router_ip}))
        return
    except Exception as exc:
        _append_log(log_file, f"ERROR: {exc}")
        print(json.dumps({"status": "error", "message": str(exc), "router_ip": router_ip}))
        return

    export_text = _clean_export_output(result.stdout)
    stderr_text = (result.stderr or "").strip()
    if result.returncode != 0:
        message = stderr_text or f"export returned code {result.returncode}"
        _append_log(log_file, f"ERROR: {message}")
        print(json.dumps({"status": "error", "message": message, "router_ip": router_ip, "returncode": result.returncode}))
        return
    if not export_text:
        message = stderr_text or "export returned empty output"
        _append_log(log_file, f"ERROR: {message}")
        print(json.dumps({"status": "error", "message": message, "router_ip": router_ip}))
        return

    backup_dir = os.path.join(BASE_DIR, "backups", "mikrotik_dhcp")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = started.strftime("%Y%m%d_%H%M%S")
    site_part = _safe_part(site_name, "site")
    router_part = _safe_part(router_name, router_ip.replace(".", "_"))
    ip_part = router_ip.replace(".", "_")
    filename = f"{timestamp}_{site_part}_{router_part}_{ip_part}.txt"
    backup_path = os.path.join(backup_dir, filename)
    counter = 2
    while os.path.exists(backup_path):
        filename = f"{timestamp}_{site_part}_{router_part}_{ip_part}_{counter}.txt"
        backup_path = os.path.join(backup_dir, filename)
        counter += 1

    with open(backup_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(export_text)

    deleted_backups, delete_failures = _delete_previous_backups(
        backup_dir,
        backup_path,
        site_part,
        ip_part,
    )

    duration_s = round((datetime.now() - started).total_seconds(), 3)
    _append_log(log_file, f"SAVED: {backup_path}")
    _append_log(log_file, f"Bytes: {os.path.getsize(backup_path)}")
    for deleted_path in deleted_backups:
        _append_log(log_file, f"DELETED OLD BACKUP: {deleted_path}")
    for failure in delete_failures:
        _append_log(log_file, f"WARNING: could not delete old backup: {failure}")
    _append_log(log_file, f"Duration: {duration_s}s")

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "router_name": router_name,
        "router_ip": router_ip,
        "backup_path": backup_path,
        "bytes": os.path.getsize(backup_path),
        "deleted_old_backups": len(deleted_backups),
        "delete_warnings": delete_failures,
        "duration_s": duration_s
    }))


if __name__ == "__main__":
    main()
