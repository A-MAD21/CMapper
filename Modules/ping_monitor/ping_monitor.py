#!/usr/bin/env python3
"""
Ping Monitor Module

Pings each device in the selected site for ~5 seconds and updates monitoring.db.
"""

from __future__ import annotations

import json
import portalocker
import os
import subprocess
import sys
from datetime import datetime
from typing import Dict, Any, Tuple


def _load_json(path: str) -> Dict[str, Any]:
    with portalocker.Lock(path, "r", timeout=5, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Dict[str, Any]) -> None:
    data.setdefault("meta", {})
    data["meta"]["last_modified"] = datetime.now().isoformat()
    with portalocker.Lock(path, "w", timeout=5, encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _init_monitoring(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        data = {
            "version": "1.0",
            "meta": {
                "created": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat()
            },
            "sites": {}
        }
        _write_json(path, data)
    return _load_json(path)


def _parse_ping_output(output: str) -> Tuple[int | None, int | None]:
    loss = None
    avg = None
    lines = output.splitlines()
    for line in lines:
        if "Packets:" in line and "%" in line:
            # Windows: Lost = 0 (0% loss)
            if "% loss" in line:
                try:
                    loss_part = line.split("% loss")[0]
                    loss = int(loss_part.split("(")[-1].strip())
                except ValueError:
                    pass
        if "packet loss" in line:
            # Linux: 0% packet loss
            try:
                loss = int(line.split("%")[0].split()[-1])
            except ValueError:
                pass
        if "Average =" in line:
            # Windows: Average = 12ms
            try:
                avg_text = line.split("Average =")[-1].strip().replace("ms", "")
                avg = int(avg_text)
            except ValueError:
                pass
        if "min/avg/max" in line:
            # Linux: rtt min/avg/max/mdev = 10.123/20.456/30.789/...
            try:
                avg_text = line.split("=")[-1].strip().split("/")[1]
                avg = int(float(avg_text))
            except (ValueError, IndexError):
                pass
    return loss, avg


def _ping_host(ip: str) -> Tuple[int | None, int | None]:
    if os.name == "nt":
        cmd = ["ping", "-n", "5", "-w", "1000", ip]
    else:
        cmd = ["ping", "-c", "5", "-W", "1", ip]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return 100, None
    output = result.stdout + "\n" + result.stderr
    loss, avg = _parse_ping_output(output)
    if loss is None:
        loss = 100 if result.returncode != 0 else 0
    return loss, avg


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Config file required"}))
        return

    config_path = sys.argv[1]
    config = _load_json(config_path)

    db_path = config.get("database_path")
    monitoring_path = config.get("monitoring_db_path")
    site_name = config.get("site_name")

    if not db_path or not monitoring_path or not site_name:
        print(json.dumps({"status": "error", "message": "Missing database_path, monitoring_db_path, or site_name"}))
        return

    database = _load_json(db_path)
    devices = [d for d in database.get("devices", []) if d.get("site") == site_name]
    monitoring = _init_monitoring(monitoring_path)
    site_entry = monitoring.setdefault("sites", {}).setdefault(site_name, {})
    site_entry.setdefault("devices", {})
    now = datetime.now().isoformat()

    results = []
    for device in devices:
        device_id = device.get("id")
        ip = device.get("ip")
        if not device_id:
            continue
        if not ip:
            status = {
                "ip": ip,
                "packet_loss": 100,
                "avg_latency_ms": None,
                "last_check": now
            }
        else:
            loss, avg = _ping_host(ip)
            status = {
                "ip": ip,
                "packet_loss": loss,
                "avg_latency_ms": avg,
                "last_check": now
            }
        site_entry["devices"][device_id] = status
        results.append({
            "id": device_id,
            "ip": ip,
            "packet_loss": status["packet_loss"],
            "avg_latency_ms": status["avg_latency_ms"]
        })

    _write_json(monitoring_path, monitoring)

    print(json.dumps({
        "status": "success",
        "site": site_name,
        "device_count": len(devices),
        "results": results
    }))


if __name__ == "__main__":
    main()
