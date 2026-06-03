#!/usr/bin/env python3
"""
Network Discovery Platform - COMPLETE WORKING BACKEND
"""

from flask import Flask, render_template, jsonify, request, send_file, session, g
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import json
import csv
import os
import glob
import threading
import time
import subprocess
import sys
import uuid
import zipfile
import io
from datetime import datetime, timedelta
import sqlite3
import copy
import portalocker 
from concurrent.futures import ThreadPoolExecutor, as_completed


# ===============================
# Global paths (defined early)
# ===============================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_FILE = os.path.join(BASE_DIR, "devices.db")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
SQLITE_DB_FILE = os.path.join(BASE_DIR, "cmapp.sqlite3")
CORRUPT_DIR = os.path.join(BASE_DIR, "corrupt_backups")
CORRUPT_RETENTION_DAYS = 7
MODULE_LOG_RETENTION_DAYS = 0
MODULE_CONFIG_RETENTION_DAYS = 0
AGENT_SCAN_RETENTION_DAYS = 180

MODULES_DIR = os.path.join(BASE_DIR, "Modules")
TEMPLATES_DIR = os.path.join(BASE_DIR, "Templates")
STATIC_DIR = os.path.join(BASE_DIR, "Static")
OUI_RANGES_FILE = os.path.join(MODULES_DIR, "mikrotik_mac_discovery", "oui_ranges.txt")
OUI_DEVICE_TYPES_FILE = os.path.join(MODULES_DIR, "enforce_oui_table", "oui_device_types.txt")

GENERATED_MAPS_DIR = os.path.join(BASE_DIR, "generated_maps")
AGENT_CONFIG_DIR = os.path.join(BASE_DIR, "share", "agent_configs")
AGENT_SCAN_DIR = os.path.join(BASE_DIR, "share", "agent_scans")

# Ensure required dirs exist
os.makedirs(GENERATED_MAPS_DIR, exist_ok=True)
os.makedirs(AGENT_CONFIG_DIR, exist_ok=True)
os.makedirs(AGENT_SCAN_DIR, exist_ok=True)



# ==================== PATHS ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _pick_existing_dir(*candidates: str) -> str:
    """Return first existing directory path relative to BASE_DIR."""
    for name in candidates:
        path = os.path.join(BASE_DIR, name)
        if os.path.isdir(path):
            return path
    # Fall back to first candidate (Flask will error with a clear message)
    return os.path.join(BASE_DIR, candidates[0]) if candidates else BASE_DIR

TEMPLATES_DIR = _pick_existing_dir("Templates", "templates")
STATIC_DIR = _pick_existing_dir("Static", "static")

GENERATED_MAPS_DIR = os.path.join(BASE_DIR, "generated_maps")

app = Flask(
    __name__,
    template_folder=TEMPLATES_DIR,
    static_folder=STATIC_DIR,
    static_url_path="/static",
)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("USE_SSL", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)



# ==================== CONFIGURATION ====================


# ==================== FILE OPERATIONS ====================

# Ensure output directories exist
os.makedirs(GENERATED_MAPS_DIR, exist_ok=True)

def _get_sqlite_conn():
    conn = sqlite3.connect(SQLITE_DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS json_store ("
        "name TEXT PRIMARY KEY,"
        "json TEXT NOT NULL,"
        "updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_users ("
        "username TEXT PRIMARY KEY,"
        "password_hash TEXT NOT NULL,"
        "role TEXT NOT NULL,"
        "allowed_sites_json TEXT NOT NULL DEFAULT '[]',"
        "disabled INTEGER NOT NULL DEFAULT 0,"
        "created_at TEXT NOT NULL,"
        "updated_at TEXT NOT NULL,"
        "last_login_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_sessions ("
        "session_id TEXT PRIMARY KEY,"
        "username TEXT NOT NULL,"
        "created_at TEXT NOT NULL,"
        "expires_at TEXT NOT NULL,"
        "revoked_at TEXT,"
        "ip_address TEXT,"
        "user_agent TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auth_login_attempts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT,"
        "success INTEGER NOT NULL,"
        "ip_address TEXT,"
        "user_agent TEXT,"
        "created_at TEXT NOT NULL,"
        "reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "actor TEXT,"
        "action TEXT NOT NULL,"
        "target TEXT,"
        "details_json TEXT NOT NULL DEFAULT '{}',"
        "ip_address TEXT,"
        "created_at TEXT NOT NULL)"
    )
    return conn

def _read_sqlite_json(name: str):
    try:
        conn = _get_sqlite_conn()
        cur = conn.execute("SELECT json FROM json_store WHERE name = ?", (name,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return json.loads(row[0])
    except Exception:
        return None

def _write_sqlite_json(name: str, data):
    payload = json.dumps(data)
    now = datetime.now().isoformat()
    conn = _get_sqlite_conn()
    conn.execute(
        "INSERT INTO json_store (name, json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
        (name, payload, now)
    )
    conn.commit()
    conn.close()

def _read_json_file(path: str, default=None):
    try:
        if os.path.exists(path):
            with portalocker.Lock(path, 'r', timeout=5, encoding='utf-8') as f:
                return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, portalocker.exceptions.LockException, PermissionError):
        return default
    return default

def _write_json_file(path: str, data):
    try:
        with portalocker.Lock(path, 'w', timeout=5, encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def normalize_mac(value: str) -> str:
    mac = (value or "").strip().replace("-", ":").replace(".", "")
    if len(mac) == 12:
        mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
    return mac.upper()

def _read_legacy_database_with_salvage():
    def _cleanup_corrupt_backups():
        cutoff = time.time() - (CORRUPT_RETENTION_DAYS * 86400)
        try:
            if not os.path.isdir(CORRUPT_DIR):
                return
            for name in os.listdir(CORRUPT_DIR):
                path = os.path.join(CORRUPT_DIR, name)
                if not os.path.isfile(path):
                    continue
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
        except OSError:
            pass

    try:
        if os.path.exists(DATABASE_FILE):
            with portalocker.Lock(DATABASE_FILE, 'r', timeout=5, encoding='utf-8') as f:
                return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        # Attempt salvage: keep the first JSON object if extra data was appended.
        try:
            with portalocker.Lock(DATABASE_FILE, 'r', timeout=5, encoding='utf-8') as f:
                raw = f.read()
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(raw)
            if isinstance(data, dict):
                os.makedirs(CORRUPT_DIR, exist_ok=True)
                backup = os.path.join(
                    CORRUPT_DIR,
                    f"devices.db.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                )
                try:
                    os.replace(DATABASE_FILE, backup)
                except OSError:
                    pass
                _cleanup_corrupt_backups()
                _write_json_file(DATABASE_FILE, data)
                return data
        except Exception:
            return None
    except portalocker.exceptions.LockException:
        return None
    return None

def _sync_sqlite_from_legacy_files():
    legacy_db = _read_legacy_database_with_salvage()
    if legacy_db is not None:
        _write_sqlite_json("devices", legacy_db)
    legacy_settings = _read_json_file(SETTINGS_FILE, default=None)
    if legacy_settings is not None:
        _write_sqlite_json("settings", legacy_settings)

def init_database():
    """Initialize empty database if it doesn't exist"""
    existing = _read_sqlite_json("devices")
    if existing is not None:
        return
    legacy = _read_legacy_database_with_salvage()
    if legacy is not None:
        _write_sqlite_json("devices", legacy)
        return
    data = {
        "version": "1.0",
        "meta": {
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        },
        "sites": [],
        "devices": [],
        "discovery_sessions": []
    }
    _write_sqlite_json("devices", data)

DEFAULT_SETTINGS = {
    "default_site": "",
    "backup_path": "./backups",
    "default_scan_depth": 3,
    "auto_refresh": False,
    "refresh_interval": 30,
    "module_max_concurrent": 2,
    "module_credentials": {},
    "module_last_params": {},
    "module_schedules": [],
    "agent_server_url": "http://127.0.0.1:5000",
    "agents": [],
    "stale_scan_days": 7,
    "agent_online_minutes": 5,
    "auth_session_hours": 12,
    "auth": {
        "enabled": False,
        "users": []
    },
}

def init_settings():
    """Initialize default settings"""
    existing = _read_sqlite_json("settings")
    if existing is not None:
        return
    legacy = _read_json_file(SETTINGS_FILE, default=None)
    if legacy is not None:
        _write_sqlite_json("settings", legacy)
        return
    settings = DEFAULT_SETTINGS.copy()
    _write_sqlite_json("settings", settings)

def _log_perf(label, start_time):
    duration = time.perf_counter() - start_time
    print(f"[PERF] {label} took {duration:.2f}s")

def read_database():
    """Read database (SQLite-backed with JSON fallback)."""
    data = _read_sqlite_json("devices")
    if data is not None:
        return data
    legacy = _read_legacy_database_with_salvage()
    if legacy is not None:
        _write_sqlite_json("devices", legacy)
        return legacy
    init_database()
    return _read_sqlite_json("devices") or {
        "version": "1.0",
        "meta": {"created": datetime.now().isoformat(), "last_modified": datetime.now().isoformat()},
        "sites": [],
        "devices": [],
        "discovery_sessions": []
    }


def write_database(data):
    """Write database"""
    try:
        _write_sqlite_json("devices", data)
        return True
    except Exception as e:
        print(f"Error writing database: {e}", file=sys.stderr)
        return False

def read_settings():
    """Read settings (SQLite-backed with JSON fallback)."""
    loaded = _read_sqlite_json("settings")
    if loaded is None:
        legacy = _read_json_file(SETTINGS_FILE, default=None)
        if legacy is not None:
            loaded = legacy
            _write_sqlite_json("settings", legacy)
        else:
            init_settings()
            loaded = _read_sqlite_json("settings") or {}

    merged = DEFAULT_SETTINGS.copy()
    if isinstance(loaded, dict):
        merged.update(loaded)

    if merged != loaded:
        _write_sqlite_json("settings", merged)

    return merged


def write_settings(settings):
    """Write settings file"""
    _write_sqlite_json("settings", settings)
    try:
        _write_json_file(SETTINGS_FILE, settings)
    except Exception:
        pass


def _generate_agent_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _normalize_agent_payload(payload, existing=None):
    if not isinstance(payload, dict):
        return None
    name = (payload.get("name") or "").strip()
    site = (payload.get("site") or "").strip()
    target_range = (payload.get("target_range") or "").strip()
    target_ranges = payload.get("target_ranges") if isinstance(payload.get("target_ranges"), list) else (existing.get("target_ranges") if existing else [])
    target_ranges = [r.strip() for r in target_ranges if isinstance(r, str) and r.strip()]
    if not name or not site or (not target_range and not target_ranges):
        return None
    enabled = bool(payload.get("enabled", True))
    interval_min = int(payload.get("interval_min") or 0)
    if interval_min < 0:
        interval_min = 0
    allow_interval = bool(payload.get("allow_interval", True))
    allow_on_demand = bool(payload.get("allow_on_demand", True))
    server_host = (payload.get("server_host") or "").strip()
    agent_id = (payload.get("id") or "").strip() or (existing.get("id") if existing else "")
    token = (payload.get("token") or "").strip() or (existing.get("token") if existing else "")
    if not token:
        token = _generate_agent_token()
    if not agent_id:
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"
    trust_mode = (payload.get("trust_mode") or (existing.get("trust_mode") if existing else "augment")).strip().lower()
    if trust_mode not in ("augment", "replace"):
        trust_mode = "augment"
    ip_scan_min = int(payload.get("ip_scan_min") or (existing.get("ip_scan_min") if existing else 10) or 10)
    ping_min = int(payload.get("ping_min") or (existing.get("ping_min") if existing else 2) or 2)
    modules_cfg = payload.get("modules") if isinstance(payload.get("modules"), dict) else (existing.get("modules") if existing else {})
    credentials_cfg = payload.get("credentials") if isinstance(payload.get("credentials"), dict) else (existing.get("credentials") if existing else {})

    return {
        "id": agent_id,
        "name": name,
        "site": site,
        "target_range": target_range,
        "enabled": enabled,
        "interval_min": interval_min,
        "allow_interval": allow_interval,
        "allow_on_demand": allow_on_demand,
        "target_ranges": target_ranges,
        "server_host": server_host,
        "token": token,
        "trust_mode": trust_mode,
        "ip_scan_min": ip_scan_min,
        "ping_min": ping_min,
        "modules": modules_cfg,
        "credentials": credentials_cfg,
        "device_name": (payload.get("device_name") or (existing.get("device_name") if existing else "")).strip(),
        "device_ip": (payload.get("device_ip") or (existing.get("device_ip") if existing else "")).strip(),
        "device_mac": normalize_mac(payload.get("device_mac") or (existing.get("device_mac") if existing else "")),
        "last_seen": existing.get("last_seen") if existing else None,
        "last_scan_at": existing.get("last_scan_at") if existing else None,
        "last_result": existing.get("last_result") if existing else None,
        "last_result_at": existing.get("last_result_at") if existing else None,
        "last_state": existing.get("last_state") if existing else None,
        "run_now": bool(existing.get("run_now")) if existing else False
    }


def _write_agent_config_files(agent, settings):
    if not agent:
        return
    base_url = (settings or {}).get("agent_server_url") or "http://127.0.0.1:5000"
    host_override = (agent.get("server_host") or "").strip()
    if host_override:
        base_url = host_override
    config = {
        "agent_id": agent.get("id"),
        "name": agent.get("name"),
        "site": agent.get("site"),
        "target_range": agent.get("target_range"),
        "target_ranges": agent.get("target_ranges") or [],
        "enabled": agent.get("enabled", True),
        "interval_min": agent.get("interval_min", 0),
        "allow_interval": agent.get("allow_interval", True),
        "allow_on_demand": agent.get("allow_on_demand", True),
        "server_url": base_url,
        "token": agent.get("token"),
        "retention_days": AGENT_SCAN_RETENTION_DAYS
    }
    os.makedirs(AGENT_CONFIG_DIR, exist_ok=True)
    json_path = os.path.join(AGENT_CONFIG_DIR, f"{agent.get('id')}.json")
    txt_path = os.path.join(AGENT_CONFIG_DIR, f"{agent.get('id')}.txt")
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            for key, value in config.items():
                f.write(f"{key}={value}\n")
    except Exception:
        pass


def _cleanup_agent_scans(agent_id=None):
    cutoff = time.time() - (AGENT_SCAN_RETENTION_DAYS * 86400)
    base_dir = os.path.join(AGENT_SCAN_DIR, agent_id) if agent_id else AGENT_SCAN_DIR
    if not os.path.isdir(base_dir):
        return
    for root, _, files in os.walk(base_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


def _write_agent_scan_files(agent, scan_time, devices):
    if not agent:
        return None
    agent_id = agent.get("id") or "agent"
    site = agent.get("site") or ""
    safe_site = site.replace("/", "_").replace("\\", "_").replace(":", "_")
    ts = scan_time.replace(":", "").replace("-", "").replace("T", "_").split(".")[0]
    out_dir = os.path.join(AGENT_SCAN_DIR, agent_id)
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"{safe_site}_{ts}.json")
    csv_path = os.path.join(out_dir, f"{safe_site}_{ts}.csv")

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "agent_id": agent_id,
                "site": site,
                "scan_time": scan_time,
                "devices": devices
            }, f, indent=2)
    except Exception:
        pass

    header = [
        "Status", "Name", "IP", "Radmin", "Http", "Https", "Ftp", "Rdp",
        "Shared folders", "Shared printers", "NetBIOS group", "Manufacturer",
        "MAC address", "User", "Date", "Comments"
    ]
    try:
        with open(csv_path, "w", encoding="utf-16", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(header)
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
    except Exception:
        pass

    _cleanup_agent_scans(agent_id)
    return {"json": json_path, "csv": csv_path}

def read_oui_ranges():
    if not os.path.exists(OUI_RANGES_FILE):
        return ""
    with open(OUI_RANGES_FILE, "r", encoding="utf-8") as f:
        return f.read()

def write_oui_ranges(content: str):
    os.makedirs(os.path.dirname(OUI_RANGES_FILE), exist_ok=True)
    with open(OUI_RANGES_FILE, "w", encoding="utf-8") as f:
        f.write(content or "")


def read_oui_device_types():
    if not os.path.exists(OUI_DEVICE_TYPES_FILE):
        return ""
    with open(OUI_DEVICE_TYPES_FILE, "r", encoding="utf-8") as f:
        return f.read()


def write_oui_device_types(content: str):
    os.makedirs(os.path.dirname(OUI_DEVICE_TYPES_FILE), exist_ok=True)
    with open(OUI_DEVICE_TYPES_FILE, "w", encoding="utf-8") as f:
        f.write(content or "")


def _mask_sensitive(obj):
    if isinstance(obj, dict):
        masked = {}
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in ("password", "pass", "secret", "token", "api_key"):
                masked[key] = "********"
            else:
                masked[key] = _mask_sensitive(value)
        return masked
    if isinstance(obj, list):
        return [_mask_sensitive(item) for item in obj]
    return obj

def _normalize_schedule_payload(payload):
    if not isinstance(payload, dict):
        return None
    name = (payload.get("name") or "").strip()
    if not name:
        return None
    enabled = bool(payload.get("enabled", True))

    scope = payload.get("site_scope") if isinstance(payload.get("site_scope"), dict) else {}
    mode = (scope.get("mode") or "selected").lower()
    if mode not in ("all", "selected"):
        mode = "selected"
    sites = scope.get("sites") or []
    if not isinstance(sites, list):
        sites = []
    sites = [s for s in sites if isinstance(s, str) and s.strip()]

    run_mode = (payload.get("site_run_mode") or "sequential").lower()
    if run_mode not in ("sequential", "concurrent"):
        run_mode = "sequential"

    delay_between = int(payload.get("delay_between_modules_sec") or 0)
    repeat_minutes = int(payload.get("repeat_interval_min") or 0)

    modules = []
    for entry in payload.get("modules") or []:
        if not isinstance(entry, dict):
            continue
        module_id = entry.get("module_id")
        if not module_id:
            continue
        params = entry.get("parameters") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params) if params.strip() else {}
            except json.JSONDecodeError:
                params = {}
        if not isinstance(params, dict):
            params = {}
        mod_entry = {"module_id": module_id, "parameters": params}

        # Normalize credential profile (prefer explicit field, but accept param value)
        cred = entry.get("credential_profile") or params.pop("credential_profile", None)
        if isinstance(cred, str) and cred.strip():
            mod_entry["credential_profile"] = cred.strip()

        # For all-sites schedules, avoid persisting stale device selections
        if mode == "all":
            targets = params.get("targets")
            if isinstance(targets, dict):
                targets["auto"] = True
                targets["auto_on_empty"] = True
                if "device_ids" in targets:
                    targets["device_ids"] = "__AUTO__"
                if "manual_devices" in targets:
                    targets["manual_devices"] = []
            elif targets is None and module_id in ("ubiquiti_cdp_reader", "uniview_nvr_capture"):
                params["targets"] = {
                    "auto": True,
                    "auto_on_empty": True,
                    "device_ids": "__AUTO__",
                    "manual_devices": []
                }
        modules.append(mod_entry)

    return {
        "name": name,
        "enabled": enabled,
        "site_scope": {"mode": mode, "sites": sites},
        "site_run_mode": run_mode,
        "delay_between_modules_sec": delay_between,
        "repeat_interval_min": repeat_minutes,
        "modules": modules
    }

def _serialize_schedule(schedule, state=None):
    data = copy.deepcopy(schedule)
    if state:
        data["status"] = state.get("status", "idle")
        data["progress"] = {
            "completed_sites": int(state.get("completed_sites") or 0),
            "total_sites": int(state.get("total_sites") or 0),
            "active_sites": int(state.get("active_sites") or 0)
        }
        next_run = state.get("next_run_at")
        last_run = state.get("last_run_at")
        if isinstance(next_run, datetime):
            data["next_run_at"] = next_run.isoformat()
        elif next_run:
            data["next_run_at"] = next_run
        if isinstance(last_run, datetime):
            data["last_run_at"] = last_run.isoformat()
        elif last_run:
            data["last_run_at"] = last_run
        data["last_result"] = _mask_sensitive(state.get("last_result")) if state.get("last_result") else None
    data["modules"] = _mask_sensitive(data.get("modules") or [])
    return data

def _validate_schedule_config(schedule):
    errors = []
    if not isinstance(schedule, dict):
        return ["invalid_schedule"]
    modules_by_id = {m.get("id"): m for m in discover_modules() if isinstance(m, dict)}
    settings = read_settings() or {}
    module_creds = settings.get("module_credentials", {}) if isinstance(settings, dict) else {}
    database = read_database() or {}

    for entry in schedule.get("modules") or []:
        if not isinstance(entry, dict):
            continue
        module_id = entry.get("module_id")
        if not module_id:
            continue
        module = modules_by_id.get(module_id)
        if not module:
            errors.append(f"{module_id}: module not found")
            continue
        params = copy.deepcopy(entry.get("parameters") or {})
        cred_profile = entry.get("credential_profile")
        if cred_profile:
            profiles = module_creds.get(module_id, []) if isinstance(module_creds, dict) else []
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("name") == cred_profile:
                    params.setdefault("username", profile.get("username", ""))
                    params.setdefault("password", profile.get("password", ""))
                    break

        if module_id == "mikrotik_mac_discovery":
            has_router = bool(params.get("router_ip") or params.get("router_device_id"))
            if not has_router:
                site_name = (schedule.get("site_scope") or {}).get("sites") or []
                site_name = site_name[0] if site_name else None
                for dev in database.get("devices", []):
                    if site_name and dev.get("site") != site_name:
                        continue
                    if (dev.get("type") or "").lower() != "server":
                        continue
                    name = (dev.get("name") or "").lower()
                    vendor = (dev.get("vendor") or "").lower()
                    if "mikrotik" in name or "mikrotik" in vendor:
                        if dev.get("ip"):
                            has_router = True
                            break
            if not has_router:
                errors.append("mikrotik_mac_discovery: missing router_ip or router_device_id")

        for field in module.get("inputs", []) or []:
            if not isinstance(field, dict):
                continue
            if not field.get("required"):
                continue
            name = field.get("name")
            if not name:
                continue
            if module_id in ("ubiquiti_cdp_reader", "uniview_nvr_capture") and name == "targets":
                continue
            if module_id == "uniview_nvr_capture" and name == "nic" and params.get("nic") in (None, ""):
                params["nic"] = field.get("default") or "NIC1"
            if module_id == "uniview_nvr_capture" and name == "ip_mode" and params.get("ip_mode") in (None, ""):
                params["ip_mode"] = field.get("default") or "filter"
            value = params.get(name)
            if value is None:
                if "default" in field:
                    params[name] = field.get("default")
                    continue
                errors.append(f"{module_id}: missing {name}")
                continue
            if isinstance(value, str) and not value.strip():
                errors.append(f"{module_id}: missing {name}")

    return errors

def _json_list(value):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            if isinstance(loaded, list):
                return [item for item in loaded if isinstance(item, str)]
        except Exception:
            return []
    return []


def _auth_user_from_row(row):
    if not row:
        return None
    return {
        "username": row[0],
        "password_hash": row[1],
        "role": row[2] or "guest",
        "allowed_sites": _json_list(row[3]),
        "disabled": bool(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
        "last_login_at": row[7],
    }


def _audit(action, target=None, details=None, actor=None):
    try:
        now = datetime.now().isoformat()
        username = actor
        if username is None:
            user = getattr(g, "current_user", None)
            username = user.get("username") if isinstance(user, dict) else session.get("user")
        conn = _get_sqlite_conn()
        conn.execute(
            "INSERT INTO audit_log (actor, action, target, details_json, ip_address, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                action,
                target,
                json.dumps(details or {}),
                request.remote_addr if request else None,
                now,
            )
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _sync_legacy_auth_users_to_settings():
    try:
        conn = _get_sqlite_conn()
        rows = conn.execute(
            "SELECT username, password_hash, role, allowed_sites_json, disabled "
            "FROM auth_users ORDER BY username COLLATE NOCASE"
        ).fetchall()
        conn.close()
        legacy_users = []
        for row in rows:
            legacy_users.append({
                "username": row[0],
                "password_hash": row[1],
                "role": row[2] or "guest",
                "allowed_sites": _json_list(row[3]),
                "disabled": bool(row[4]),
            })
        settings = read_settings()
        auth = settings.get("auth") if isinstance(settings.get("auth"), dict) else {}
        auth["users"] = legacy_users
        auth["users_migrated_to_sqlite"] = True
        settings["auth"] = auth
        write_settings(settings)
    except Exception:
        pass


def _record_login_attempt(username, success, reason=""):
    try:
        conn = _get_sqlite_conn()
        conn.execute(
            "INSERT INTO auth_login_attempts (username, success, ip_address, user_agent, created_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                1 if success else 0,
                request.remote_addr,
                request.headers.get("User-Agent", "")[:500],
                datetime.now().isoformat(),
                reason,
            )
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _list_auth_users():
    _migrate_auth_users_from_settings()
    conn = _get_sqlite_conn()
    rows = conn.execute(
        "SELECT username, password_hash, role, allowed_sites_json, disabled, created_at, updated_at, last_login_at "
        "FROM auth_users ORDER BY username COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [_auth_user_from_row(row) for row in rows]


def _auth_user_count():
    _migrate_auth_users_from_settings()
    conn = _get_sqlite_conn()
    row = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()
    conn.close()
    return int(row[0] if row else 0)


def _active_admin_count(exclude_username=None):
    _migrate_auth_users_from_settings()
    conn = _get_sqlite_conn()
    if exclude_username:
        row = conn.execute(
            "SELECT COUNT(*) FROM auth_users WHERE role = 'admin' AND disabled = 0 AND username != ?",
            (exclude_username,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM auth_users WHERE role = 'admin' AND disabled = 0"
        ).fetchone()
    conn.close()
    return int(row[0] if row else 0)


def _migrate_auth_users_from_settings():
    if getattr(_migrate_auth_users_from_settings, "_done", False):
        return
    conn = _get_sqlite_conn()
    row = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()
    existing = int(row[0] if row else 0)
    if existing == 0:
        settings_row = conn.execute("SELECT json FROM json_store WHERE name = 'settings'").fetchone()
        settings = {}
        if settings_row:
            try:
                settings = json.loads(settings_row[0])
            except Exception:
                settings = {}
        auth = settings.get("auth", {}) if isinstance(settings, dict) else {}
        users = auth.get("users", []) if isinstance(auth, dict) else []
        now = datetime.now().isoformat()
        for user in users:
            if not isinstance(user, dict):
                continue
            username = (user.get("username") or "").strip()
            password_hash = user.get("password_hash") or ""
            role = user.get("role") or "guest"
            if not username or not password_hash or role not in ("admin", "operator", "guest"):
                continue
            allowed_sites = user.get("allowed_sites") if isinstance(user.get("allowed_sites"), list) else []
            conn.execute(
                "INSERT OR IGNORE INTO auth_users "
                "(username, password_hash, role, allowed_sites_json, disabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    password_hash,
                    role,
                    json.dumps(_json_list(allowed_sites)),
                    1 if user.get("disabled") else 0,
                    now,
                    now,
                )
            )
        if isinstance(settings, dict):
            auth = settings.get("auth", {}) if isinstance(settings.get("auth"), dict) else {}
            auth["users_migrated_to_sqlite"] = True
            settings["auth"] = auth
            conn.execute(
                "INSERT INTO json_store (name, json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
                ("settings", json.dumps(settings), now)
            )
            try:
                _write_json_file(SETTINGS_FILE, settings)
            except Exception:
                pass
    conn.commit()
    conn.close()
    _migrate_auth_users_from_settings._done = True
    _sync_legacy_auth_users_to_settings()


def _get_auth_config():
    _migrate_auth_users_from_settings()
    settings = read_settings()
    auth = settings.get("auth") if isinstance(settings, dict) else {}
    if not isinstance(auth, dict):
        auth = {}
    enabled = bool(auth.get("enabled", False))
    return {"enabled": enabled, "users": _list_auth_users()}

def _safe_site_name(site_name):
    if not site_name:
        return ""
    return "".join(ch for ch in str(site_name) if ch.isalnum() or ch in ("-", "_")).strip().lower()

def _auth_required():
    auth = _get_auth_config()
    return auth["enabled"] and len(auth["users"]) > 0

def _get_effective_user():
    if not _auth_required():
        return {
            "username": "local",
            "role": "admin",
            "allowed_sites": [],
            "disabled": False,
        }
    session_id = session.get("auth_session_id")
    user = _user_for_session(session_id) if session_id else None
    if user and user.get("disabled"):
        _revoke_session(session_id)
        session.pop("auth_session_id", None)
        session.pop("user", None)
        return None
    if user:
        g.current_user = user
    return user

def _find_user(username):
    if not username:
        return None
    _migrate_auth_users_from_settings()
    conn = _get_sqlite_conn()
    row = conn.execute(
        "SELECT username, password_hash, role, allowed_sites_json, disabled, created_at, updated_at, last_login_at "
        "FROM auth_users WHERE username = ?",
        (username,)
    ).fetchone()
    conn.close()
    return _auth_user_from_row(row)


def _create_auth_user(username, password, role, allowed_sites, disabled=False):
    now = datetime.now().isoformat()
    conn = _get_sqlite_conn()
    conn.execute(
        "INSERT INTO auth_users "
        "(username, password_hash, role, allowed_sites_json, disabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            username,
            generate_password_hash(password),
            role,
            json.dumps(_json_list(allowed_sites)),
            1 if disabled else 0,
            now,
            now,
        )
    )
    conn.commit()
    conn.close()
    _sync_legacy_auth_users_to_settings()


def _update_auth_user(username, updates):
    if not updates:
        return
    fields = []
    values = []
    if "role" in updates:
        fields.append("role = ?")
        values.append(updates["role"])
    if "allowed_sites" in updates:
        fields.append("allowed_sites_json = ?")
        values.append(json.dumps(_json_list(updates["allowed_sites"])))
    if "disabled" in updates:
        fields.append("disabled = ?")
        values.append(1 if updates["disabled"] else 0)
    if "password" in updates and updates["password"]:
        fields.append("password_hash = ?")
        values.append(generate_password_hash(updates["password"]))
    fields.append("updated_at = ?")
    values.append(datetime.now().isoformat())
    values.append(username)
    conn = _get_sqlite_conn()
    conn.execute(f"UPDATE auth_users SET {', '.join(fields)} WHERE username = ?", values)
    conn.commit()
    conn.close()
    _sync_legacy_auth_users_to_settings()


def _delete_auth_user(username):
    conn = _get_sqlite_conn()
    conn.execute("DELETE FROM auth_users WHERE username = ?", (username,))
    conn.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
        (datetime.now().isoformat(), username)
    )
    conn.commit()
    conn.close()


def _create_session_for_user(username):
    settings = read_settings()
    hours = int(settings.get("auth_session_hours") or 12)
    hours = max(1, min(hours, 168))
    now = datetime.now()
    session_id = uuid.uuid4().hex + uuid.uuid4().hex
    conn = _get_sqlite_conn()
    conn.execute(
        "INSERT INTO auth_sessions (session_id, username, created_at, expires_at, ip_address, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id,
            username,
            now.isoformat(),
            (now + timedelta(hours=hours)).isoformat(),
            request.remote_addr,
            request.headers.get("User-Agent", "")[:500],
        )
    )
    conn.execute("UPDATE auth_users SET last_login_at = ? WHERE username = ?", (now.isoformat(), username))
    conn.commit()
    conn.close()
    session.permanent = True
    session["auth_session_id"] = session_id
    session["user"] = username
    return session_id


def _user_for_session(session_id):
    if not session_id:
        return None
    now = datetime.now().isoformat()
    conn = _get_sqlite_conn()
    row = conn.execute(
        "SELECT u.username, u.password_hash, u.role, u.allowed_sites_json, u.disabled, "
        "u.created_at, u.updated_at, u.last_login_at "
        "FROM auth_sessions s JOIN auth_users u ON u.username = s.username "
        "WHERE s.session_id = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
        (session_id, now)
    ).fetchone()
    conn.close()
    return _auth_user_from_row(row)


def _revoke_session(session_id):
    if not session_id:
        return
    conn = _get_sqlite_conn()
    conn.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
        (datetime.now().isoformat(), session_id)
    )
    conn.commit()
    conn.close()


def _revoke_user_sessions(username):
    conn = _get_sqlite_conn()
    conn.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND revoked_at IS NULL",
        (datetime.now().isoformat(), username)
    )
    conn.commit()
    conn.close()

def _is_admin(user):
    return bool(user) and user.get("role") == "admin"

def _allowed_sites(user):
    sites = user.get("allowed_sites") if user else []
    if not isinstance(sites, list):
        return []
    return [s for s in sites if isinstance(s, str)]

def _can_read_site(user, site_name):
    if _is_admin(user):
        return True
    if not user or not site_name:
        return False
    allowed = _allowed_sites(user)
    return "*" in allowed or site_name in allowed

def _can_write_site(user, site_name):
    if _is_admin(user):
        return True
    if not user or user.get("role") != "operator":
        return False
    if not site_name:
        return False
    allowed = _allowed_sites(user)
    return "*" in allowed or site_name in allowed

def _filter_sites_for_user(sites, user):
    if _is_admin(user):
        return sites
    allowed = set(_allowed_sites(user))
    if "*" in allowed:
        return sites
    return [site for site in sites if site.get("name") in allowed]

def _filter_devices_for_user(devices, user):
    if _is_admin(user):
        return devices
    allowed = set(_allowed_sites(user))
    if "*" in allowed:
        return devices
    return [device for device in devices if device.get("site") in allowed]

def _site_from_module_config(config):
    if not isinstance(config, dict):
        return None
    for key in ("site_name", "site", "site_id", "target_site"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

def _require_role(*roles):
    user = _get_effective_user()
    if not user:
        return None, (jsonify({"error": "auth_required"}), 401)
    if roles and user.get("role") not in roles:
        return user, (jsonify({"error": "forbidden"}), 403)
    return user, None

def _sanitize_user(user):
    if not isinstance(user, dict):
        return None
    return {
        "username": user.get("username"),
        "role": user.get("role", "guest"),
        "allowed_sites": _allowed_sites(user),
        "disabled": bool(user.get("disabled", False)),
    }

@app.before_request
def enforce_auth():
    if not _auth_required():
        return None
    path = request.path or ""
    if path.startswith("/static/"):
        return None
    if path.startswith("/api/auth"):
        return None
    if path == "/":
        return None
    if path.startswith("/api/") or path.startswith("/generated_maps/") or path.startswith("/static/maps/"):
        user = _get_effective_user()
        if not user:
            session.pop("auth_session_id", None)
            session.pop("user", None)
            if path.startswith("/api/"):
                return jsonify({"error": "auth_required"}), 401
            return ("Unauthorized", 401)
    return None

@app.before_request
def start_timer():
    g._req_start = time.perf_counter()

@app.after_request
def log_request(response):
    start = getattr(g, "_req_start", None)
    if start is not None:
        path = request.path or ""
        if path.startswith("/api/") or path.startswith("/generated_maps/") or path.startswith("/static/maps/"):
            _log_perf(path, start)
    return response

# ==================== MODULE SYSTEM ====================

def discover_modules():
    """Find all available modules in modules/ directory"""
    modules = []
    
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
        return modules
    
    for module_name in os.listdir(MODULES_DIR):
        module_path = os.path.join(MODULES_DIR, module_name)
        module_json = os.path.join(module_path, 'module.json')
        
        if os.path.isdir(module_path) and os.path.exists(module_json):
            try:
                with open(module_json, 'r') as f:
                    module_info = json.load(f)
                    module_info['id'] = module_name
                    modules.append(module_info)
            except (json.JSONDecodeError, KeyError):
                # Skip invalid modules
                continue
    
    return modules

class ModuleRunner:
    """Run modules asynchronously with status tracking"""
    
    def __init__(self):
        self.running_modules = {}
        self.module_results = {}
        self.lock = threading.Lock()
        # Limit concurrent module executions to avoid resource contention.
        self.max_concurrent = int(os.environ.get("CMAPPER_MAX_MODULES", "2") or 2)
        self.semaphore = threading.Semaphore(self.max_concurrent)

    def set_max_concurrent(self, value):
        try:
            new_max = int(value)
        except (TypeError, ValueError):
            return
        if new_max < 1:
            new_max = 1
        with self.lock:
            running_count = sum(1 for item in self.running_modules.values() if item.get("status") == "running")
            self.max_concurrent = new_max
            self.semaphore = threading.Semaphore(new_max)
            for _ in range(min(running_count, new_max)):
                try:
                    self.semaphore.acquire(False)
                except Exception:
                    break
    
    def run_module(self, module_id, config):
        """Run a module in background thread"""
        thread_id = str(uuid.uuid4())[:8]
        
        def module_thread():
            config_file = None
            acquired = False
            try:
                log_file = os.path.join(BASE_DIR, f"module_log_{thread_id}.txt")
                site_name = config.get("site_name") if isinstance(config, dict) else None
                schedule_id = config.get("schedule_id") if isinstance(config, dict) else None
                schedule_name = config.get("schedule_name") if isinstance(config, dict) else None
                # Update status
                with self.lock:
                    self.running_modules[thread_id] = {
                        "module_id": module_id,
                        "status": "queued",
                        "queued_at": datetime.now().isoformat(),
                        "progress": 0,
                        "log_file": log_file,
                        "site_name": site_name,
                        "schedule_id": schedule_id,
                        "schedule_name": schedule_name
                    }

                # Respect concurrency cap
                self.semaphore.acquire()
                acquired = True
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = "running"
                        self.running_modules[thread_id]["start_time"] = datetime.now().isoformat()
                
                print(f"=== DEBUG: Starting module {module_id} ===", file=sys.stderr)
                print(f"Config: {_mask_sensitive(config)}", file=sys.stderr)
                
                # Prepare module execution
                module_dir = os.path.join(MODULES_DIR, module_id)
                module_script = os.path.join(module_dir, f"{module_id}.py")
                
                if not os.path.exists(module_script):
                    # Try to find any .py file in module directory
                    py_files = [f for f in os.listdir(module_dir) if f.endswith('.py')]
                    if py_files:
                        module_script = os.path.join(module_dir, py_files[0])
                    else:
                        raise FileNotFoundError(f"No Python script found for module {module_id}")
                
                # Create temp config file
                temp_config = {
                    **config,
                    "database_path": os.path.abspath(SQLITE_DB_FILE),
                    "module_id": module_id,
                    "thread_id": thread_id,
                    "log_file": log_file
                }
                
                config_file = f"module_config_{thread_id}.json"
                with open(config_file, 'w') as f:
                    json.dump(temp_config, f, indent=2)
                
                print(f"Config file created: {config_file}", file=sys.stderr)
                
                # Run the module
                with self.lock:
                    self.running_modules[thread_id]["progress"] = 25
                
                # Use the system's Python interpreter
                python_executable = "python" if os.name == "nt" else "python3"
                
                print(f"Running: {python_executable} {module_script} {config_file}", file=sys.stderr)
                print(f"Current dir: {os.getcwd()}", file=sys.stderr)
                try:
                    with open(log_file, "a", encoding="utf-8") as lf:
                        lf.write(f"Running: {python_executable} {module_script} {config_file}\n")
                except Exception:
                    pass
                result = subprocess.run(
                    [python_executable, module_script, config_file],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=os.path.dirname(os.path.abspath(__file__))
                )
                
                print(f"=== DEBUG: Module execution result ===", file=sys.stderr)
                print(f"Return code: {result.returncode}", file=sys.stderr)
                print(f"STDOUT: {result.stdout[:500]}", file=sys.stderr)
                print(f"STDERR: {result.stderr}", file=sys.stderr)
                try:
                    with open(log_file, "a", encoding="utf-8") as lf:
                        hide_mac_success_output = (
                            module_id in ("mac_table_search", "mac_group_map")
                            and result.returncode == 0
                            and '"status": "error"' not in result.stdout
                        )
                        if not hide_mac_success_output:
                            lf.write(f"Return code: {result.returncode}\n")
                            if result.stdout:
                                lf.write("STDOUT:\n")
                                lf.write(result.stdout)
                                if not result.stdout.endswith("\n"):
                                    lf.write("\n")
                            if result.stderr:
                                lf.write("STDERR:\n")
                                lf.write(result.stderr)
                                if not result.stderr.endswith("\n"):
                                    lf.write("\n")
                except Exception:
                    pass
                
                with self.lock:
                    self.running_modules[thread_id]["progress"] = 75
                
                # Parse module output
                final_status = "completed"
                if result.returncode == 0:
                    try:
                        module_output = json.loads(result.stdout.strip())
                        if isinstance(module_output, dict) and str(module_output.get("status", "")).lower() == "error":
                            final_status = "failed"
                        with self.lock:
                            self.module_results[thread_id] = {
                                "status": final_status,
                                "output": module_output,
                                "completed_at": datetime.now().isoformat(),
                                "log_file": log_file
                            }
                            if thread_id in self.running_modules:
                                self.running_modules[thread_id]["output"] = module_output
                    except json.JSONDecodeError:
                        with self.lock:
                            self.module_results[thread_id] = {
                                "status": "completed",
                                "output": {"message": result.stdout.strip()},
                                "completed_at": datetime.now().isoformat(),
                                "log_file": log_file
                            }
                            if thread_id in self.running_modules:
                                self.running_modules[thread_id]["output"] = {"message": result.stdout.strip()}
                else:
                    final_status = "failed"
                    with self.lock:
                        self.module_results[thread_id] = {
                            "status": "failed",
                            "error": result.stderr,
                            "completed_at": datetime.now().isoformat(),
                            "log_file": log_file
                        }

                # Update final status
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = final_status
                        self.running_modules[thread_id]["progress"] = 100
                        self.running_modules[thread_id]["completed_at"] = datetime.now().isoformat()

                
                # Clean up temp config file
                try:
                    if config_file and os.path.exists(config_file):
                        os.remove(config_file)
                except Exception:
                    pass
                
            except subprocess.TimeoutExpired:
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = "timeout"
                        self.running_modules[thread_id]["progress"] = 100
            except Exception as e:
                with self.lock:
                    if thread_id in self.running_modules:
                        self.running_modules[thread_id]["status"] = "error"
                        self.running_modules[thread_id]["error"] = str(e)
                        self.running_modules[thread_id]["progress"] = 100
            finally:
                try:
                    if config_file and os.path.exists(config_file):
                        os.remove(config_file)
                except Exception:
                    pass
                if acquired:
                    try:
                        self.semaphore.release()
                    except ValueError:
                        pass
                # Cleanup after delay
                threading.Timer(300, self.cleanup_thread, args=[thread_id]).start()
        
        # Start the thread
        thread = threading.Thread(target=module_thread)
        thread.daemon = True
        thread.start()
        
        return thread_id
    
    def get_module_status(self, thread_id):
        """Get status of a running module"""
        with self.lock:
            if thread_id in self.running_modules:
                return self.running_modules[thread_id]
            elif thread_id in self.module_results:
                return self.module_results[thread_id]
        return None
    
    def cleanup_thread(self, thread_id):
        """Clean up old thread data"""
        log_file = None
        with self.lock:
            running = self.running_modules.pop(thread_id, None)
            result = self.module_results.pop(thread_id, None)
            log_file = (running or {}).get("log_file") or (result or {}).get("log_file")
        _cleanup_module_logs()
        _cleanup_module_configs()
    
    def get_all_status(self):
        """Get status of all modules"""
        with self.lock:
            running_jobs = []
            for thread_id, info in self.running_modules.items():
                entry = copy.deepcopy(info)
                entry["thread_id"] = thread_id
                running_jobs.append(entry)
            return {
                "running": list(self.running_modules.keys()),
                "completed": list(self.module_results.keys()),
                "running_jobs": running_jobs
            }

    def get_running_jobs(self):
        with self.lock:
            jobs = []
            for thread_id, info in self.running_modules.items():
                entry = copy.deepcopy(info)
                entry["thread_id"] = thread_id
                jobs.append(entry)
            return jobs

# ==================== SCHEDULED MODULE RUNNER ====================

class ScheduleRunner:
    def __init__(self, poll_interval=2):
        self.poll_interval = poll_interval
        self.state = {}
        self.lock = threading.Lock()
        self.dirty = False
        self._load_state()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _load_state(self):
        settings = read_settings() or {}
        saved = settings.get("schedule_state", {})
        if not isinstance(saved, dict):
            return
        with self.lock:
            for schedule_id, entry in saved.items():
                if not isinstance(entry, dict):
                    continue
                next_run_at = entry.get("next_run_at")
                last_run_at = entry.get("last_run_at")
                if isinstance(next_run_at, str):
                    try:
                        next_run_at = datetime.fromisoformat(next_run_at)
                    except Exception:
                        next_run_at = None
                if isinstance(last_run_at, str):
                    try:
                        last_run_at = datetime.fromisoformat(last_run_at)
                    except Exception:
                        last_run_at = None
                status = entry.get("status") or "idle"
                if status == "running":
                    status = "idle"
                self.state[schedule_id] = {
                    "status": status,
                    "next_run_at": next_run_at,
                    "last_run_at": last_run_at,
                    "last_result": entry.get("last_result"),
                    "running": False,
                    "active_sites": entry.get("active_sites", 0),
                    "completed_sites": entry.get("completed_sites", 0),
                    "total_sites": entry.get("total_sites", 0),
                    "started_at": None
                }

    def _persist_state(self):
        payload = {}
        with self.lock:
            for schedule_id, entry in self.state.items():
                if not isinstance(entry, dict):
                    continue
                next_run_at = entry.get("next_run_at")
                last_run_at = entry.get("last_run_at")
                payload[schedule_id] = {
                    "status": entry.get("status", "idle"),
                    "next_run_at": next_run_at.isoformat() if isinstance(next_run_at, datetime) else next_run_at,
                    "last_run_at": last_run_at.isoformat() if isinstance(last_run_at, datetime) else last_run_at,
                    "last_result": entry.get("last_result"),
                    "active_sites": entry.get("active_sites", 0),
                    "completed_sites": entry.get("completed_sites", 0),
                    "total_sites": entry.get("total_sites", 0)
                }
        settings = read_settings() or {}
        settings["schedule_state"] = payload
        write_settings(settings)

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as exc:
                print(f"Scheduler error: {exc}", file=sys.stderr)
            time.sleep(self.poll_interval)

    def _tick(self):
        settings = read_settings() or {}
        schedules = settings.get("module_schedules", [])
        schedule_ids = set()
        now = datetime.now()

        for schedule in schedules:
            if not isinstance(schedule, dict):
                continue
            schedule_id = schedule.get("id")
            if not schedule_id:
                continue
            schedule_ids.add(schedule_id)
            enabled = bool(schedule.get("enabled", True))

            with self.lock:
                state = self.state.setdefault(schedule_id, {
                    "status": "idle",
                    "next_run_at": None,
                    "last_run_at": None,
                    "last_result": None,
                    "running": False
                })

            if not enabled:
                with self.lock:
                    state["status"] = "disabled"
                    state["next_run_at"] = None
                    state["running"] = False
                    self.dirty = True
                continue

            with self.lock:
                next_run_at = state["next_run_at"]
                running = state["running"]

            if running:
                continue

            if next_run_at and now >= next_run_at:
                runner = threading.Thread(target=self._run_schedule, args=(copy.deepcopy(schedule),), daemon=True)
                runner.start()

        with self.lock:
            for schedule_id in list(self.state.keys()):
                if schedule_id not in schedule_ids:
                    self.state.pop(schedule_id, None)
                    self.dirty = True

        if self.dirty:
            self.dirty = False
            self._persist_state()

    def _resolve_sites(self, schedule):
        scope = schedule.get("site_scope", {}) if isinstance(schedule.get("site_scope"), dict) else {}
        mode = (scope.get("mode") or "selected").lower()
        selected = scope.get("sites") or []
        if mode == "all":
            data = read_database()
            return [s.get("name") for s in data.get("sites", []) if s.get("name")]
        return [name for name in selected if isinstance(name, str) and name.strip()]

    def _run_site_pipeline(self, site_name, schedule, available_modules, schedule_id=None):
        delay = int(schedule.get("delay_between_modules_sec") or 0)
        modules = schedule.get("modules") or []
        results = []
        settings = read_settings()
        module_creds = settings.get("module_credentials", {}) if isinstance(settings, dict) else {}
        for entry in modules:
            if not isinstance(entry, dict):
                continue
            module_id = entry.get("module_id")
            if not module_id:
                continue
            if module_id not in available_modules:
                results.append({"module_id": module_id, "status": "missing"})
                continue
            params = copy.deepcopy(entry.get("parameters") or {})
            scope_mode = ((schedule.get("site_scope") or {}).get("mode") or "").lower()
            if module_id in ("ubiquiti_cdp_reader", "uniview_nvr_capture"):
                targets = params.get("targets")
                if isinstance(targets, dict) and targets.get("device_ids") == "__AUTO__":
                    params["targets"] = {"auto": True}
                elif targets is None:
                    params["targets"] = {"auto": True}
                if scope_mode == "all":
                    if isinstance(params.get("targets"), dict):
                        params["targets"]["auto_on_empty"] = True
                        params["targets"]["auto"] = True
                        if "device_ids" in params["targets"]:
                            params["targets"]["device_ids"] = []
            if module_id == "uniview_nvr_capture" and not params.get("nic"):
                params["nic"] = "NIC1"
            if module_id == "uniview_nvr_capture" and not params.get("ip_mode"):
                params["ip_mode"] = "filter"
            credential_profile = entry.get("credential_profile")
            if credential_profile:
                profiles = module_creds.get(module_id, []) if isinstance(module_creds, dict) else []
                for profile in profiles:
                    if isinstance(profile, dict) and profile.get("name") == credential_profile:
                        params["username"] = profile.get("username", "")
                        params["password"] = profile.get("password", "")
                        break
            # Avoid leaking/using credential_profile inside module params
            params.pop("credential_profile", None)
            config = {
                "site_name": site_name,
                "parameters": params,
                "schedule_id": schedule_id,
                "schedule_name": schedule.get("name") if isinstance(schedule, dict) else None
            }
            thread_id = module_runner.run_module(module_id, config)
            while True:
                status = module_runner.get_module_status(thread_id) or {}
                state = status.get("status")
                if state in ("completed", "failed", "error", "timeout"):
                    results.append({"module_id": module_id, "status": state})
                    break
                time.sleep(1)
            if delay > 0:
                time.sleep(delay)
        return results

    def _run_schedule(self, schedule):
        schedule_id = schedule.get("id")
        if not schedule_id:
            return
        with self.lock:
            state = self.state.setdefault(schedule_id, {})
            state["status"] = "running"
            state["running"] = True
            state["active_sites"] = 0
            state["completed_sites"] = 0
            state["total_sites"] = 0
            state["started_at"] = datetime.now()
            self.dirty = True

        errors = _validate_schedule_config(schedule)
        if errors:
            with self.lock:
                state["last_run_at"] = datetime.now()
                state["last_result"] = {"error": "invalid_schedule", "details": errors}
                state["status"] = "idle"
                state["running"] = False
                state["next_run_at"] = None
                self.dirty = True
            self._persist_state()
            return

        sites = self._resolve_sites(schedule)
        with self.lock:
            state["total_sites"] = len(sites)
        run_mode = (schedule.get("site_run_mode") or "sequential").lower()
        schedule_result = {"sites": len(sites), "results": {}}

        available_modules = {m.get("id") for m in discover_modules() if isinstance(m, dict)}
        if not sites:
            schedule_result["error"] = "no_sites"
        else:
            def run_site(site):
                with self.lock:
                    state["active_sites"] = int(state.get("active_sites", 0)) + 1
                try:
                    return self._run_site_pipeline(site, schedule, available_modules, schedule_id)
                finally:
                    with self.lock:
                        state["completed_sites"] = int(state.get("completed_sites", 0)) + 1
                        state["active_sites"] = max(0, int(state.get("active_sites", 0)) - 1)

            if run_mode == "concurrent":
                with ThreadPoolExecutor(max_workers=len(sites)) as pool:
                    future_map = {pool.submit(run_site, site): site for site in sites}
                    for future in as_completed(future_map):
                        site = future_map.get(future)
                        try:
                            schedule_result["results"][site] = future.result()
                        except Exception as exc:
                            schedule_result["results"][site] = [{"error": str(exc)}]
            else:
                for site in sites:
                    try:
                        schedule_result["results"][site] = run_site(site)
                    except Exception as exc:
                        schedule_result["results"][site] = [{"error": str(exc)}]

        repeat_minutes = int(schedule.get("repeat_interval_min") or 0)
        with self.lock:
            state["last_run_at"] = datetime.now()
            state["last_result"] = schedule_result
            state["status"] = "idle"
            state["running"] = False
            if repeat_minutes > 0:
                state["next_run_at"] = datetime.now() + timedelta(minutes=repeat_minutes)
            else:
                state["next_run_at"] = None
            self.dirty = True
        self._persist_state()

    def trigger_run(self, schedule_id):
        with self.lock:
            state = self.state.setdefault(schedule_id, {})
            state["next_run_at"] = datetime.now()
            return True

    def run_now(self, schedule):
        schedule_id = schedule.get("id")
        if not schedule_id:
            return False
        if not self._resolve_sites(schedule):
            return False
        with self.lock:
            state = self.state.setdefault(schedule_id, {})
            if state.get("running"):
                return False
            state["next_run_at"] = datetime.now()
            self.dirty = True
        self._persist_state()
        runner = threading.Thread(target=self._run_schedule, args=(copy.deepcopy(schedule),), daemon=True)
        runner.start()
        return True

    def get_schedule_state(self, schedule_id):
        with self.lock:
            return copy.deepcopy(self.state.get(schedule_id) or {})

    def get_all_states(self):
        with self.lock:
            return copy.deepcopy(self.state)

# Global module runner
module_runner = ModuleRunner()
# Global scheduler
schedule_runner = ScheduleRunner()

def _cleanup_module_logs() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    pattern = os.path.join(base_dir, "module_log_*.txt")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except OSError:
            pass

def _cleanup_module_configs() -> None:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    pattern = os.path.join(base_dir, "module_config_*.json")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except OSError:
            pass

# ==================== API ENDPOINTS ====================

@app.route('/')
def index():
    
    t0 = time.perf_counter()
    """Serve the main interface"""
    print(f"[PERF] / took {time.perf_counter() - t0:.2f}s")
    return render_template('index.html')

@app.route('/api/database')
def get_database():
    """Get complete database"""
    user, err = _require_role("admin")
    if err:
        return err
    return jsonify(read_database())

@app.route('/api/sites', methods=['GET', 'POST'])
def handle_sites():
    """Manage sites"""
    if request.method == 'GET':
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        data = read_database()
        sites = _filter_sites_for_user(data.get("sites", []), user)
        return jsonify(sites)
    
    elif request.method == 'POST':
        user, err = _require_role("admin")
        if err:
            return err
        site_data = request.json
        if isinstance(site_data, dict) and isinstance(site_data.get("name"), str):
            site_data["name"] = site_data["name"].strip()
        
        if not site_data.get("name") or not site_data.get("root_ip"):
            return jsonify({"error": "Name and root_ip are required"}), 400
        
        data = read_database()
        
        # Check for duplicate site name
        if any(s.get("name") == site_data["name"] for s in data.get("sites", [])):
            return jsonify({"error": "Site with this name already exists"}), 400
        
        # Add site
        new_site = {
            "id": str(uuid.uuid4())[:8],
            "name": site_data["name"],
            "root_ip": site_data["root_ip"],
            "created": datetime.now().isoformat(),
            "last_scan": None,
            "locked": False,
            "notes": site_data.get("notes", "")
        }
        
        data.setdefault("sites", []).append(new_site)
        write_database(data)
        
        return jsonify(new_site)


@app.route('/api/generate_map', methods=['POST'])
def api_generate_map():
    """Generate map from database"""
    try:
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        if user.get("role") not in ("admin", "operator"):
            return jsonify({"error": "forbidden"}), 403
        # Import the generator function
        from generate_map import generate_map_from_database
        
        result = generate_map_from_database()
        
        if result.get('status') == 'success':
            return jsonify({
                "success": True,
                "message": result['message'],
                "map_url": "/static/maps/Roodan_map.html",
                "device_count": result['device_count'],
                "connection_count": result['connection_count']
            })
        else:
            return jsonify({"error": result.get('message', 'Unknown error')}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/generate_visual_map', methods=['POST'])
def api_generate_visual_map():
    """Generate a visual map for a specific site"""
    try:
        data = request.get_json() or {}
        site_name = data.get('site_name')
        spacing = data.get('spacing')
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        if not _can_write_site(user, site_name):
            return jsonify({"error": "forbidden"}), 403

        from generale_visual_map import generate_visual_map

        result = generate_visual_map(site_name, spacing)

        if result.get('status') == 'success':
            return jsonify({
                "success": True,
                "message": result['message'],
                "map_file": result['map_file'],
                "map_url": result['map_url'],
                "site_name": result['site_name'],
                "device_count": result['device_count'],
                "connection_count": result['connection_count']
            })
        return jsonify({"error": result.get('message', 'Unable to generate map')}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/generate_text_map', methods=['POST'])
def api_generate_text_map():
    """Generate a text map for a specific site"""
    try:
        data = request.get_json() or {}
        site_name = data.get('site_name')
        
        if not site_name:
            return jsonify({"error": "site_name is required"}), 400
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        if not _can_write_site(user, site_name):
            return jsonify({"error": "forbidden"}), 403
        
        from generate_map import generate_map_from_database
        
        result = generate_map_from_database(site_name)
        
        if result.get('status') == 'success':
            return jsonify({
                "success": True,
                "message": result['message'],
                "map_file": result['map_file'],
                "map_url": result['map_url'],
                "site_name": result['site_name'],
                "device_count": result['device_count'],
                "connection_count": result['connection_count']
            })
        else:
            return jsonify({"error": result.get('message', 'Unknown error')}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/sites/<site_id>', methods=['PUT', 'DELETE'])
def handle_site(site_id):
    """Update or delete a site"""
    user, err = _require_role("admin")
    if err:
        return err
    data = read_database()
    
    # Find site
    site_index = None
    for i, site in enumerate(data.get("sites", [])):
        if site.get("id") == site_id:
            site_index = i
            break
    
    if site_index is None:
        return jsonify({"error": "Site not found"}), 404
    
    if request.method == 'PUT':
        update_data = request.json
        current_site = data["sites"][site_index]
        old_name = current_site.get("name")
        
        for field in ["name", "root_ip", "notes", "locked"]:
            if field in update_data:
                value = update_data[field]
                if field == "name" and isinstance(value, str):
                    value = value.strip()
                current_site[field] = value

        new_name = current_site.get("name")
        if old_name and new_name and old_name != new_name:
            for dev in data.get("devices", []):
                if dev.get("site") == old_name:
                    dev["site"] = new_name

            settings = read_settings()
            if isinstance(settings, dict):
                if settings.get("default_site") == old_name:
                    settings["default_site"] = new_name
                schedules = settings.get("module_schedules", []) if isinstance(settings.get("module_schedules"), list) else []
                for sched in schedules:
                    if not isinstance(sched, dict):
                        continue
                    scope = sched.get("site_scope")
                    if not isinstance(scope, dict):
                        continue
                    sites = scope.get("sites")
                    if not isinstance(sites, list):
                        continue
                    scope["sites"] = [new_name if s == old_name else s for s in sites]
                write_settings(settings)

        write_database(data)
        return jsonify(current_site)
    
    elif request.method == 'DELETE':
        site_name = data["sites"][site_index]["name"]
        data["sites"].pop(site_index)
        data["devices"] = [d for d in data.get("devices", []) if d.get("site") != site_name]
        write_database(data)
        return jsonify({"success": True})

@app.route('/api/devices')
def get_devices():
    """Get all devices"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    data = read_database()
    site_filter = request.args.get('site')
    
    devices = data.get("devices", [])
    devices = _filter_devices_for_user(devices, user)
    if site_filter:
        if not _can_read_site(user, site_filter):
            return jsonify({"error": "forbidden"}), 403
        devices = [d for d in devices if d.get("site") == site_filter]
    return jsonify(devices)

@app.route('/api/devices/<device_id>', methods=['PUT', 'DELETE'])
def handle_device(device_id):
    """Update or delete a device"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") == "guest":
        return jsonify({"error": "forbidden"}), 403
    data = read_database()
    
    # Find device
    device_index = None
    for i, device in enumerate(data.get("devices", [])):
        if device.get("id") == device_id:
            device_index = i
            break
    
    if device_index is None:
        return jsonify({"error": "Device not found"}), 404
    if not _can_write_site(user, data["devices"][device_index].get("site")):
        return jsonify({"error": "forbidden"}), 403
    
    if request.method == 'PUT':
        update_data = request.json
        current_device = data["devices"][device_index]
        
        updatable_fields = [
            "name",
            "ip",
            "mac",
            "type",
            "status",
            "notes",
            "locked",
            "always_show_on_map",
            "os",
            "vendor",
            "platform",
            "model",
            "oui",
            "oui_label",
            "oui_range_start",
            "oui_range_end",
            "vlan",
            "domain",
            "domain_name",
            "domain_lookup_name",
            "domain_query",
            "domain_resolved_ip",
            "domain_last_checked",
        ]
        for field in updatable_fields:
            if field in update_data:
                current_device[field] = update_data[field]

        def _parse_connections(text):
            if not text:
                return []
            lines = [line.strip() for line in str(text).splitlines() if line.strip()]
            parsed = []
            for line in lines:
                parts = [part.strip() for part in line.split(",")]
                if len(parts) < 2:
                    continue
                while len(parts) < 4:
                    parts.append("")
                parsed.append(
                    {
                        "local_interface": parts[0] or "unknown",
                        "remote_lookup": parts[1],
                        "remote_interface": parts[2] or "unknown",
                        "protocol": parts[3] or "manual",
                    }
                )
            return parsed

        if "connections_input" in update_data or "connections_list" in update_data:
            create_missing = bool(update_data.get("create_missing_nodes", True))
            parsed = _parse_connections(update_data.get("connections_input"))
            list_entries = update_data.get("connections_list")
            if isinstance(list_entries, list):
                for entry in list_entries:
                    if not isinstance(entry, dict):
                        continue
                    parsed.append(
                        {
                            "local_interface": entry.get("local_interface") or "unknown",
                            "remote_lookup": entry.get("remote_device_id") or entry.get("remote_lookup") or "",
                            "remote_interface": entry.get("remote_interface") or "unknown",
                            "protocol": entry.get("protocol") or "manual",
                            "lookup_by_id": True,
                        }
                    )
            connections = []
            devices = data.get("devices", [])

            def _find_device(token):
                token_norm = str(token).strip().lower()
                for device in devices:
                    if device.get("site") != current_device.get("site"):
                        continue
                    if str(device.get("id", "")).lower() == token_norm:
                        return device
                    if str(device.get("name", "")).lower() == token_norm:
                        return device
                    if str(device.get("ip", "")).lower() == token_norm:
                        return device
                return None

            def _create_placeholder(token):
                placeholder = {
                    "id": f"dev_{str(uuid.uuid4())[:8]}",
                    "site": current_device.get("site"),
                    "name": token,
                    "ip": token if str(token).count(".") == 3 else "",
                    "type": "unknown",
                    "model": "",
                    "platform": "",
                    "vendor": "",
                    "os": "",
                    "discovered_by": "manual",
                    "discovered_at": datetime.now().isoformat(),
                    "last_seen": datetime.now().isoformat(),
                    "last_modified": datetime.now().isoformat(),
                    "status": "unknown",
                    "reachable": False,
                    "config_backup": {"enabled": False},
                    "connections": [],
                    "credentials_used": None,
                    "modules_successful": [],
                    "modules_failed": [],
                    "locked": False,
                    "notes": "Placeholder created via manual edit"
                }
                devices.append(placeholder)
                return placeholder

            for entry in parsed:
                remote_token = entry.get("remote_lookup")
                if not remote_token:
                    continue
                remote_device = _find_device(remote_token)
                if not remote_device and create_missing:
                    remote_device = _create_placeholder(remote_token)
                if not remote_device:
                    continue
                connections.append(
                    {
                        "id": f"conn_{str(uuid.uuid4())[:8]}",
                        "local_interface": entry.get("local_interface") or "unknown",
                        "remote_device": remote_device.get("id"),
                        "remote_interface": entry.get("remote_interface") or "unknown",
                        "protocol": entry.get("protocol") or "manual",
                        "discovered_at": datetime.now().isoformat(),
                        "status": "up",
                    }
                )

            current_device["connections"] = connections

        current_device["last_modified"] = datetime.now().isoformat()
        write_database(data)
        return jsonify(current_device)
    
    elif request.method == 'DELETE':
        data["devices"].pop(device_index)
        write_database(data)
        return jsonify({"success": True})

@app.route('/api/devices/bulk_delete', methods=['POST'])
def bulk_delete_devices():
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") == "guest":
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json() or {}
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids_required"}), 400
    ids = [i for i in ids if isinstance(i, str) and i.strip()]
    if not ids:
        return jsonify({"error": "ids_required"}), 400

    data = read_database()
    devices = data.get("devices", [])
    id_set = set(ids)
    deleted = []
    forbidden = []
    remaining = []
    for device in devices:
        device_id = device.get("id")
        if device_id in id_set:
            if not _can_write_site(user, device.get("site")):
                forbidden.append(device_id)
                remaining.append(device)
            else:
                deleted.append(device_id)
        else:
            remaining.append(device)

    data["devices"] = remaining
    write_database(data)
    missing = [i for i in ids if i not in deleted and i not in forbidden]
    return jsonify({
        "success": True,
        "deleted": deleted,
        "forbidden": forbidden,
        "missing": missing
    })

@app.route('/api/modules')
def get_modules():
    """Get all available modules"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    print("=== /api/modules endpoint called ===", file=sys.stderr)
    modules = discover_modules()
    print(f"Found {len(modules)} modules", file=sys.stderr)
    return jsonify(modules)

@app.route('/api/modules/<module_id>/run', methods=['POST'])
def run_module(module_id):
    """Run a module"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    print(f"=== /api/modules/{module_id}/run called ===", file=sys.stderr)
    
    config = request.json
    if isinstance(config, dict):
        params = config.get("parameters")
        if isinstance(params, dict):
            if isinstance(params.get("targets"), dict) and params["targets"].get("device_ids") == "__AUTO__":
                params["targets"] = {"auto": True}
            if params.get("nic") is None and module_id == "uniview_nvr_capture":
                params["nic"] = "NIC1"
            if params.get("targets") is None and module_id in ("ubiquiti_cdp_reader", "uniview_nvr_capture"):
                params["targets"] = {"auto": True}
            if module_id == "ubiquiti_cdp_reader" and isinstance(params.get("targets"), dict) and params["targets"].get("auto") is True:
                params["targets"]["device_types"] = ["ap"]
            if module_id == "uniview_nvr_capture" and isinstance(params.get("targets"), dict) and params["targets"].get("auto") is True:
                params["targets"]["device_types"] = ["nvr"]
        if isinstance(params, dict) and params.get("credential_profile"):
            settings = read_settings()
            module_creds = settings.get("module_credentials", {}) if isinstance(settings, dict) else {}
            profiles = module_creds.get(module_id, []) if isinstance(module_creds, dict) else []
            if module_id in ("mac_table_search", "mac_group_map") and isinstance(module_creds, dict):
                known_names = {profile.get("name") for profile in profiles if isinstance(profile, dict)}
                profiles = list(profiles) + [
                    profile for profile in module_creds.get("cdp_discovery", [])
                    if isinstance(profile, dict) and profile.get("name") not in known_names
                ]
            profile_name = str(params.get("credential_profile")).strip()
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("name") == profile_name:
                    params["username"] = profile.get("username", "")
                    params["password"] = profile.get("password", "")
                    break
            params.pop("credential_profile", None)
        # Persist last-used parameters (excluding passwords)
        try:
            settings = read_settings()
            module_last = settings.get("module_last_params", {})
            if not isinstance(module_last, dict):
                module_last = {}
            site_name = _site_from_module_config(config) or "*"
            safe_params = {}
            if isinstance(params, dict):
                for key, value in params.items():
                    if str(key).lower() in ("password", "pass", "secret", "token", "api_key"):
                        continue
                    safe_params[key] = value
            module_last.setdefault(module_id, {})
            if isinstance(module_last[module_id], dict):
                module_last[module_id][site_name] = safe_params
            settings["module_last_params"] = module_last
            write_settings(settings)
        except Exception:
            pass
    safe_config = json.loads(json.dumps(config or {}))
    if isinstance(safe_config, dict):
        params = safe_config.get("parameters")
        if isinstance(params, dict) and "password" in params:
            params["password"] = "********"
        if "password" in safe_config:
            safe_config["password"] = "********"
    print(f"Request data: {json.dumps(safe_config, indent=2)}", file=sys.stderr)
    site_name = _site_from_module_config(config)
    if site_name and not _can_write_site(user, site_name):
        return jsonify({"error": "forbidden"}), 403
    
    # Validate module exists
    modules = discover_modules()
    module_exists = any(m.get('id') == module_id for m in modules)
    
    if not module_exists:
        print(f"Module {module_id} not found. Available: {[m.get('id') for m in modules]}", file=sys.stderr)
        return jsonify({"error": f"Module {module_id} not found"}), 404
    
    print(f"Module {module_id} found. Starting execution...", file=sys.stderr)
    
    # Run the module
    thread_id = module_runner.run_module(module_id, config)
    
    return jsonify({
        "thread_id": thread_id,
        "status": "started",
        "message": f"Module {module_id} started"
    })

@app.route('/api/modules/status/<thread_id>')
def get_module_status(thread_id):
    """Get module execution status"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    status = module_runner.get_module_status(thread_id)
    
    if status:
        return jsonify(status)
    else:
        return jsonify({"error": "Thread not found"}), 404

@app.route('/api/modules/log/<thread_id>')
def get_module_log(thread_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    delete_after = request.args.get("delete") == "1"
    status = module_runner.get_module_status(thread_id)
    log_file = status.get("log_file") if isinstance(status, dict) else None
    if not log_file:
        candidate = os.path.join(BASE_DIR, f"module_log_{thread_id}.txt")
        if os.path.exists(candidate):
            log_file = candidate
    if not log_file or not os.path.exists(log_file):
        return jsonify({"lines": []})
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        if delete_after:
            try:
                os.remove(log_file)
            except OSError:
                pass
        return jsonify({"lines": lines})
    except OSError:
        return jsonify({"lines": []})

@app.route('/api/modules/status')
def get_all_module_status():
    """Get status of all modules"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(module_runner.get_all_status())

@app.route('/api/schedules', methods=['GET', 'POST'])
def handle_schedules():
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if request.method == 'GET':
        settings = read_settings()
        schedules = settings.get("module_schedules", []) if isinstance(settings, dict) else []
        states = schedule_runner.get_all_states()
        running_jobs = module_runner.get_running_jobs()
        output = []
        for sched in schedules:
            if not isinstance(sched, dict):
                continue
            state = states.get(sched.get("id")) if sched.get("id") else None
            serialized = _serialize_schedule(sched, state)
            if isinstance(serialized, dict):
                schedule_id = serialized.get("id") or sched.get("id")
                if schedule_id:
                    serialized["active_jobs"] = [
                        job for job in running_jobs
                        if job.get("schedule_id") == schedule_id
                    ]
            output.append(serialized)
        return jsonify(output)

    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json() or {}
    normalized = _normalize_schedule_payload(payload)
    if not normalized:
        return jsonify({"error": "invalid_schedule"}), 400
    schedule_id = payload.get("id") or f"sched_{uuid.uuid4().hex[:8]}"
    normalized["id"] = schedule_id
    settings = read_settings()
    schedules = settings.get("module_schedules", []) if isinstance(settings, dict) else []
    schedules.append(normalized)
    settings["module_schedules"] = schedules
    write_settings(settings)
    state = schedule_runner.get_schedule_state(schedule_id)
    return jsonify(_serialize_schedule(normalized, state))

@app.route('/api/schedules/<schedule_id>', methods=['PUT', 'DELETE'])
def handle_schedule(schedule_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    settings = read_settings()
    schedules = settings.get("module_schedules", []) if isinstance(settings, dict) else []
    target = None
    for sched in schedules:
        if isinstance(sched, dict) and sched.get("id") == schedule_id:
            target = sched
            break
    if not target:
        return jsonify({"error": "schedule_not_found"}), 404

    if request.method == 'DELETE':
        settings["module_schedules"] = [s for s in schedules if not (isinstance(s, dict) and s.get("id") == schedule_id)]
        write_settings(settings)
        return jsonify({"success": True})

    payload = request.get_json() or {}
    normalized = _normalize_schedule_payload(payload)
    if not normalized:
        return jsonify({"error": "invalid_schedule"}), 400
    normalized["id"] = schedule_id
    settings["module_schedules"] = [normalized if (isinstance(s, dict) and s.get("id") == schedule_id) else s for s in schedules]
    write_settings(settings)
    state = schedule_runner.get_schedule_state(schedule_id)
    return jsonify(_serialize_schedule(normalized, state))

@app.route('/api/schedules/<schedule_id>/run_now', methods=['POST'])
def run_schedule_now(schedule_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if user.get("role") not in ("admin", "operator"):
        return jsonify({"error": "forbidden"}), 403
    settings = read_settings()
    schedules = settings.get("module_schedules", []) if isinstance(settings, dict) else []
    target = None
    for sched in schedules:
        if isinstance(sched, dict) and sched.get("id") == schedule_id:
            target = sched
            break
    if not target:
        return jsonify({"error": "schedule_not_found"}), 404
    if not schedule_runner._resolve_sites(target):
        return jsonify({"error": "no_sites_selected"}), 400
    errors = _validate_schedule_config(target)
    if errors:
        return jsonify({"error": "invalid_schedule", "details": errors}), 400
    started = schedule_runner.run_now(target)
    return jsonify({"success": started})

@app.route('/api/users', methods=['GET', 'POST'])
def handle_users():
    user, err = _require_role("admin")
    if err:
        return err
    if request.method == 'GET':
        return jsonify([_sanitize_user(u) for u in _list_auth_users() if isinstance(u, dict)])

    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or "guest"
    allowed_sites = data.get("allowed_sites") or []
    disabled = bool(data.get("disabled", False))

    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400
    if role not in ("admin", "operator", "guest"):
        return jsonify({"error": "invalid_role"}), 400
    if not isinstance(allowed_sites, list):
        allowed_sites = []

    if _find_user(username):
        return jsonify({"error": "user_exists"}), 400

    _create_auth_user(username, password, role, allowed_sites, disabled)
    _audit("user.create", target=username, details={"role": role, "disabled": disabled})
    return jsonify({"success": True, "user": username})

@app.route('/api/users/<username>', methods=['PUT', 'DELETE'])
def handle_user(username):
    current_user, err = _require_role("admin")
    if err:
        return err
    target = _find_user(username)
    if not target:
        return jsonify({"error": "user_not_found"}), 404

    if request.method == 'DELETE':
        if target.get("role") == "admin" and not target.get("disabled") and _active_admin_count(exclude_username=username) < 1:
            return jsonify({"error": "last_admin_required"}), 400
        _delete_auth_user(username)
        _audit("user.delete", target=username)
        if current_user.get("username") == username:
            _revoke_session(session.get("auth_session_id"))
            session.pop("auth_session_id", None)
            session.pop("user", None)
        return jsonify({"success": True})

    data = request.get_json() or {}
    role = data.get("role")
    allowed_sites = data.get("allowed_sites")
    disabled = data.get("disabled")
    password = data.get("password")

    if role:
        if role not in ("admin", "operator", "guest"):
            return jsonify({"error": "invalid_role"}), 400
    effective_role = role or target.get("role")
    effective_disabled = bool(disabled) if disabled is not None else bool(target.get("disabled"))
    if target.get("role") == "admin" and not target.get("disabled"):
        losing_admin = effective_role != "admin" or effective_disabled
        if losing_admin and _active_admin_count(exclude_username=username) < 1:
            return jsonify({"error": "last_admin_required"}), 400

    updates = {}
    if role:
        updates["role"] = role
    if allowed_sites is not None:
        if not isinstance(allowed_sites, list):
            return jsonify({"error": "invalid_allowed_sites"}), 400
        updates["allowed_sites"] = allowed_sites
    if disabled is not None:
        updates["disabled"] = bool(disabled)
    if password:
        updates["password"] = password

    _update_auth_user(username, updates)
    if updates.get("disabled") or updates.get("password"):
        _revoke_user_sessions(username)
    _audit(
        "user.update",
        target=username,
        details={k: ("set" if k == "password" else v) for k, v in updates.items()}
    )
    return jsonify({"success": True})

@app.route('/api/auth/me')
def auth_me():
    auth = _get_auth_config()
    user = _get_effective_user()
    return jsonify({
        "auth_required": auth["enabled"] and len(auth["users"]) > 0,
        "authenticated": bool(user),
        "user": user.get("username") if user else None,
        "role": user.get("role") if user else None,
        "allowed_sites": _allowed_sites(user) if user else []
    })

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    auth = _get_auth_config()
    if not (auth["enabled"] and auth["users"]):
        return jsonify({"error": "auth_not_configured"}), 400
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = _find_user(username)
    if not user or user.get("disabled") or not check_password_hash(user.get("password_hash", ""), password):
        _record_login_attempt(username, False, "invalid_credentials")
        _audit("auth.login_failed", target=username, details={"reason": "invalid_credentials"}, actor=username or None)
        return jsonify({"error": "invalid_credentials"}), 401
    session.clear()
    _create_session_for_user(username)
    _record_login_attempt(username, True)
    _audit("auth.login", target=username, actor=username)
    return jsonify({
        "authenticated": True,
        "user": username,
        "role": user.get("role", "guest"),
        "allowed_sites": _allowed_sites(user)
    })

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    _revoke_session(session.get("auth_session_id"))
    _audit("auth.logout")
    session.clear()
    return jsonify({"authenticated": False})

@app.route('/api/auth/setup', methods=['POST'])
def auth_setup():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    enabled = bool(data.get("enabled", True))
    if not username or not password:
        return jsonify({"error": "username_and_password_required"}), 400

    settings = read_settings()
    auth = settings.get("auth")
    if not isinstance(auth, dict):
        auth = {}
    users = _list_auth_users()

    if users:
        current_user = _get_effective_user()
        if not _is_admin(current_user):
            return jsonify({"error": "auth_required"}), 403

    existing = _find_user(username)
    if not users or not existing:
        role = "admin" if not users else (data.get("role") or "guest")
    else:
        role = data.get("role") or (existing.get("role") if existing else "guest")
    if role not in ("admin", "operator", "guest"):
        return jsonify({"error": "invalid_role"}), 400
    allowed_sites = data.get("allowed_sites", []) if data.get("allowed_sites") is not None else (existing.get("allowed_sites") if existing else [])
    if not isinstance(allowed_sites, list):
        allowed_sites = []
    disabled = bool(data.get("disabled", False))

    if existing:
        if existing.get("role") == "admin" and role != "admin" and _active_admin_count(exclude_username=username) < 1:
            return jsonify({"error": "last_admin_required"}), 400
        _update_auth_user(username, {
            "role": role,
            "allowed_sites": allowed_sites,
            "disabled": disabled,
            "password": password,
        })
        _revoke_user_sessions(username)
    else:
        _create_auth_user(username, password, role, allowed_sites, disabled)
    auth["enabled"] = enabled
    settings["auth"] = auth
    write_settings(settings)
    session.clear()
    _create_session_for_user(username)
    _audit("auth.setup", target=username, details={"enabled": enabled, "role": role}, actor=username)

    return jsonify({
        "success": True,
        "user": username,
        "auth_required": enabled,
        "role": role,
        "allowed_sites": allowed_sites
    })

@app.route('/api/auth/change_password', methods=['POST'])
def auth_change_password():
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    data = request.get_json() or {}
    current = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    if not current or not new_password:
        return jsonify({"error": "passwords_required"}), 400
    if not check_password_hash(user.get("password_hash", ""), current):
        _record_login_attempt(user.get("username"), False, "bad_current_password")
        return jsonify({"error": "invalid_credentials"}), 401
    _update_auth_user(user.get("username"), {"password": new_password})
    current_session_id = session.get("auth_session_id")
    conn = _get_sqlite_conn()
    conn.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE username = ? AND session_id != ? AND revoked_at IS NULL",
        (datetime.now().isoformat(), user.get("username"), current_session_id)
    )
    conn.commit()
    conn.close()
    _audit("auth.change_password", target=user.get("username"))
    return jsonify({"success": True})

@app.route('/api/auth/config', methods=['PUT'])
def auth_config():
    user, err = _require_role("admin")
    if err:
        return err
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", True))
    settings = read_settings()
    auth = settings.get("auth", {})
    if not isinstance(auth, dict):
        auth = {}
    auth["enabled"] = enabled
    auth["users_migrated_to_sqlite"] = True
    settings["auth"] = auth
    write_settings(settings)
    _audit("auth.config", details={"enabled": enabled})
    return jsonify({"success": True, "enabled": enabled})


@app.route('/static/maps/<filename>')
def serve_map_file(filename):
    """Serve map HTML files directly"""
    try:
        # Clean filename to prevent directory traversal
        filename = os.path.basename(filename)
        user = _get_effective_user()
        if not user:
            return ("Unauthorized", 401)
        if not _is_admin(user):
            allowed = _allowed_sites(user)
            if "*" in allowed:
                allowed = []
            lowered = filename.lower()
            if allowed and not any(
                lowered.startswith(f"{_safe_site_name(site)}_") or lowered.startswith(f"{site.lower()}_")
                for site in allowed
            ):
                return ("Forbidden", 403)
        
        # Look for the file directly
        if os.path.exists(filename):
            return send_file(filename)
        
        # Try with maps/ prefix
        maps_path = f"maps/{filename}"
        if os.path.exists(maps_path):
            return send_file(maps_path)
        
        # Fallback to Roodan_map.html
        if os.path.exists("Roodan_map.html"):
            return send_file("Roodan_map.html")
        
        return jsonify({
            "error": f"Map file '{filename}' not found",
            "checked_paths": [filename, maps_path, "Roodan_map.html"]
        }), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/generated_maps/<filename>')
def serve_generated_map_file(filename):
    """Serve generated map HTML files directly"""
    try:
        # Clean filename to prevent directory traversal
        filename = os.path.basename(filename)
        user = _get_effective_user()
        if not user:
            return ("Unauthorized", 401)
        if not _is_admin(user):
            allowed = _allowed_sites(user)
            if "*" in allowed:
                allowed = []
            lowered = filename.lower()
            if allowed and not any(
                lowered.startswith(f"{_safe_site_name(site)}_") or lowered.startswith(f"{site.lower()}_")
                for site in allowed
            ):
                return ("Forbidden", 403)
        
        # Look for the file in generated_maps directory
        generated_path = f"generated_maps/{filename}"
        if os.path.exists(generated_path):
            return send_file(generated_path)
        
        return jsonify({
            "error": f"Generated map file '{filename}' not found",
            "checked_paths": [generated_path]
        }), 404
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/files')
def debug_files():
    """Debug endpoint to see available files"""
    user, err = _require_role("admin")
    if err:
        return err
    import glob
    
    files = []
    patterns = ["*.html", "maps/*.html", "*_map.html"]
    
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            files.append({
                "name": os.path.basename(filepath),
                "path": filepath,
                "size": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                "exists": os.path.exists(filepath)
            })
    
    return jsonify({
        "current_dir": os.getcwd(),
        "files": files,
        "has_roodan": os.path.exists("Roodan_map.html")
    })

@app.route('/api/map/<site_name>')
def get_map_for_site(site_name):
    """Get map URL for a specific site"""
    try:
        import glob
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        if not _can_read_site(user, site_name):
            return jsonify({"error": "forbidden"}), 403

        safe_site = _safe_site_name(site_name)

        def _find_latest(patterns):
            candidates = []
            for pattern in patterns:
                candidates.extend(glob.glob(pattern))
            if not candidates:
                return None
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0]

        visual = _find_latest([
            f"generated_maps/{site_name}_visual_map*.html",
            f"maps/{site_name}_visual_map*.html",
            f"{site_name}_visual_map*.html",
            f"generated_maps/{safe_site}_visual_map*.html",
            f"maps/{safe_site}_visual_map*.html",
            f"{safe_site}_visual_map*.html",
        ])
        text = _find_latest([
            f"generated_maps/{site_name}_text_map*.txt",
            f"generated_maps/{site_name}_map*.html",
            f"maps/{site_name}_map*.html",
            f"{site_name}_map*.html",
            f"generated_maps/{safe_site}_text_map*.txt",
            f"generated_maps/{safe_site}_map*.html",
            f"maps/{safe_site}_map*.html",
            f"{safe_site}_map*.html",
        ])

        path = visual or text

        if path and os.path.exists(path):
            normalized = path.replace("\\", "/")
            if normalized.startswith("generated_maps/"):
                url_path = f"/generated_maps/{os.path.basename(path)}"
            elif normalized.startswith("maps/"):
                url_path = f"/static/maps/{os.path.basename(path)}"
            else:
                url_path = f"/static/maps/{os.path.basename(path)}"
            
            return jsonify({
                "map_url": url_path,
                "site": site_name,
                "file_exists": True,
                "path": path,
                "is_visual": "_visual_" in path
            })
        
        # If no map found
        return jsonify({
            "error": f"No map found for site '{site_name}'",
            "map_url": None,
            "file_exists": False,
            "suggestion": "Run topology discovery first"
        }), 404
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "map_url": None
        }), 500



@app.route('/api/export')
def export_data():
    user, err = _require_role("admin")
    if err:
        return err

    devices = _read_sqlite_json("devices")
    settings = _read_sqlite_json("settings")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(SQLITE_DB_FILE):
            zf.write(SQLITE_DB_FILE, arcname="cmapp.sqlite3")
        if devices is not None:
            zf.writestr("devices.db", json.dumps(devices, indent=2))
        if settings is not None:
            zf.writestr("settings.json", json.dumps(settings, indent=2))
    buf.seek(0)

    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name='cmapp_export.zip'
    )


@app.route('/api/import', methods=['POST'])
def import_data():
    user, err = _require_role("admin")
    if err:
        return err
    if 'file' not in request.files:
        return jsonify({"error": "file_required"}), 400

    upload = request.files['file']
    if not upload.filename:
        return jsonify({"error": "file_required"}), 400

    try:
        data = upload.read()
        buf = io.BytesIO(data)
        with zipfile.ZipFile(buf, 'r') as zf:
            imported_sqlite = False
            devices_payload = None
            settings_payload = None
            for name in zf.namelist():
                if name == 'cmapp.sqlite3':
                    with zf.open(name) as src, open(SQLITE_DB_FILE, 'wb') as dst:
                        dst.write(src.read())
                    imported_sqlite = True
                    break
                if name == 'devices.db':
                    with zf.open(name) as src:
                        devices_payload = json.load(src)
                if name == 'settings.json':
                    with zf.open(name) as src:
                        settings_payload = json.load(src)
        if imported_sqlite:
            _migrate_auth_users_from_settings._done = False
            _migrate_auth_users_from_settings()
            return jsonify({"success": True})
        if devices_payload is not None:
            _write_sqlite_json("devices", devices_payload)
        if settings_payload is not None:
            _write_sqlite_json("settings", settings_payload)
        if _read_sqlite_json("settings") is None:
            init_settings()
        if _read_sqlite_json("devices") is None:
            init_database()
        _migrate_auth_users_from_settings._done = False
        _migrate_auth_users_from_settings()
        return jsonify({"success": True})
    except zipfile.BadZipFile:
        return jsonify({"error": "invalid_zip"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings', methods=['GET', 'PUT'])
def handle_settings():
    """Manage settings"""
    if request.method == 'GET':
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        settings = read_settings()
        auth = settings.get("auth", {})
        settings["auth"] = {
            "enabled": bool(auth.get("enabled", False))
        }
        if not _is_admin(user):
            settings = {k: v for k, v in settings.items() if k not in ("auth", "module_credentials", "agents")}
        return jsonify(settings)
    
    elif request.method == 'PUT':
        user, err = _require_role("admin")
        if err:
            return err
        new_settings = request.json
        current_settings = read_settings()
        if isinstance(new_settings, dict):
            if "auth" in new_settings:
                auth = current_settings.get("auth", {})
                if not isinstance(auth, dict):
                    auth = {}
                auth["enabled"] = bool(new_settings.get("auth", {}).get("enabled", auth.get("enabled", False)))
                if "users" not in auth:
                    auth["users"] = []
                current_settings["auth"] = auth
                new_settings = {k: v for k, v in new_settings.items() if k != "auth"}
            current_settings.update(new_settings)
        write_settings(current_settings)
        module_runner.set_max_concurrent(current_settings.get("module_max_concurrent", 2))
        auth = current_settings.get("auth", {})
        current_settings["auth"] = {"enabled": bool(auth.get("enabled", False))}
        return jsonify(current_settings)

@app.route('/api/agents', methods=['GET', 'POST'])
def handle_agents():
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if request.method == 'GET':
        if not _is_admin(user):
            return jsonify({"error": "forbidden"}), 403
        settings = read_settings()
        agents = settings.get("agents", []) if isinstance(settings, dict) else []
        online_minutes = int(settings.get("agent_online_minutes") or 5)
        cutoff = datetime.now() - timedelta(minutes=online_minutes)
        enriched = []
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            last_seen = agent.get("last_seen")
            status = "offline"
            if last_seen:
                try:
                    dt = datetime.fromisoformat(last_seen)
                    if dt >= cutoff:
                        status = "online"
                except Exception:
                    status = "offline"
            entry = copy.deepcopy(agent)
            entry["agent_status"] = status
            enriched.append(entry)
        return jsonify(enriched)

    # POST create
    user, err = _require_role("admin")
    if err:
        return err
    payload = request.get_json() or {}
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    normalized = _normalize_agent_payload(payload)
    if not normalized:
        return jsonify({"error": "invalid_agent"}), 400
    agents.append(normalized)
    settings["agents"] = agents
    write_settings(settings)
    _write_agent_config_files(normalized, settings)
    return jsonify(normalized)


@app.route('/api/agents/<agent_id>', methods=['PUT', 'DELETE'])
def handle_agent(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    user, err = _require_role("admin")
    if err:
        return err
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    target = None
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            target = agent
            break
    if not target:
        return jsonify({"error": "agent_not_found"}), 404

    if request.method == 'DELETE':
        settings["agents"] = [a for a in agents if not (isinstance(a, dict) and a.get("id") == agent_id)]
        write_settings(settings)
        try:
            os.remove(os.path.join(AGENT_CONFIG_DIR, f"{agent_id}.json"))
        except OSError:
            pass
        try:
            os.remove(os.path.join(AGENT_CONFIG_DIR, f"{agent_id}.txt"))
        except OSError:
            pass
        return jsonify({"success": True})

    payload = request.get_json() or {}
    normalized = _normalize_agent_payload(payload, existing=target)
    if not normalized:
        return jsonify({"error": "invalid_agent"}), 400
    settings["agents"] = [normalized if (isinstance(a, dict) and a.get("id") == agent_id) else a for a in agents]
    write_settings(settings)
    _write_agent_config_files(normalized, settings)
    return jsonify(normalized)


@app.route('/api/agents/<agent_id>/trigger', methods=['POST'])
def trigger_agent(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    user, err = _require_role("admin")
    if err:
        return err
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    updated = False
    payload = request.get_json(silent=True) or {}
    module_id = payload.get("module_id") if isinstance(payload, dict) else None
    targets = payload.get("targets") if isinstance(payload, dict) else None
    params = payload.get("params") if isinstance(payload, dict) else None
    run_scan = bool(payload.get("run_scan")) if isinstance(payload, dict) else False
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            if not module_id:
                agent["run_now"] = True
            elif run_scan:
                agent["run_now"] = True
            if module_id:
                queued = agent.get("queued_modules") if isinstance(agent.get("queued_modules"), list) else []
                job = {"id": module_id}
                if isinstance(targets, list):
                    job["targets"] = targets
                if isinstance(params, dict):
                    job["params"] = params
                queued.append(job)
                agent["queued_modules"] = queued
            updated = True
            break
    if not updated:
        return jsonify({"error": "agent_not_found"}), 404
    settings["agents"] = agents
    write_settings(settings)
    if updated:
        _write_agent_config_files(agent, settings)
    return jsonify({"success": True})


@app.route('/api/agents/<agent_id>/reset_identity', methods=['POST'])
def reset_agent_identity(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    user, err = _require_role("admin")
    if err:
        return err
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    updated = False
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            agent["device_name"] = ""
            agent["device_ip"] = ""
            agent["device_mac"] = ""
            updated = True
            break
    if not updated:
        return jsonify({"error": "agent_not_found"}), 404
    settings["agents"] = agents
    write_settings(settings)
    return jsonify({"success": True})


@app.route('/api/agents/<agent_id>/clear_queue', methods=['POST'])
def clear_agent_queue(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    user, err = _require_role("admin")
    if err:
        return err
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    updated = False
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            agent["queued_modules"] = []
            agent["run_now"] = False
            updated = True
            break
    if not updated:
        return jsonify({"error": "agent_not_found"}), 404
    settings["agents"] = agents
    write_settings(settings)
    return jsonify({"success": True})


@app.route('/api/agents/<agent_id>/identity', methods=['GET'])
def get_agent_identity(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == agent_id), None)
    if not agent:
        return jsonify({"error": "agent_not_found"}), 404
    return jsonify({
        "device_name": agent.get("device_name") or "",
        "device_ip": agent.get("device_ip") or "",
        "device_mac": agent.get("device_mac") or "",
        "last_seen": agent.get("last_seen"),
        "last_scan_at": agent.get("last_scan_at")
    })


@app.route('/api/agents/<agent_id>/config', methods=['GET'])
def download_agent_config(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    fmt = (request.args.get("format") or "json").lower()
    path = os.path.join(AGENT_CONFIG_DIR, f"{agent_id}.json" if fmt != "txt" else f"{agent_id}.txt")
    if not os.path.exists(path):
        return jsonify({"error": "config_not_found"}), 404
    return send_file(path, as_attachment=True)

@app.route('/api/agents/<agent_id>/package', methods=['GET'])
def download_agent_package(agent_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == agent_id), None)
    if not agent:
        return jsonify({"error": "agent_not_found"}), 404

    exe_candidates = [
        os.path.join(BASE_DIR, "agent", "dist", "cmapp-agent.exe"),
        os.path.join(BASE_DIR, "agent", "cmapp-agent.exe"),
    ]
    exe_path = next((p for p in exe_candidates if os.path.exists(p)), None)
    if not exe_path:
        return jsonify({"error": "agent_exe_not_found"}), 404

    _write_agent_config_files(agent, settings)
    config_path = os.path.join(AGENT_CONFIG_DIR, f"{agent_id}.json")
    if not os.path.exists(config_path):
        return jsonify({"error": "config_not_found"}), 404

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe_path, arcname="cmapp-agent.exe")
        zf.write(config_path, arcname="agent_config.json")
    mem.seek(0)
    safe_site = (agent.get("site") or "agent").replace("/", "_").replace("\\", "_").replace(":", "_")
    return send_file(mem, mimetype="application/zip", as_attachment=True,
                     download_name=f"{safe_site}_agent_package.zip")


@app.route('/api/agents/agent_exe', methods=['GET'])
def download_agent_exe():
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _is_admin(user):
        return jsonify({"error": "forbidden"}), 403
    candidates = [
        os.path.join(BASE_DIR, "agent", "dist", "cmapp-agent.exe"),
        os.path.join(BASE_DIR, "agent", "cmapp-agent.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return send_file(path, as_attachment=True)
    return jsonify({"error": "agent_exe_not_found"}), 404


@app.route('/api/agent/config/<agent_id>', methods=['GET'])
def agent_poll_config(agent_id):
    token = request.headers.get("X-Agent-Token") or request.args.get("token") or ""
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == agent_id), None)
    if not agent:
        return jsonify({"error": "agent_not_found"}), 404
    if token != (agent.get("token") or ""):
        return jsonify({"error": "invalid_token"}), 401
    base_url = (settings.get("agent_server_url") or "http://127.0.0.1:5000")
    if agent.get("server_host"):
        base_url = agent.get("server_host")
    response = {
        "agent_id": agent.get("id"),
        "name": agent.get("name"),
        "site": agent.get("site"),
        "target_range": agent.get("target_range"),
        "target_ranges": agent.get("target_ranges") or [],
        "enabled": agent.get("enabled", True),
        "interval_min": agent.get("interval_min", 0),
        "allow_interval": agent.get("allow_interval", True),
        "allow_on_demand": agent.get("allow_on_demand", True),
        "trust_mode": agent.get("trust_mode", "augment"),
        "ip_scan_min": agent.get("ip_scan_min", 10),
        "ping_min": agent.get("ping_min", 2),
        "modules": agent.get("modules", {}),
        "credentials": agent.get("credentials", {}),
        "server_url": base_url,
        "run_now": bool(agent.get("run_now")),
        "queued_modules": agent.get("queued_modules", []),
        "retention_days": AGENT_SCAN_RETENTION_DAYS
    }
    return jsonify(response)


@app.route('/api/agent/report', methods=['POST'])
def agent_report():
    payload = request.get_json() or {}
    agent_id = (payload.get("agent_id") or "").strip()
    token = request.headers.get("X-Agent-Token") or payload.get("token") or ""
    if not agent_id:
        return jsonify({"error": "missing_agent_id"}), 400
    settings = read_settings()
    agents = settings.get("agents", []) if isinstance(settings, dict) else []
    agent = next((a for a in agents if isinstance(a, dict) and a.get("id") == agent_id), None)
    if not agent:
        return jsonify({"error": "agent_not_found"}), 404
    if token != (agent.get("token") or ""):
        return jsonify({"error": "invalid_token"}), 401
    if not agent.get("enabled", True):
        return jsonify({"error": "agent_disabled"}), 403

    site = (payload.get("site") or agent.get("site") or "").strip()
    scan_time = (payload.get("scan_time") or datetime.now().isoformat())
    devices = payload.get("devices") or []
    agent_device = payload.get("agent_device") or {}
    agent_state = (payload.get("agent_state") or "").strip()
    network_ranges = payload.get("network_ranges") or []
    mode = (payload.get("mode") or "full_sync").strip().lower()
    trust_mode = (payload.get("trust_mode") or agent.get("trust_mode") or "augment").strip().lower()
    module_results = payload.get("module_results") or {}
    if not isinstance(devices, list):
        return jsonify({"error": "invalid_devices"}), 400

    data = read_database() or {}
    device_list = data.get("devices", []) if isinstance(data, dict) else []
    now = datetime.now().isoformat()

    def normalize_mac_value(value):
        if not value:
            return ""
        return normalize_mac(str(value))

    # Build lookup by site
    by_ip = {}
    by_mac = {}
    by_switch_name = {}
    for dev in device_list:
        if dev.get("site") != site:
            continue
        ip = (dev.get("ip") or "").strip()
        if ip:
            by_ip[ip] = dev
        mac = normalize_mac_value(dev.get("mac"))
        if mac:
            by_mac[mac] = dev
        if (dev.get("type") or "").lower() == "switch":
            name = (dev.get("name") or "").strip()
            if name:
                by_switch_name[name] = dev

    created = 0
    updated = 0
    def _create_switch_stub(name: str, ip: str | None = None, mac: str | None = None):
        nonlocal created
        if not name:
            return None
        dev = {
            "id": f"dev_{uuid.uuid4().hex[:8]}",
            "site": site,
            "name": name,
            "ip": ip or "",
            "mac": mac or "",
            "type": "switch",
            "discovered_by": f"agent:{agent_id}",
            "discovered_at": now,
            "last_seen": now,
            "last_modified": now,
            "status": "unknown",
            "connections": []
        }
        device_list.append(dev)
        if dev["ip"]:
            by_ip[dev["ip"]] = dev
        if dev["mac"]:
            by_mac[dev["mac"]] = dev
        by_switch_name[dev["name"]] = dev
        created += 1
        return dev

    def is_mac_like(value: str) -> bool:
        if not value:
            return False
        v = value.replace("-", ":").replace(".", "").upper()
        if len(v) == 12:
            return True
        return v.count(":") == 5

    def can_update_name(dev, name):
        if not name:
            return False
        dev_type = (dev.get("type") or "").lower()
        if dev_type and dev_type not in ("unknown", "pc", "pda"):
            return False
        current = (dev.get("name") or "").strip()
        ip_val = (dev.get("ip") or "").strip()
        if not current:
            return True
        if ip_val and current == ip_val:
            return True
        if is_mac_like(current):
            return True
        return False

    def apply_device(dev, ip, mac, name, matched_by_mac=False, scan_name=""):
        nonlocal updated
        if matched_by_mac:
            if scan_name:
                dev["name"] = scan_name
            elif name:
                dev["name"] = name
            elif mac:
                dev["name"] = mac
        elif can_update_name(dev, name):
            dev["name"] = name
        src_type = (item.get("type") or "").strip()
        if src_type == "switch" and name and (dev.get("name") or "") != name:
            dev["name"] = name
        if src_type and (not dev.get("type") or dev.get("type") == "unknown"):
            dev["type"] = src_type
        for field in ("parent_switch_name", "parent_switch_ip", "parent_switch_port", "parent_switch_platform", "vlan"):
            if item.get(field):
                dev[field] = item.get(field)
        if isinstance(item.get("connections"), list) and item.get("connections"):
            resolved = []
            for conn in item.get("connections"):
                if not isinstance(conn, dict):
                    continue
                remote_id = conn.get("remote_device")
                remote_ip = (conn.get("remote_ip") or "").strip()
                remote_mac = normalize_mac_value(conn.get("remote_mac"))
                remote_name = (conn.get("remote_name") or "").strip()
                remote_dev = None
                if remote_ip and remote_ip in by_ip:
                    remote_dev = by_ip.get(remote_ip)
                if remote_dev is None and remote_mac:
                    remote_dev = by_mac.get(remote_mac)
                if remote_dev is None and remote_name and (conn.get("protocol") or "").lower() == "cdp":
                    remote_dev = by_switch_name.get(remote_name)
                    if remote_dev is None:
                        remote_dev = _create_switch_stub(remote_name, remote_ip or None, remote_mac or None)
                if remote_dev:
                    remote_id = remote_dev.get("id")
                    conn["remote_device"] = remote_id
                    conn["remote_name"] = remote_dev.get("name") or remote_name
                    conn["remote_ip"] = remote_dev.get("ip") or remote_ip
                    if remote_dev.get("mac"):
                        conn["remote_mac"] = remote_dev.get("mac")
                resolved.append(conn)
            dev["connections"] = resolved
        if item.get("platform"):
            dev["platform"] = item.get("platform")
        if item.get("vendor"):
            dev["vendor"] = item.get("vendor")
        if ip:
            dev["ip"] = ip
            by_ip[ip] = dev
        if mac:
            dev["mac"] = mac
            by_mac[mac] = dev
        dev["last_seen"] = now
        dev["last_modified"] = now
        updated += 1

    if mode == "full_sync" and trust_mode == "replace":
        # Replace site inventory, but keep IDs where possible
        existing_for_site = [d for d in device_list if d.get("site") == site]
        existing_lookup = {}
        for d in existing_for_site:
            key_ip = (d.get("ip") or "").strip()
            key_mac = normalize_mac_value(d.get("mac"))
            if key_ip:
                existing_lookup[f"ip:{key_ip}"] = d
            if key_mac:
                existing_lookup[f"mac:{key_mac}"] = d
            if (d.get("type") or "").lower() == "switch":
                key_name = (d.get("name") or "").strip()
                if key_name:
                    existing_lookup[f"switch:{key_name}"] = d

        kept = [d for d in device_list if d.get("site") != site]
        for item in devices:
            if not isinstance(item, dict):
                continue
            ip = (item.get("ip") or "").strip()
            mac = normalize_mac_value(item.get("mac"))
            scan_name = (item.get("hostname") or "").strip()
            name = (scan_name or item.get("name") or ip or mac or "").strip()
            if not ip and not mac:
                if (item.get("type") or "").lower() != "switch" or not name:
                    continue
            dev = existing_lookup.get(f"ip:{ip}") or existing_lookup.get(f"mac:{mac}")
            if dev is None and (item.get("type") or "").lower() == "switch" and name:
                dev = existing_lookup.get(f"switch:{name}")
            if dev is None:
                dev = {
                    "id": f"dev_{uuid.uuid4().hex[:8]}",
                    "site": site,
                    "name": name or ip or mac,
                    "ip": ip,
                    "mac": mac,
                    "type": (item.get("type") or "unknown"),
                    "discovered_by": f"agent:{agent_id}",
                    "discovered_at": now,
                    "last_seen": now,
                    "last_modified": now,
                    "status": "unknown",
                    "parent_switch_name": item.get("parent_switch_name"),
                    "parent_switch_ip": item.get("parent_switch_ip"),
                    "parent_switch_port": item.get("parent_switch_port"),
                    "parent_switch_platform": item.get("parent_switch_platform"),
                    "vlan": item.get("vlan"),
                    "connections": item.get("connections") if isinstance(item.get("connections"), list) else []
                }
                if item.get("platform"):
                    dev["platform"] = item.get("platform")
                if item.get("vendor"):
                    dev["vendor"] = item.get("vendor")
                created += 1
            else:
                apply_device(dev, ip, mac, name, matched_by_mac=bool(existing_lookup.get(f"mac:{mac}")), scan_name=scan_name)
            kept.append(dev)
        device_list = kept
    else:
        for item in devices:
            if not isinstance(item, dict):
                continue
            ip = (item.get("ip") or "").strip()
            mac = normalize_mac_value(item.get("mac"))
            scan_name = (item.get("hostname") or "").strip()
            name = (scan_name or item.get("name") or ip or mac or "").strip()
            if not ip and not mac:
                if (item.get("type") or "").lower() != "switch" or not name:
                    continue
            matched_by_mac = False
            dev = by_ip.get(ip)
            if not dev and mac:
                dev = by_mac.get(mac)
                matched_by_mac = dev is not None
            if dev is None and (item.get("type") or "").lower() == "switch" and name:
                dev = by_switch_name.get(name)
            if dev is None:
                dev = {
                    "id": f"dev_{uuid.uuid4().hex[:8]}",
                    "site": site,
                    "name": name or ip or mac,
                    "ip": ip,
                    "mac": mac,
                    "type": (item.get("type") or "unknown"),
                    "discovered_by": f"agent:{agent_id}",
                    "discovered_at": now,
                    "last_seen": now,
                    "last_modified": now,
                    "status": "unknown",
                    "parent_switch_name": item.get("parent_switch_name"),
                    "parent_switch_ip": item.get("parent_switch_ip"),
                    "parent_switch_port": item.get("parent_switch_port"),
                    "parent_switch_platform": item.get("parent_switch_platform"),
                    "vlan": item.get("vlan"),
                    "connections": item.get("connections") if isinstance(item.get("connections"), list) else []
                }
                if item.get("platform"):
                    dev["platform"] = item.get("platform")
                if item.get("vendor"):
                    dev["vendor"] = item.get("vendor")
                device_list.append(dev)
                if ip:
                    by_ip[ip] = dev
                if mac:
                    by_mac[mac] = dev
                created += 1
            else:
                apply_device(dev, ip, mac, name, matched_by_mac=matched_by_mac, scan_name=scan_name)

    if isinstance(data, dict):
        data["devices"] = device_list
        data.setdefault("meta", {})["last_modified"] = now
        write_database(data)

    # Server only keeps latest in DB; agent stores local scan history
    scan_paths = {}

    # update agent status
    for a in agents:
        if isinstance(a, dict) and a.get("id") == agent_id:
            # bind / validate agent identity
            reported_name = (agent_device.get("name") or "").strip()
            reported_ip = (agent_device.get("ip") or "").strip()
            reported_mac = normalize_mac_value(agent_device.get("mac") or "")
            if a.get("device_name") or a.get("device_ip") or a.get("device_mac"):
                if a.get("device_name") and reported_name and a.get("device_name") != reported_name:
                    a["run_now"] = False
                    a["last_result"] = "identity_mismatch"
                    a["last_result_at"] = datetime.now().isoformat()
                    write_settings(settings)
                    return jsonify({"error": "agent_identity_mismatch", "field": "name"}), 403
                if a.get("device_ip") and reported_ip and a.get("device_ip") != reported_ip:
                    a["run_now"] = False
                    a["last_result"] = "identity_mismatch"
                    a["last_result_at"] = datetime.now().isoformat()
                    write_settings(settings)
                    return jsonify({"error": "agent_identity_mismatch", "field": "ip"}), 403
                if a.get("device_mac") and reported_mac and a.get("device_mac") != reported_mac:
                    a["run_now"] = False
                    a["last_result"] = "identity_mismatch"
                    a["last_result_at"] = datetime.now().isoformat()
                    write_settings(settings)
                    return jsonify({"error": "agent_identity_mismatch", "field": "mac"}), 403
            else:
                a["device_name"] = reported_name
                a["device_ip"] = reported_ip
                a["device_mac"] = reported_mac
            a["last_seen"] = datetime.now().isoformat()
            a["last_scan_at"] = scan_time
            a["run_now"] = False
            a["last_result"] = "success"
            a["last_result_at"] = datetime.now().isoformat()
            if agent_state:
                a["last_state"] = agent_state
            if module_results:
                a["last_state"] = f"module:{','.join(module_results.keys())}"
                a["queued_modules"] = []
            if isinstance(network_ranges, list):
                a["network_ranges"] = [r for r in network_ranges if isinstance(r, str)]
    settings["agents"] = agents
    write_settings(settings)
    _write_agent_config_files(agent, settings)

    return jsonify({
        "status": "success",
        "created": created,
        "updated": updated,
        "scan_files": scan_paths
    })

@app.route('/api/oui_ranges', methods=['GET', 'PUT'])
def handle_oui_ranges():
    user, err = _require_role("admin")
    if err:
        return err
    if request.method == 'GET':
        return jsonify({"content": read_oui_ranges()})
    data = request.json or {}
    content = data.get("content", "")
    write_oui_ranges(content)
    return jsonify({"success": True})


@app.route('/api/oui_device_types', methods=['GET', 'PUT'])
def handle_oui_device_types():
    user, err = _require_role("admin")
    if err:
        return err
    if request.method == 'GET':
        return jsonify({"content": read_oui_device_types()})
    data = request.json or {}
    content = data.get("content", "")
    write_oui_device_types(content)
    return jsonify({"success": True})

@app.route('/api/stats')
def get_stats():
    """Get statistics"""
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    data = read_database()

    devices = _filter_devices_for_user(data.get("devices", []), user)
    sites = _filter_sites_for_user(data.get("sites", []), user)
    settings = read_settings() or {}
    stale_days = int(settings.get("stale_scan_days") or 7)

    unknown_devices = len([d for d in devices if (d.get("type") or "").lower() in ("", "unknown")])

    # Per-site device grouping
    devices_by_site = {}
    for d in devices:
        site = d.get("site") or ""
        devices_by_site.setdefault(site, []).append(d)

    # Sites with no router identified
    sites_no_router = []
    for site in sites:
        name = site.get("name") or ""
        devs = devices_by_site.get(name, [])
        has_router = any((d.get("type") or "").lower() == "router" for d in devs)
        if not has_router:
            sites_no_router.append(name)

    # Sites with stale scans
    stale_sites = []
    cutoff = datetime.now() - timedelta(days=stale_days)
    for site in sites:
        last_scan = site.get("last_scan")
        if not last_scan:
            stale_sites.append(site.get("name") or "")
            continue
        try:
            dt = datetime.fromisoformat(last_scan)
        except Exception:
            stale_sites.append(site.get("name") or "")
            continue
        if dt < cutoff:
            stale_sites.append(site.get("name") or "")

    # Sites with highest unknown rate
    unknown_rate = []
    for site in sites:
        name = site.get("name") or ""
        devs = devices_by_site.get(name, [])
        total = len(devs)
        if total == 0:
            continue
        unk = len([d for d in devs if (d.get("type") or "").lower() in ("", "unknown")])
        rate = (unk / total) * 100
        unknown_rate.append({"site": name, "rate": round(rate, 1), "unknown": unk, "total": total})
    unknown_rate.sort(key=lambda x: x["rate"], reverse=True)

    # Uncompleted maps (no generated visual map)
    def _site_has_visual_map(site_name: str) -> bool:
        if not site_name:
            return False
        safe_site = site_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        patterns = [
            os.path.join(GENERATED_MAPS_DIR, f"{site_name}_visual_map*.html"),
            os.path.join(GENERATED_MAPS_DIR, f"{safe_site}_visual_map*.html"),
            os.path.join("maps", f"{site_name}_visual_map*.html"),
            os.path.join("maps", f"{safe_site}_visual_map*.html"),
            f"{site_name}_visual_map*.html",
            f"{safe_site}_visual_map*.html"
        ]
        for pattern in patterns:
            if glob.glob(pattern):
                return True
        return False

    uncompleted_maps = []
    for site in sites:
        name = site.get("name") or ""
        if not _site_has_visual_map(name):
            uncompleted_maps.append(name)

    # Catched IPs
    catched_by_site = {}
    for d in devices:
        name = (d.get("name") or "")
        if name.lower().startswith("catched-"):
            site = d.get("site") or ""
            catched_by_site[site] = catched_by_site.get(site, 0) + 1
    catched_sites = [{"site": k, "count": v} for k, v in catched_by_site.items()]
    catched_sites.sort(key=lambda x: x["count"], reverse=True)

    # Sites with the most PC devices missing domain lookup data
    pc_no_domain_by_site = {}
    for d in devices:
        if (d.get("type") or "").lower() != "pc":
            continue
        if (d.get("domain") or "").strip():
            continue
        site = d.get("site") or ""
        pc_no_domain_by_site[site] = pc_no_domain_by_site.get(site, 0) + 1
    pc_no_domain_sites = [{"site": k, "count": v} for k, v in pc_no_domain_by_site.items()]
    pc_no_domain_sites.sort(key=lambda x: x["count"], reverse=True)

    stats = {
        "total_sites": len(sites),
        "total_devices": len(devices),
        "unknown_devices": unknown_devices,
        "last_modified": data.get("meta", {}).get("last_modified", "Never"),
        "stale_scan_days": stale_days,
        "sites_no_router": sites_no_router,
        "stale_sites": stale_sites,
        "unknown_rate_sites": unknown_rate[:5],
        "uncompleted_maps": uncompleted_maps,
        "catched_sites": catched_sites[:5],
        "catched_total": sum(catched_by_site.values()),
        "pc_no_domain_sites": pc_no_domain_sites[:10],
        "pc_no_domain_total": sum(pc_no_domain_by_site.values())
    }

    return jsonify(stats)

# ==================== MAIN ====================

if __name__ == '__main__':
    # Import sys for stderr printing
    import sys
    
    # Check for required packages
    try:
        import portalocker
    except ImportError:
        print("=" * 60)
        print("MISSING REQUIRED PACKAGE: portalocker")
        print("Please install it using: pip install portalocker")
        print("=" * 60)
        exit(1)
    
    # Initialize files
    init_database()
    init_settings()
    module_runner.set_max_concurrent(read_settings().get("module_max_concurrent", 2))
    
    # Create modules directory if it doesn't exist
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
    
    # Create example modules if they don't exist
    example_modules = ['add_device_manual', 'cdp_discovery', 'view_map', 'enforce_oui_table', 'ubiquiti_cdp_reader']
    
    for module_name in example_modules:
        module_dir = os.path.join(MODULES_DIR, module_name)
        if not os.path.exists(module_dir):
            os.makedirs(module_dir, exist_ok=True)
            print(f"Created module directory: {module_dir}", file=sys.stderr)
    
    print("=" * 60)
    print("NETWORK DISCOVERY PLATFORM (HTTPS)")
    print("=" * 60)
    print(f"Dashboard URL: https://localhost:8443")
    print(f"API Base URL: https://localhost:8443/api/")
    print("\nAvailable API endpoints:")
    print("  GET  /api/database          - Get database")
    print("  GET  /api/sites             - List sites")
    print("  POST /api/sites             - Add site")
    print("  GET  /api/devices           - List devices")
    print("  GET  /api/modules           - List modules")
    print("  POST /api/modules/{id}/run  - Run module")
    print("  GET  /api/stats             - Get stats")
    print("  GET  /api/settings          - Get settings")
    print("  PUT  /api/settings          - Update settings")
    print("\n⚠️  WARNING: Using self-signed SSL certificate")
    print("   Browsers will show security warnings - accept them for development")
    print("\nDebug logs will appear below...")
    print("=" * 60)
    
    if __name__ == "__main__":
        use_ssl = os.getenv("USE_SSL", "0") == "1"

        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "5000"))

        ssl_ctx = "adhoc" if use_ssl else None

        print(f"Starting server on {'https' if use_ssl else 'http'}://{host}:{port}")
        app.run(debug=True, host=host, port=port, use_reloader=False)
