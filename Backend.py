#!/usr/bin/env python3
"""
Network Discovery Platform - COMPLETE WORKING BACKEND
"""

from flask import Flask, render_template, jsonify, request, send_file, session, g
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
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
MONITORING_FILE = os.path.join(BASE_DIR, "monitoring.db")
SQLITE_DB_FILE = os.path.join(BASE_DIR, "cmapp.sqlite3")
MONITORING_INTERVAL_SEC = 5
MONITORING_LOCK = threading.Lock()
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_RETENTION_DAYS = 60
CORRUPT_DIR = os.path.join(BASE_DIR, "corrupt_backups")
CORRUPT_RETENTION_DAYS = 7
MODULE_LOG_RETENTION_DAYS = 0
MODULE_CONFIG_RETENTION_DAYS = 0

MODULES_DIR = os.path.join(BASE_DIR, "Modules")
TEMPLATES_DIR = os.path.join(BASE_DIR, "Templates")
STATIC_DIR = os.path.join(BASE_DIR, "Static")
OUI_RANGES_FILE = os.path.join(MODULES_DIR, "mikrotik_mac_discovery", "oui_ranges.txt")
OUI_DEVICE_TYPES_FILE = os.path.join(MODULES_DIR, "enforce_oui_table", "oui_device_types.txt")

DEFAULT_MONITORING_LAYOUT = {
    "top": 90,
    "bottom": 90,
    "left": 120,
    "right": 120,
    "labels": {
        "top": "Top",
        "left": "Left",
        "center": "Center",
        "right": "Right",
        "bottom": "Bottom"
    }
}

GENERATED_MAPS_DIR = os.path.join(BASE_DIR, "generated_maps")

# Ensure required dirs exist
os.makedirs(GENERATED_MAPS_DIR, exist_ok=True)



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
    legacy_monitoring = _read_json_file(MONITORING_FILE, default=None)
    if legacy_monitoring is not None:
        _write_sqlite_json("monitoring", legacy_monitoring)
    legacy_settings = _read_json_file(SETTINGS_FILE, default=None)
    if legacy_settings is not None:
        _write_sqlite_json("settings", legacy_settings)

def init_database():
    """Initialize empty database if it doesn't exist"""
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
    _write_json_file(DATABASE_FILE, data)

DEFAULT_SETTINGS = {
    "default_site": "",
    "backup_path": "./backups",
    "default_scan_depth": 3,
    "auto_refresh": False,
    "refresh_interval": 30,
    "module_credentials": {},
    "module_schedules": [],
    "auth": {
        "enabled": False,
        "users": []
    },
}

def init_settings():
    """Initialize default settings"""
    settings = DEFAULT_SETTINGS.copy()
    _write_sqlite_json("settings", settings)
    _write_json_file(SETTINGS_FILE, settings)

DEFAULT_MONITORING_RULES_LIST = [
    {"id": "loss", "type": "loss", "threshold": 100, "enabled": True},
    {"id": "latency", "type": "latency", "threshold": 500, "enabled": True},
]

def init_monitoring():
    data = {
        "version": "1.0",
        "meta": {
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        },
        "sites": {}
    }
    _write_sqlite_json("monitoring", data)
    _write_json_file(MONITORING_FILE, data)
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR, exist_ok=True)

def read_monitoring():
    data = _read_sqlite_json("monitoring")
    if data is not None:
        return data
    legacy = _read_json_file(MONITORING_FILE, default=None)
    if legacy is not None:
        _write_sqlite_json("monitoring", legacy)
        return legacy
    init_monitoring()
    return _read_sqlite_json("monitoring") or {"version": "1.0", "meta": {"last_modified": datetime.now().isoformat()}, "sites": {}}

def write_monitoring(data):
    data.setdefault("meta", {})
    data["meta"]["last_modified"] = datetime.now().isoformat()
    _write_sqlite_json("monitoring", data)
    _write_json_file(MONITORING_FILE, data)

def _safe_log_name(site_name):
    safe = _safe_site_name(site_name)
    return safe or "site"

def _cleanup_logs():
    cutoff = time.time() - (LOG_RETENTION_DAYS * 86400)
    try:
        for name in os.listdir(LOGS_DIR):
            path = os.path.join(LOGS_DIR, name)
            if not os.path.isfile(path):
                continue
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError:
        pass

def _cleanup_module_logs():
    try:
        for name in os.listdir(BASE_DIR):
            if not name.startswith("module_log_") or not name.endswith(".txt"):
                continue
            path = os.path.join(BASE_DIR, name)
            if not os.path.isfile(path):
                continue
            os.remove(path)
    except OSError:
        pass

def _cleanup_module_configs():
    try:
        for name in os.listdir(BASE_DIR):
            if not name.startswith("module_config_") or not name.endswith(".json"):
                continue
            path = os.path.join(BASE_DIR, name)
            if not os.path.isfile(path):
                continue
            os.remove(path)
    except OSError:
        pass

def log_event(site_name, message):
    init_monitoring()
    safe = _safe_log_name(site_name)
    log_path = os.path.join(LOGS_DIR, f"{safe}.log")
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"[{timestamp}] {message}\n"
    try:
        with portalocker.Lock(log_path, 'a', timeout=2, encoding='utf-8') as f:
            f.write(line)
    except portalocker.exceptions.LockException:
        pass
    _cleanup_logs()

def _get_monitoring_site(data, site_name):
    sites = data.setdefault("sites", {})
    entry = sites.get(site_name)
    if not isinstance(entry, dict):
        entry = {}
        sites[site_name] = entry
    entry.setdefault("devices", {})
    if "rules" not in entry:
        entry["rules"] = [rule.copy() for rule in DEFAULT_MONITORING_RULES_LIST]
    elif isinstance(entry.get("rules"), dict):
        legacy = entry.get("rules", {})
        entry["rules"] = [
            {"id": "loss", "type": "loss", "threshold": int(legacy.get("loss_threshold", 100)), "enabled": True},
            {"id": "latency", "type": "latency", "threshold": int(legacy.get("latency_threshold_ms", 500)), "enabled": True},
        ]
    return entry

def _get_device_monitoring(site_entry, device_id):
    devices = site_entry.setdefault("devices", {})
    entry = devices.get(device_id)
    if not isinstance(entry, dict):
        entry = {}
        devices[device_id] = entry
    entry.setdefault("enabled", False)
    entry.setdefault("placed", False)
    entry.setdefault("dock", "center")
    entry.setdefault("last_status", None)
    if "rules" not in entry:
        entry["rules"] = []
    return entry

def _get_monitoring_layout(site_entry):
    layout = site_entry.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    merged = {
        "top": int(layout.get("top", DEFAULT_MONITORING_LAYOUT["top"])),
        "bottom": int(layout.get("bottom", DEFAULT_MONITORING_LAYOUT["bottom"])),
        "left": int(layout.get("left", DEFAULT_MONITORING_LAYOUT["left"])),
        "right": int(layout.get("right", DEFAULT_MONITORING_LAYOUT["right"])),
        "labels": DEFAULT_MONITORING_LAYOUT["labels"].copy()
    }
    labels = layout.get("labels")
    if isinstance(labels, dict):
        for key in merged["labels"].keys():
            if isinstance(labels.get(key), str) and labels.get(key).strip():
                merged["labels"][key] = labels[key].strip()
    site_entry["layout"] = merged
    return merged

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
        _write_json_file(DATABASE_FILE, data)
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
        _write_json_file(SETTINGS_FILE, merged)

    return merged


def write_settings(settings):
    """Write settings file"""
    _write_sqlite_json("settings", settings)
    _write_json_file(SETTINGS_FILE, settings)

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
        cred = entry.get("credential_profile")
        if isinstance(cred, str) and cred.strip():
            mod_entry["credential_profile"] = cred.strip()
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
            value = params.get(name)
            if value is None:
                errors.append(f"{module_id}: missing {name}")
                continue
            if isinstance(value, str) and not value.strip():
                errors.append(f"{module_id}: missing {name}")

    return errors

def _get_auth_config():
    settings = read_settings()
    auth = settings.get("auth") if isinstance(settings, dict) else {}
    if not isinstance(auth, dict):
        auth = {}
    users = auth.get("users", [])
    if not isinstance(users, list):
        users = []
    enabled = bool(auth.get("enabled", False))
    return {"enabled": enabled, "users": users}

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
    username = session.get("user")
    user = _find_user(username) if username else None
    if user and user.get("disabled"):
        session.pop("user", None)
        return None
    return user

def _find_user(username):
    if not username:
        return None
    auth = _get_auth_config()
    for user in auth["users"]:
        if isinstance(user, dict) and user.get("username") == username:
            return user
    return None

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
    
    def run_module(self, module_id, config):
        """Run a module in background thread"""
        thread_id = str(uuid.uuid4())[:8]
        
        def module_thread():
            config_file = None
            try:
                log_file = os.path.join(BASE_DIR, f"module_log_{thread_id}.txt")
                # Update status
                with self.lock:
                    self.running_modules[thread_id] = {
                        "module_id": module_id,
                        "status": "running",
                        "start_time": datetime.now().isoformat(),
                        "progress": 0,
                        "log_file": log_file
                    }
                
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
                    "database_path": os.path.abspath(DATABASE_FILE),
                    "monitoring_db_path": os.path.abspath(MONITORING_FILE),
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
                if result.returncode == 0:
                    try:
                        module_output = json.loads(result.stdout.strip())
                        with self.lock:
                            self.module_results[thread_id] = {
                                "status": "completed",
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
                        self.running_modules[thread_id]["status"] = "completed"
                        self.running_modules[thread_id]["progress"] = 100
                        self.running_modules[thread_id]["completed_at"] = datetime.now().isoformat()

                # Sync SQLite from legacy JSON files after module writes
                try:
                    _sync_sqlite_from_legacy_files()
                except Exception:
                    pass
                
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
        # Keep log files for troubleshooting; manual cleanup if needed.
        _cleanup_module_logs()
        _cleanup_module_configs()
    
    def get_all_status(self):
        """Get status of all modules"""
        with self.lock:
            return {
                "running": list(self.running_modules.keys()),
                "completed": list(self.module_results.keys())
            }

# ==================== SCHEDULED MODULE RUNNER ====================

class ScheduleRunner:
    def __init__(self, poll_interval=2):
        self.poll_interval = poll_interval
        self.state = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

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

    def _resolve_sites(self, schedule):
        scope = schedule.get("site_scope", {}) if isinstance(schedule.get("site_scope"), dict) else {}
        mode = (scope.get("mode") or "selected").lower()
        selected = scope.get("sites") or []
        if mode == "all":
            data = read_database()
            return [s.get("name") for s in data.get("sites", []) if s.get("name")]
        return [name for name in selected if isinstance(name, str) and name.strip()]

    def _run_site_pipeline(self, site_name, schedule, available_modules):
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
            credential_profile = entry.get("credential_profile")
            if credential_profile:
                profiles = module_creds.get(module_id, []) if isinstance(module_creds, dict) else []
                for profile in profiles:
                    if isinstance(profile, dict) and profile.get("name") == credential_profile:
                        params["username"] = profile.get("username", "")
                        params["password"] = profile.get("password", "")
                        break
            config = {"site_name": site_name, "parameters": params}
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

        errors = _validate_schedule_config(schedule)
        if errors:
            with self.lock:
                state["last_run_at"] = datetime.now()
                state["last_result"] = {"error": "invalid_schedule", "details": errors}
                state["status"] = "idle"
                state["running"] = False
                state["next_run_at"] = None
            return

        sites = self._resolve_sites(schedule)
        run_mode = (schedule.get("site_run_mode") or "sequential").lower()
        schedule_result = {"sites": len(sites), "results": {}}

        available_modules = {m.get("id") for m in discover_modules() if isinstance(m, dict)}
        if not sites:
            schedule_result["error"] = "no_sites"
        else:
            if run_mode == "concurrent":
                with ThreadPoolExecutor(max_workers=len(sites)) as pool:
                    future_map = {pool.submit(self._run_site_pipeline, site, schedule, available_modules): site for site in sites}
                    for future in as_completed(future_map):
                        site = future_map.get(future)
                        try:
                            schedule_result["results"][site] = future.result()
                        except Exception as exc:
                            schedule_result["results"][site] = [{"error": str(exc)}]
            else:
                for site in sites:
                    try:
                        schedule_result["results"][site] = self._run_site_pipeline(site, schedule, available_modules)
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
        user = _get_effective_user()
        if not user:
            return jsonify({"error": "auth_required"}), 401
        if not _can_write_site(user, site_name):
            return jsonify({"error": "forbidden"}), 403

        from generale_visual_map import generate_visual_map

        result = generate_visual_map(site_name)

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

            try:
                monitoring = read_monitoring()
                sites_entry = monitoring.get("sites", {})
                if isinstance(sites_entry, dict) and old_name in sites_entry:
                    sites_entry[new_name] = sites_entry.pop(old_name)
                    write_monitoring(monitoring)
            except Exception:
                pass

            old_log = os.path.join(LOGS_DIR, f"{_safe_log_name(old_name)}.log")
            new_log = os.path.join(LOGS_DIR, f"{_safe_log_name(new_name)}.log")
            if os.path.exists(old_log) and not os.path.exists(new_log):
                try:
                    os.replace(old_log, new_log)
                except OSError:
                    pass
        
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
        if isinstance(params, dict) and params.get("credential_profile"):
            settings = read_settings()
            module_creds = settings.get("module_credentials", {}) if isinstance(settings, dict) else {}
            profiles = module_creds.get(module_id, []) if isinstance(module_creds, dict) else []
            profile_name = str(params.get("credential_profile")).strip()
            for profile in profiles:
                if isinstance(profile, dict) and profile.get("name") == profile_name:
                    params["username"] = profile.get("username", "")
                    params["password"] = profile.get("password", "")
                    break
            params.pop("credential_profile", None)
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
            lines = f.read().splitlines()[-200:]
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
        output = []
        for sched in schedules:
            if not isinstance(sched, dict):
                continue
            state = states.get(sched.get("id")) if sched.get("id") else None
            output.append(_serialize_schedule(sched, state))
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
    settings = read_settings()
    auth = settings.get("auth", {})
    users = auth.get("users", [])
    if request.method == 'GET':
        return jsonify([_sanitize_user(u) for u in users if isinstance(u, dict)])

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

    if any(isinstance(u, dict) and u.get("username") == username for u in users):
        return jsonify({"error": "user_exists"}), 400

    users.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
        "allowed_sites": allowed_sites,
        "disabled": disabled
    })
    auth["users"] = users
    settings["auth"] = auth
    write_settings(settings)
    return jsonify({"success": True, "user": username})

@app.route('/api/users/<username>', methods=['PUT', 'DELETE'])
def handle_user(username):
    current_user, err = _require_role("admin")
    if err:
        return err
    settings = read_settings()
    auth = settings.get("auth", {})
    users = auth.get("users", [])

    target = None
    for entry in users:
        if isinstance(entry, dict) and entry.get("username") == username:
            target = entry
            break
    if not target:
        return jsonify({"error": "user_not_found"}), 404

    if request.method == 'DELETE':
        auth["users"] = [u for u in users if not (isinstance(u, dict) and u.get("username") == username)]
        settings["auth"] = auth
        write_settings(settings)
        if current_user.get("username") == username:
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
        target["role"] = role
    if allowed_sites is not None:
        if not isinstance(allowed_sites, list):
            return jsonify({"error": "invalid_allowed_sites"}), 400
        target["allowed_sites"] = allowed_sites
    if disabled is not None:
        target["disabled"] = bool(disabled)
    if password:
        target["password_hash"] = generate_password_hash(password)

    auth["users"] = users
    settings["auth"] = auth
    write_settings(settings)
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
        return jsonify({"error": "invalid_credentials"}), 401
    session["user"] = username
    return jsonify({
        "authenticated": True,
        "user": username,
        "role": user.get("role", "guest"),
        "allowed_sites": _allowed_sites(user)
    })

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.pop("user", None)
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
    users = auth.get("users", [])
    if not isinstance(users, list):
        users = []

    if users:
        current_user = _get_effective_user()
        if not _is_admin(current_user):
            return jsonify({"error": "auth_required"}), 403

    users = [
        user for user in users
        if not (isinstance(user, dict) and user.get("username") == username)
    ]
    existing = _find_user(username)
    if not users:
        role = "admin"
    else:
        role = data.get("role") or (existing.get("role") if existing else "guest")
    allowed_sites = data.get("allowed_sites", []) if data.get("allowed_sites") is not None else (existing.get("allowed_sites") if existing else [])
    if not isinstance(allowed_sites, list):
        allowed_sites = []
    disabled = bool(data.get("disabled", False))

    users.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
        "allowed_sites": allowed_sites,
        "disabled": disabled
    })
    auth["users"] = users
    auth["enabled"] = enabled
    settings["auth"] = auth
    write_settings(settings)
    session["user"] = username

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
        return jsonify({"error": "invalid_credentials"}), 401
    settings = read_settings()
    auth = settings.get("auth", {})
    users = auth.get("users", [])
    for entry in users:
        if isinstance(entry, dict) and entry.get("username") == user.get("username"):
            entry["password_hash"] = generate_password_hash(new_password)
            break
    auth["users"] = users
    settings["auth"] = auth
    write_settings(settings)
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
    if "users" not in auth:
        auth["users"] = []
    settings["auth"] = auth
    write_settings(settings)
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

    file_map = {
        "devices.db": DATABASE_FILE,
        "settings.json": SETTINGS_FILE,
        "monitoring.db": MONITORING_FILE,
        "cmapp.sqlite3": SQLITE_DB_FILE,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name, path in file_map.items():
            if os.path.exists(path):
                zf.write(path, arcname=name)
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
            allowed = {
                'devices.db': DATABASE_FILE,
                'settings.json': SETTINGS_FILE,
                'monitoring.db': MONITORING_FILE,
                'cmapp.sqlite3': SQLITE_DB_FILE,
            }
            imported_sqlite = False
            for name in zf.namelist():
                if name in allowed:
                    target = allowed[name]
                    with zf.open(name) as src, open(target, 'wb') as dst:
                        dst.write(src.read())
                    if name == 'cmapp.sqlite3':
                        imported_sqlite = True
        if imported_sqlite:
            return jsonify({"success": True})
        _sync_sqlite_from_legacy_files()
        if _read_sqlite_json("settings") is None:
            init_settings()
        if _read_sqlite_json("devices") is None:
            init_database()
        if _read_sqlite_json("monitoring") is None:
            init_monitoring()
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
            settings = {k: v for k, v in settings.items() if k not in ("auth", "module_credentials")}
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
        auth = current_settings.get("auth", {})
        current_settings["auth"] = {"enabled": bool(auth.get("enabled", False))}
        return jsonify(current_settings)

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
    online_devices = len([d for d in devices if d.get("status") == "online"])
    offline_devices = len([d for d in devices if d.get("status") == "offline"])
    
    sites = _filter_sites_for_user(data.get("sites", []), user)
    unknown_devices = len([d for d in devices if (d.get("type") or "").lower() in ("", "unknown")])
    stats = {
        "total_sites": len(sites),
        "total_devices": len(devices),
        "online_devices": online_devices,
        "offline_devices": offline_devices,
        "unknown_status": len(devices) - online_devices - offline_devices,
        "unknown_devices": unknown_devices,
        "last_modified": data.get("meta", {}).get("last_modified", "Never")
    }
    
    return jsonify(stats)

@app.route('/api/monitoring/site/<site_name>')
def get_monitoring_site(site_name):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _can_read_site(user, site_name):
        return jsonify({"error": "forbidden"}), 403

    monitoring = read_monitoring()
    site_entry = _get_monitoring_site(monitoring, site_name)
    layout = _get_monitoring_layout(site_entry)
    site_rules = site_entry.get("rules", [rule.copy() for rule in DEFAULT_MONITORING_RULES_LIST])

    data = read_database()
    devices = [d for d in data.get("devices", []) if d.get("site") == site_name]
    now = datetime.now()

    results = []
    for device in devices:
        device_id = device.get("id")
        m = site_entry.get("devices", {}).get(device_id, {}) if device_id else {}
        enabled = bool(m.get("enabled", False))
        last_check = m.get("last_check")
        last_check_dt = None
        if last_check:
            try:
                last_check_dt = datetime.fromisoformat(last_check)
            except ValueError:
                last_check_dt = None

        status = "unknown"
        if not enabled:
            status = "unknown"
        elif last_check_dt:
            age = (now - last_check_dt).total_seconds()
            loss = m.get("packet_loss")
            latency = m.get("avg_latency_ms")
            status = "ok"
            rules = m.get("rules") or site_rules
            for rule in rules:
                if not isinstance(rule, dict) or not rule.get("enabled", True):
                    continue
                rtype = rule.get("type")
                threshold = rule.get("threshold")
                if rtype == "stale" and threshold is not None and age > float(threshold):
                    status = "not_ok"
                    break
                if rtype == "loss" and loss is not None and threshold is not None and loss >= float(threshold):
                    status = "not_ok"
                    break
                if rtype == "latency" and latency is not None and threshold is not None and latency >= float(threshold):
                    status = "not_ok"
                    break

        results.append({
            "id": device_id,
            "name": device.get("name"),
            "ip": device.get("ip"),
            "status": status,
            "packet_loss": m.get("packet_loss"),
            "avg_latency_ms": m.get("avg_latency_ms"),
            "last_check": last_check,
            "enabled": enabled,
            "rules": m.get("rules") or [],
            "placed": bool(m.get("placed", False)),
            "dock": m.get("dock", "center")
        })

    return jsonify({
        "site": site_name,
        "rules": site_rules,
        "devices": results,
        "layout": layout
    })


@app.route('/api/monitoring/logs/<site_name>')
def get_monitoring_logs(site_name):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _can_read_site(user, site_name):
        return jsonify({"error": "forbidden"}), 403
    init_monitoring()
    safe = _safe_log_name(site_name)
    log_path = os.path.join(LOGS_DIR, f"{safe}.log")
    if not os.path.exists(log_path):
        return jsonify({"site": site_name, "lines": []})
    try:
        with portalocker.Lock(log_path, 'r', timeout=2, encoding='utf-8') as f:
            lines = f.read().splitlines()[-100:]
        return jsonify({"site": site_name, "lines": lines})
    except (portalocker.exceptions.LockException, OSError):
        return jsonify({"site": site_name, "lines": []})

@app.route('/api/monitoring/rules/<site_name>', methods=['PUT'])
def update_monitoring_rules(site_name):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _can_write_site(user, site_name):
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json() or {}
    init_monitoring()
    with MONITORING_LOCK:
        monitoring = read_monitoring()
    site_entry = _get_monitoring_site(monitoring, site_name)
    device_id = data.get("device_id")
    rules = data.get("rules")
    enabled = data.get("enabled")

    if rules is None:
        # Backwards-compatible payload (site rules)
        rules = [
            {"id": "loss", "type": "loss", "threshold": int(data.get("loss_threshold", 100)), "enabled": True},
            {"id": "latency", "type": "latency", "threshold": int(data.get("latency_threshold_ms", 500)), "enabled": True},
        ]
    if not isinstance(rules, list):
        return jsonify({"error": "invalid_rules"}), 400
    sanitized = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rtype = rule.get("type")
        if rtype not in ("loss", "latency"):
            continue
        threshold = rule.get("threshold")
        try:
            threshold_val = float(threshold)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_threshold"}), 400
        sanitized.append({
            "id": rule.get("id") or rtype,
            "type": rtype,
            "threshold": threshold_val,
            "enabled": bool(rule.get("enabled", True)),
        })

    if device_id:
        device_entry = _get_device_monitoring(site_entry, device_id)
        device_entry["rules"] = sanitized
        if enabled is not None:
            device_entry["enabled"] = bool(enabled)
        with MONITORING_LOCK:
            write_monitoring(monitoring)
        log_event(site_name, f"rules updated for device {device_id}")
        return jsonify({"success": True, "device_id": device_id, "rules": device_entry["rules"], "enabled": device_entry["enabled"]})

    site_entry["rules"] = sanitized or [
        {"id": "loss", "type": "loss", "threshold": 100, "enabled": True},
        {"id": "latency", "type": "latency", "threshold": 500, "enabled": True},
    ]
    with MONITORING_LOCK:
        write_monitoring(monitoring)
    log_event(site_name, "site rules updated")
    return jsonify({"success": True, "rules": site_entry["rules"]})

@app.route('/api/monitoring/layout/<site_name>', methods=['PUT'])
def update_monitoring_layout(site_name):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _can_write_site(user, site_name):
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json() or {}
    layout = payload.get("layout")
    if not isinstance(layout, dict):
        return jsonify({"error": "invalid_layout"}), 400

    init_monitoring()
    with MONITORING_LOCK:
        monitoring = read_monitoring()
    site_entry = _get_monitoring_site(monitoring, site_name)
    merged = _get_monitoring_layout(site_entry)
    for key in ("top", "bottom", "left", "right"):
        if key in layout:
            try:
                merged[key] = int(layout[key])
            except (TypeError, ValueError):
                pass
    labels = layout.get("labels")
    if isinstance(labels, dict):
        merged_labels = merged.get("labels", {})
        for key in merged_labels.keys():
            if isinstance(labels.get(key), str):
                merged_labels[key] = labels[key].strip() or merged_labels[key]
        merged["labels"] = merged_labels
    site_entry["layout"] = merged
    with MONITORING_LOCK:
        write_monitoring(monitoring)
    return jsonify({"success": True, "layout": merged})


@app.route('/api/monitoring/device/<site_name>/<device_id>', methods=['PUT'])
def update_monitoring_device(site_name, device_id):
    user = _get_effective_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    if not _can_write_site(user, site_name):
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json() or {}
    init_monitoring()
    with MONITORING_LOCK:
        monitoring = read_monitoring()
    site_entry = _get_monitoring_site(monitoring, site_name)
    device_entry = _get_device_monitoring(site_entry, device_id)

    if "placed" in data:
        device_entry["placed"] = bool(data.get("placed"))

    if "dock" in data:
        dock = data.get("dock")
        if dock not in ("top", "right", "bottom", "left", "center"):
            return jsonify({"error": "invalid_dock"}), 400
        device_entry["dock"] = dock

    if "enabled" in data:
        device_entry["enabled"] = bool(data.get("enabled"))

    if "rules" in data and isinstance(data.get("rules"), list):
        device_entry["rules"] = data.get("rules")

    with MONITORING_LOCK:
        write_monitoring(monitoring)
    if "dock" in data or "placed" in data:
        log_event(site_name, f"layout updated for device {device_id}")
    if "enabled" in data:
        state = "enabled" if device_entry.get("enabled") else "disabled"
        log_event(site_name, f"monitoring {state} for device {device_id}")

    return jsonify({
        "success": True,
        "device_id": device_id,
        "dock": device_entry.get("dock", "center"),
        "enabled": device_entry.get("enabled", False),
        "rules": device_entry.get("rules", []),
        "placed": device_entry.get("placed", False)
    })


def _ping_host_once(ip: str) -> tuple[int | None, int | None]:
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", "1000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
        return 100, None
    output = result.stdout + "\n" + result.stderr
    loss = None
    avg = None
    for line in output.splitlines():
        if "Packets:" in line and "% loss" in line:
            try:
                loss_part = line.split("% loss")[0]
                loss = int(loss_part.split("(")[-1].strip())
            except ValueError:
                pass
        if "packet loss" in line:
            try:
                loss = int(line.split("%")[0].split()[-1])
            except ValueError:
                pass
        if "Average =" in line:
            try:
                avg_text = line.split("Average =")[-1].strip().replace("ms", "")
                avg = int(avg_text)
            except ValueError:
                pass
        if "min/avg/max" in line:
            try:
                avg_text = line.split("=")[-1].strip().split("/")[1]
                avg = int(float(avg_text))
            except (ValueError, IndexError):
                pass
    if loss is None:
        loss = 100 if result.returncode != 0 else 0
    return loss, avg


def _monitoring_cycle():
    data = read_database()
    monitoring = read_monitoring()
    now = datetime.now().isoformat()
    for site in data.get("sites", []):
        site_name = site.get("name")
        if not site_name:
            continue
        site_known_ids = set()
        site_entry = _get_monitoring_site(monitoring, site_name)
        site_rules = site_entry.get("rules", DEFAULT_MONITORING_RULES_LIST)
        devices = [d for d in data.get("devices", []) if d.get("site") == site_name]
        for device in devices:
            device_id = device.get("id")
            ip = device.get("ip")
            if not device_id:
                continue
            site_known_ids.add(device_id)
            device_entry = _get_device_monitoring(site_entry, device_id)
            if not device_entry.get("enabled", False):
                continue
            if not ip:
                device_entry.update({
                    "ip": ip,
                    "packet_loss": 100,
                    "avg_latency_ms": None,
                    "last_check": now
                })
            else:
                loss, avg = _ping_host_once(ip)
                device_entry.update({
                    "ip": ip,
                    "packet_loss": loss,
                    "avg_latency_ms": avg,
                    "last_check": now
                })
                if loss is not None and loss >= 100:
                    log_event(site_name, f"ping failed for device {device_id}")

            # Update last_status for lean logging
            status = "unknown"
            if device_entry.get("enabled"):
                status = "ok"
                rules = device_entry.get("rules") or site_rules
                for rule in rules:
                    if not isinstance(rule, dict) or not rule.get("enabled", True):
                        continue
                    threshold = rule.get("threshold")
                    if rule.get("type") == "loss" and threshold is not None:
                        if device_entry.get("packet_loss") is not None and device_entry["packet_loss"] >= float(threshold):
                            status = "not_ok"
                            break
            if device_entry.get("last_status") != status:
                device_entry["last_status"] = status
                log_event(site_name, f"status {status} for device {device_id}")

        # Prune removed devices for this site
        site_entry["devices"] = {
            did: entry for did, entry in site_entry.get("devices", {}).items()
            if did in site_known_ids
        }

    write_monitoring(monitoring)


def start_monitoring_loop():
    def loop():
        while True:
            try:
                with MONITORING_LOCK:
                    _monitoring_cycle()
            except Exception as exc:
                print(f"[MONITOR] Error: {exc}", file=sys.stderr)
            time.sleep(MONITORING_INTERVAL_SEC)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()

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
    init_monitoring()

    # Start background monitoring loop
    start_monitoring_loop()
    
    # Create modules directory if it doesn't exist
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
    
    # Create example modules if they don't exist
    example_modules = ['add_device_manual', 'cdp_discovery', 'view_map', 'ping_monitor', 'enforce_oui_table', 'ubiquiti_cdp_reader']
    
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
