#!/usr/bin/env python3
"""
Test NVR HARD module

Logs into NVR using Digest auth and fetches HDD status from:
/LAPI/V1.0/Storage/Containers/DetailInfos
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_db(path: str) -> Dict[str, Any]:
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT json FROM json_store WHERE name='devices'").fetchone()
    conn.close()
    if not row:
        return {}
    return json.loads(row[0])


def _append_log(log_file: str | None, message: str) -> None:
    if not log_file:
        return
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def _normalize_targets(db: Dict[str, Any], site_name: str, targets: Dict[str, Any]) -> List[Dict[str, Any]]:
    devices = [d for d in db.get("devices", []) if d.get("site") == site_name and d.get("ip")]
    device_ids = targets.get("device_ids") or []
    manual_devices = targets.get("manual_devices") or []
    auto_targets = bool(targets.get("auto"))
    if device_ids:
        wanted = set(device_ids)
        devices = [d for d in devices if d.get("id") in wanted]
    elif auto_targets:
        devices = [d for d in devices if (d.get("type") or "").lower() == "nvr"]
    manual_list = []
    for entry in manual_devices:
        ip = (entry.get("ip") or "").strip()
        if not ip:
            continue
        manual_list.append({
            "id": f"manual_{ip}",
            "ip": ip,
            "name": entry.get("name") or ip
        })
    return devices + manual_list


def _fetch_hdd_status(ip: str, username: str, password: str, timeout: int, verify_ssl: bool, trace: bool, log_file: str | None) -> Tuple[bool, Dict[str, Any]]:
    try:
        import requests
        from requests.auth import HTTPDigestAuth
    except Exception as exc:
        return False, {"error": f"requests missing: {exc}"}

    base = f"http://{ip}"
    session = requests.Session()
    auth = HTTPDigestAuth(username, password)
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "CMapper-TestNVRHARD/1.0"
    }

    def _url(path: str) -> str:
        return f"{base}{path}"

    # Use same login flow as Uniview module
    login_url = _url("/LAPI/V1.0/System/Security/Login")
    try:
        if trace:
            _append_log(log_file, f"LOGIN {ip} {login_url}")
        login_resp = session.put(login_url, auth=auth, timeout=timeout, verify=verify_ssl)
        if login_resp.status_code not in (200, 204):
            if trace:
                _append_log(log_file, f"LOGIN FAIL {ip} {login_resp.status_code}")
                _append_log(log_file, f"LOGIN BODY {ip} {login_resp.text[:300]}")
            return False, {"error": f"login_fail_{login_resp.status_code}", "body": login_resp.text[:500]}
    except Exception as exc:
        if trace:
            _append_log(log_file, f"LOGIN {ip} EXC {exc}")
        return False, {"error": f"login_exception: {exc}"}

    def _keepalive() -> None:
        try:
            keepalive_url = _url("/LAPI/V1.0/System/Security/KeepAlive")
            session.put(keepalive_url, auth=auth, timeout=timeout, verify=verify_ssl)
        except Exception:
            return

    url = f"{base}/LAPI/V1.0/Storage/Containers/DetailInfos"
    try:
        _keepalive()
        resp = session.get(url, auth=auth, headers=headers, timeout=timeout, verify=verify_ssl)
        if trace:
            _append_log(log_file, f"GET {ip} {resp.status_code} {url}")
        if resp.status_code != 200:
            return False, {"error": f"http_{resp.status_code}", "body": resp.text[:500]}
        payload = resp.json()
        return True, {"data": payload}
    except Exception as exc:
        return False, {"error": str(exc)}


def _is_disk_ok(status: Any) -> bool:
    try:
        return int(status) == 3
    except Exception:
        return False


def _render_html(site_name: str, results: List[Dict[str, Any]]) -> str:
    cards = []
    for entry in results:
        name = entry.get("name") or entry.get("ip") or "Unknown"
        ip = entry.get("ip") or "?"
        ok = bool(entry.get("ok"))
        data = (entry.get("data") or {}).get("Response", {}).get("Data", {})
        disks = data.get("LocalHDDList") or []
        if ok and disks:
            disk_lines = []
            for disk in disks:
                status = disk.get("Status")
                good = _is_disk_ok(status)
                cls = "ok" if good else "bad"
                disk_lines.append(
                    f"<li class='disk {cls}'>"
                    f"HDD {disk.get('ID','?')}: status {status} • "
                    f"{disk.get('Manufacturer','')} • "
                    f"{disk.get('TotalCapacity','?')}MB</li>"
                )
            detail = "<ul class='disk-list'>" + "".join(disk_lines) + "</ul>"
        else:
            detail = f"<div class='error'>{entry.get('error','No data')}</div>"

        card_cls = "ok" if ok else "bad"
        cards.append(
            f"<div class='card {card_cls}'>"
            f"<div class='title'>{name} <span class='ip'>{ip}</span></div>"
            f"{detail}</div>"
        )

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>NVR HDD Status - {site_name}</title>
  <style>
    body {{ font-family: Arial, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:20px; }}
    h1 {{ margin:0 0 16px 0; font-size:20px; }}
    .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap:12px; }}
    .card {{ background:#111827; border:1px solid #1f2937; border-radius:12px; padding:12px; }}
    .card.ok {{ border-color:#22c55e; }}
    .card.bad {{ border-color:#ef4444; }}
    .title {{ font-weight:600; margin-bottom:8px; }}
    .ip {{ color:#93c5fd; font-weight:400; font-size:12px; margin-left:6px; }}
    .disk-list {{ list-style:none; padding:0; margin:0; }}
    .disk {{ padding:4px 0; font-size:13px; }}
    .disk.ok {{ color:#86efac; }}
    .disk.bad {{ color:#fca5a5; }}
    .error {{ color:#fca5a5; font-size:13px; }}
  </style>
</head>
<body>
  <h1>NVR HDD Status • {site_name}</h1>
  <div class="grid">
    {''.join(cards) if cards else '<div>No data</div>'}
  </div>
</body>
</html>"""
    return html


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_config(sys.argv[1])
    params = config.get("parameters", {})
    db_path = config.get("database_path")
    site_name = config.get("site_name")
    log_file = config.get("log_file")

    username = (params.get("username") or "").strip()
    password = params.get("password") or ""
    timeout = int(params.get("timeout_seconds", 10) or 10)
    verify_ssl = str(params.get("verify_ssl", "false")).strip().lower() in ("1", "true", "yes")
    trace_output = str(params.get("trace_output", "false")).strip().lower() in ("1", "true", "yes")
    targets = params.get("targets") or {}

    if not db_path or not site_name:
        print(json.dumps({"status": "error", "message": "Missing database_path or site_name"}))
        return
    if not username or password is None:
        print(json.dumps({"status": "error", "message": "Missing username/password"}))
        return

    try:
        data = load_db(db_path)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"Failed to read database: {exc}"}))
        return

    devices = _normalize_targets(data, site_name, targets)
    if not devices:
        print(json.dumps({"status": "error", "message": "No target devices selected"}))
        return

    results = []
    failures = 0
    for dev in devices:
        ip = dev.get("ip") or ""
        name = dev.get("name") or dev.get("id") or ip
        if not ip:
            failures += 1
            results.append({"ip": ip, "name": name, "ok": False, "error": "missing_ip"})
            continue
        ok, info = _fetch_hdd_status(ip, username, password, timeout, verify_ssl, trace_output, log_file)
        if not ok:
            failures += 1
        entry = {
            "ip": ip,
            "name": name,
            "ok": ok
        }
        entry.update(info)
        results.append(entry)

    summary = {
        "status": "success" if failures == 0 else "error",
        "site": site_name,
        "total": len(results),
        "failed": failures,
        "devices": results,
        "ran_at": datetime.now().isoformat()
    }
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(base_dir, "..", "..", "generated_maps")
        os.makedirs(out_dir, exist_ok=True)
        safe_site = "".join(ch for ch in site_name if ch.isalnum() or ch in ("-", "_")).strip() or "site"
        filename = f"{safe_site}_nvr_hard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        out_path = os.path.join(out_dir, filename)
        html = _render_html(site_name, results)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        summary["report"] = {
            "file": filename,
            "path": os.path.abspath(out_path),
            "url": f"/generated_maps/{filename}"
        }
    except Exception as exc:
        summary["report_error"] = str(exc)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
