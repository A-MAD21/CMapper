import json
import os
import sqlite3
from datetime import datetime


def _get_conn(db_path: str) -> sqlite3.Connection:
    if not db_path:
        raise ValueError("db_path is required")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS json_store ("
        "name TEXT PRIMARY KEY,"
        "json TEXT NOT NULL,"
        "updated_at TEXT NOT NULL)"
    )
    return conn


def read_json_store(db_path: str, name: str, default=None):
    try:
        conn = _get_conn(db_path)
        cur = conn.execute("SELECT json FROM json_store WHERE name = ?", (name,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return default
        return json.loads(row[0])
    except Exception:
        return default


def write_json_store(db_path: str, name: str, data) -> None:
    payload = json.dumps(data)
    now = datetime.now().isoformat()
    conn = _get_conn(db_path)
    conn.execute(
        "INSERT INTO json_store (name, json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
        (name, payload, now)
    )
    conn.commit()
    conn.close()
