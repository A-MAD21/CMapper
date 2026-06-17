#!/usr/bin/env python3
"""
Uniview Device Type Check

Uniview cameras and NVRs can share OUI ranges. This module checks every NVR
row in the selected site against the Uniview web UI device type endpoints.
If the reported device type starts with IPC, the row is reclassified as camera
and locked so generic module/OUI updates do not flip it back.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.abspath(os.path.join(MODULE_DIR, "..", "_shared"))
if SHARED_DIR not in sys.path:
    sys.path.insert(0, SHARED_DIR)

from sqlite_store import read_json_store, write_json_store


MODULE_ID = "uniview_device_type_check"


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _append_log(path: Optional[str], message: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _append_once(values: Any, value: str) -> List[str]:
    result = list(values or [])
    if value not in result:
        result.append(value)
    return result


def _target_devices(data: Dict[str, Any], site_name: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    site_devices = [
        d for d in data.get("devices", [])
        if isinstance(d, dict) and d.get("site") == site_name and (d.get("ip") or "").strip()
    ]
    targets = params.get("targets") or {}
    device_ids = set(targets.get("device_ids") or [])
    manual_devices = targets.get("manual_devices") or []

    if device_ids:
        devices = [d for d in site_devices if d.get("id") in device_ids]
    else:
        devices = [d for d in site_devices if (d.get("type") or "").strip().lower() == "nvr"]

    for entry in manual_devices:
        ip = (entry.get("ip") or "").strip()
        if not ip:
            continue
        devices.append({
            "id": f"manual_{ip}",
            "site": site_name,
            "name": entry.get("name") or ip,
            "ip": ip,
            "type": "nvr",
            "_manual": True
        })

    seen = set()
    unique = []
    for device in devices:
        key = device.get("id") or device.get("ip")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(device)
    return unique


def _parse_device_type(text: str) -> Optional[str]:
    if not text:
        return None

    candidates: List[str] = []
    assignment_patterns = [
        r"(?i)\b(?:device[_-]?type|devicetype|szdevicetype|g_devicetype|device_model|devicemodel|model)\b\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r"(?i)<(?:device[_-]?type|devicetype|DeviceType|model|Model|DeviceModel)[^>]*>\s*([^<]+?)\s*</",
    ]
    for pattern in assignment_patterns:
        for match in re.finditer(pattern, text):
            value = (match.group(1) or "").strip()
            if value:
                candidates.append(value)

    for match in re.finditer(r"['\"]([^'\"]*IPC[^'\"]*)['\"]", text, flags=re.IGNORECASE):
        value = (match.group(1) or "").strip()
        if value:
            candidates.append(value)

    for match in re.finditer(r"\b(IPC[A-Za-z0-9_.-]+)\b", text):
        value = (match.group(1) or "").strip()
        if value:
            candidates.append(value)

    if not candidates:
        return None

    for value in candidates:
        if value.upper().startswith("IPC"):
            return value
    return candidates[0]


def _extract_http_error(resp: Any) -> str:
    try:
        body = (resp.text or "").strip()
    except Exception:
        body = ""
    if body:
        return f"http_{resp.status_code}: {body[:160]}"
    return f"http_{resp.status_code}"


def _fetch_uniview_type(
    ip: str,
    timeout: int,
    trace: bool,
    log_file: Optional[str]
) -> Tuple[bool, Optional[str], str, str]:
    try:
        import requests
    except Exception as exc:
        return False, None, "requests_missing", str(exc)

    base = f"http://{ip}"
    session = requests.Session()
    headers = {
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "CMapper-UniviewTypeCheck/1.0",
        "Referer": f"{base}/"
    }

    ts = int(time.time() * 1000)
    urls = [
        f"{base}/script/common/device_type.js?t={ts}",
        f"{base}/script/common/device_type.js",
        f"{base}/device_cap.xml?t={ts}",
        f"{base}/device_cap.xml",
    ]

    last_error = ""
    for url in urls:
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            if trace:
                _append_log(log_file, f"GET {ip} {resp.status_code} {url}")
                if resp.text:
                    _append_log(log_file, f"BODY {ip} {resp.text[:500]}")
            if resp.status_code != 200:
                last_error = _extract_http_error(resp)
                continue
            device_type = _parse_device_type(resp.text or "")
            if device_type:
                return True, device_type, url, ""
            last_error = "no_device_type_in_response"
        except Exception as exc:
            last_error = str(exc)

    return False, None, "", last_error or "not_found"


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config = load_config(sys.argv[1])
    params = config.get("parameters", {}) if isinstance(config, dict) else {}
    db_path = config.get("database_path")
    site_name = (config.get("site_name") or "").strip()
    log_file = config.get("log_file")

    timeout = int(params.get("timeout_seconds", 10) or 10)
    concurrency = max(1, min(32, int(params.get("concurrency", 6) or 6)))
    lock_camera_nodes = _as_bool(params.get("lock_camera_nodes"), True)
    override_locked = _as_bool(params.get("override_locked"), False)
    trace = str(params.get("trace_output", "false")).strip().lower() == "true"

    if not db_path:
        print(json.dumps({"status": "error", "message": "Missing database_path"}))
        return
    if not site_name:
        print(json.dumps({"status": "error", "message": "Missing site_name"}))
        return
    data = read_json_store(db_path, "devices")
    if data is None:
        print(json.dumps({"status": "error", "message": "Failed to read database"}))
        return

    devices = _target_devices(data, site_name, params)
    if not devices:
        print(json.dumps({"status": "success", "site": site_name, "checked": 0, "updated": 0, "cameras": [], "failures": []}))
        return

    started_at = datetime.now().isoformat()
    if log_file:
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"UNIVIEW DEVICE TYPE CHECK {started_at}\n")
                f.write(f"Site: {site_name}\n")
                f.write(f"Targets: {len(devices)}\n")
        except Exception:
            pass

    print(f"Uniview device type check for {len(devices)} NVR device(s) in {site_name}")

    skipped = []
    work_items = []
    for device in devices:
        if device.get("locked") and not override_locked:
            skipped.append({
                "id": device.get("id"),
                "name": device.get("name") or device.get("ip"),
                "ip": device.get("ip"),
                "reason": "locked"
            })
            print(f"SKIP locked: {device.get('name') or device.get('ip')} ({device.get('ip')})")
            continue
        work_items.append(device)

    results = []
    with ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(work_items)))) as pool:
        future_map = {
            pool.submit(
                _fetch_uniview_type,
                (device.get("ip") or "").strip(),
                timeout,
                trace,
                log_file,
            ): device
            for device in work_items
        }
        for future in as_completed(future_map):
            device = future_map[future]
            ip = (device.get("ip") or "").strip()
            name = device.get("name") or ip
            try:
                ok, device_type, source, error = future.result()
            except Exception as exc:
                ok, device_type, source, error = False, None, "", str(exc)
            results.append({
                "device": device,
                "ip": ip,
                "name": name,
                "ok": ok,
                "device_type": device_type,
                "source": source,
                "error": error
            })

    by_id = {d.get("id"): d for d in data.get("devices", []) if isinstance(d, dict) and d.get("id")}
    by_site_ip = {
        (d.get("site"), d.get("ip")): d
        for d in data.get("devices", [])
        if isinstance(d, dict) and d.get("site") and d.get("ip")
    }

    now = datetime.now().isoformat()
    cameras = []
    non_ipc = []
    failures = []
    updated = 0

    for result in sorted(results, key=lambda item: (item.get("ip") or "")):
        device = result["device"]
        ip = result["ip"]
        name = result["name"]
        if not result["ok"]:
            failures.append({"id": device.get("id"), "name": name, "ip": ip, "reason": result["error"] or "not_found"})
            print(f"FAIL: {name} ({ip}) {result['error'] or 'not_found'}")
            continue

        device_type = (result["device_type"] or "").strip()
        is_ipc = device_type.upper().startswith("IPC")
        target = None
        if device.get("id"):
            target = by_id.get(device.get("id"))
        if target is None:
            target = by_site_ip.get((site_name, ip))

        if not target:
            failures.append({"id": device.get("id"), "name": name, "ip": ip, "reason": "manual_or_missing_db_row", "device_type": device_type})
            print(f"CHECK ONLY: {name} ({ip}) device_type={device_type}")
            continue

        changed = False
        target["uniview_device_type"] = device_type
        target["modules_successful"] = _append_once(target.get("modules_successful"), MODULE_ID)
        if MODULE_ID in (target.get("modules_failed") or []):
            target["modules_failed"] = [m for m in target.get("modules_failed") or [] if m != MODULE_ID]
        changed = True

        if is_ipc:
            if (target.get("type") or "").lower() != "camera":
                target["type"] = "camera"
                changed = True
            if lock_camera_nodes and not target.get("locked"):
                target["locked"] = True
                changed = True
            if not target.get("discovered_by"):
                target["discovered_by"] = MODULE_ID
            cameras.append({
                "id": target.get("id"),
                "name": target.get("name") or name,
                "ip": ip,
                "device_type": device_type,
                "locked": bool(target.get("locked"))
            })
            print(f"CAMERA: {target.get('name') or name} ({ip}) device_type={device_type} locked={bool(target.get('locked'))}")
            updated += 1
        else:
            non_ipc.append({
                "id": target.get("id"),
                "name": target.get("name") or name,
                "ip": ip,
                "device_type": device_type
            })
            print(f"NVR/OTHER: {target.get('name') or name} ({ip}) device_type={device_type}")

        if changed:
            target["last_modified"] = now

    for failure in failures:
        target = None
        if failure.get("id"):
            target = by_id.get(failure.get("id"))
        if target and not target.get("locked"):
            target["modules_failed"] = _append_once(target.get("modules_failed"), MODULE_ID)
            target["last_modified"] = now

    data.setdefault("meta", {})["last_modified"] = now
    try:
        write_json_store(db_path, "devices", data)
    except Exception:
        print(json.dumps({"status": "error", "message": "Failed to write database"}))
        return

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "checked": len(work_items),
        "updated": updated,
        "cameras": cameras,
        "non_ipc": non_ipc,
        "skipped": skipped,
        "failures": failures
    }))


if __name__ == "__main__":
    main()
