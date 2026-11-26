#!/usr/bin/env python3
"""YatraFlow operations dashboard API.

SQLite is the zero-configuration development store. Set YATRAFLOW_DB_ENGINE=sqlserver
and provide SQL Server settings in .env to use an existing SQL Server database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
KOLKATA = ZoneInfo("Asia/Kolkata")


def load_dotenv() -> None:
    """Read a simple .env file without requiring python-dotenv."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()
DB_ENGINE = os.getenv("YATRAFLOW_DB_ENGINE", "sqlite").strip().lower()
SQLITE_PATH = Path(os.getenv("YATRAFLOW_SQLITE_PATH", str(BASE_DIR / "data" / "yatraflow.sqlite3")))
DEMO_DATE = os.getenv("YATRAFLOW_DEMO_DATE", "2026-11-25")
PORT = int(os.getenv("PORT", "5173"))
SESSION_COOKIE = "yatraflow_session"
SESSION_HOURS = 8
PBKDF2_ITERATIONS = 310_000
DEMO_ADMIN_EMAIL = "admin@yatraflow.test"
DEMO_ADMIN_PASSWORD = "YatraDemo!2026"
ROLE_LABELS = {
    "super_admin": "Super Admin",
    "site_admin": "Site Admin",
    "emergency_operator": "Emergency Operator",
    "analytics_viewer": "Analytics Viewer",
}
ROLE_PERMISSIONS = {
    "super_admin": {"read", "operate", "manage_admins", "view_pii", "audit"},
    "site_admin": {"read", "operate", "manage_admins", "view_pii", "audit"},
    "emergency_operator": {"read", "operate", "view_pii"},
    "analytics_viewer": {"read_analytics"},
}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(expected_text + "=" * (-len(expected_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    people_inside INTEGER NOT NULL,
    checked_in_today INTEGER NOT NULL DEFAULT 0,
    average_dwell_minutes INTEGER NOT NULL DEFAULT 120,
    people_change_percent REAL NOT NULL DEFAULT 0,
    predicted_tomorrow INTEGER NOT NULL DEFAULT 0,
    peak_window TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    current_visitors INTEGER NOT NULL DEFAULT 0,
    capacity INTEGER NOT NULL DEFAULT 0,
    density_percent INTEGER NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT 'Low',
    updated_at TEXT,
    UNIQUE(site_id, name)
);
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL,
    title TEXT NOT NULL,
    visitor_name TEXT NOT NULL DEFAULT '',
    visitor_id TEXT,
    zone_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Open',
    assigned_to TEXT,
    contact_phone TEXT,
    latitude REAL,
    longitude REAL,
    resolved_at TEXT,
    updated_at TEXT,
    reported_minutes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visitors (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    registered_at TEXT,
    pass_id TEXT NOT NULL,
    zone_name TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    pass_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'On site'
);
CREATE TABLE IF NOT EXISTS footfall (
    site_id INTEGER NOT NULL,
    date_key TEXT NOT NULL,
    label TEXT NOT NULL,
    series TEXT NOT NULL,
    visitor_count INTEGER NOT NULL,
    PRIMARY KEY(site_id, date_key, series)
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    role_key TEXT NOT NULL DEFAULT 'site_admin',
    access_scope TEXT NOT NULL,
    shift_status TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT,
    last_active TEXT NOT NULL,
    last_login TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS broadcasts (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Information',
    zone_name TEXT,
    priority TEXT NOT NULL,
    audience TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending_provider',
    reached INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    ip_address TEXT
);
CREATE TABLE IF NOT EXISTS visitor_sessions (
    id TEXT PRIMARY KEY,
    external_id TEXT,
    visitor_id TEXT NOT NULL,
    site_id INTEGER NOT NULL,
    zone_name TEXT,
    checked_in_at TEXT NOT NULL,
    checked_out_at TEXT,
    source TEXT NOT NULL DEFAULT 'mobile'
);
CREATE TABLE IF NOT EXISTS incident_status_history (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    site_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    actor_admin_id TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_notes (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    site_id INTEGER NOT NULL,
    author_admin_id TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS emergency_assignments (
    id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    site_id INTEGER NOT NULL,
    responder TEXT NOT NULL,
    provider TEXT NOT NULL,
    request_status TEXT NOT NULL,
    external_reference TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crowd_observations (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    zone_name TEXT NOT NULL,
    visitor_count INTEGER NOT NULL,
    capacity INTEGER,
    density_percent REAL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crowd_forecasts (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    forecast_date TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    predicted_visitors INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT,
    confidence REAL,
    generated_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    site_id INTEGER NOT NULL,
    zone_name TEXT,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
    id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    visitor_id TEXT,
    channel TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS app_metadata (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_incidents_site_status ON incidents(site_id, status);
CREATE INDEX IF NOT EXISTS ix_visitors_site ON visitors(site_id);
CREATE INDEX IF NOT EXISTS ix_footfall_site_date ON footfall(site_id, date_key);
CREATE INDEX IF NOT EXISTS ix_broadcasts_site_created ON broadcasts(site_id, created_at);
CREATE INDEX IF NOT EXISTS ix_sessions_site_active ON visitor_sessions(site_id, checked_out_at);
CREATE INDEX IF NOT EXISTS ix_incident_history_incident ON incident_status_history(incident_id, created_at);
CREATE INDEX IF NOT EXISTS ix_crowd_observations_zone_time ON crowd_observations(site_id, zone_name, observed_at);
CREATE INDEX IF NOT EXISTS ix_forecasts_site_date ON crowd_forecasts(site_id, forecast_date);
CREATE INDEX IF NOT EXISTS ix_notifications_site_created ON notifications(site_id, created_at);
"""


def app_today() -> date:
    if DB_ENGINE == "sqlite":
        try:
            return date.fromisoformat(DEMO_DATE)
        except ValueError:
            pass
    return datetime.now(KOLKATA).date()


def now_iso() -> str:
    if DB_ENGINE == "sqlite":
        d = app_today()
        return datetime.combine(d, time(14, 32), tzinfo=KOLKATA).isoformat(timespec="seconds")
    return datetime.now(KOLKATA).isoformat(timespec="seconds")


def seed_sqlite(conn: sqlite3.Connection) -> None:
    today = app_today()
    anchor = datetime.combine(today, time(14, 32), tzinfo=KOLKATA)
    stamp = anchor.isoformat(timespec="seconds")

    conn.executemany(
        "INSERT INTO sites (id,name,city,capacity,people_inside,checked_in_today,average_dwell_minutes,people_change_percent,predicted_tomorrow,peak_window,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "Amber Fort", "Jaipur", 16000, 12480, 18624, 134, 8.0, 0, "", stamp),
            (2, "City Palace", "Jaipur", 12000, 7460, 11524, 117, 4.2, 0, "", stamp),
            (3, "Hawa Mahal", "Jaipur", 9000, 6210, 9850, 95, -2.1, 0, "", stamp),
        ],
    )
    conn.executemany(
        "INSERT INTO zones (id,site_id,name,current_visitors,capacity,density_percent,level,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            (101, 1, "Main Gate", 2840, 3000, 94, "Critical watch", stamp),
            (102, 1, "Sheesh Mahal", 1920, 3100, 62, "Moderate", stamp),
            (103, 1, "Parking", 680, 2400, 28, "Low", stamp),
            (104, 1, "Courtyard", 3140, 3900, 81, "High", stamp),
            (201, 2, "Main Gate", 1640, 2450, 67, "Moderate", stamp),
            (202, 2, "Mubarak Mahal", 1830, 2400, 76, "High", stamp),
            (203, 2, "Pritam Chowk", 2270, 2780, 82, "High", stamp),
            (204, 2, "Museum Wing", 920, 2700, 34, "Low", stamp),
            (301, 3, "Front Plaza", 2210, 2830, 78, "High", stamp),
            (302, 3, "Main Gate", 1760, 2100, 84, "High", stamp),
            (303, 3, "Museum Wing", 1140, 2375, 48, "Moderate", stamp),
            (304, 3, "East Approach", 820, 2828, 29, "Low", stamp),
        ],
    )
    incident_rows = [
        ("SOS-2026-001", 1, "SOS", "Critical", "Visitor assistance", "Aarav Sharma", "Main Gate", "Development sample incident; no mobile GPS/contact feed connected.", "Assigned", "Medical team 02", 2, (anchor - timedelta(minutes=2)).isoformat(timespec="seconds")),
        ("SOS-2026-002", 1, "SOS", "Critical", "Visitor assistance", "Visitor unidentified", "Courtyard", "Development sample incident; not received from a connected mobile app.", "Submitted", None, 6, (anchor - timedelta(minutes=6)).isoformat(timespec="seconds")),
        ("RPT-2026-001", 1, "Report", "Medium", "Cleanliness report", "Visitor report", "Sheesh Mahal", "Cleanliness issue reported near the north path; photo attached.", "Open", None, 14, (anchor - timedelta(minutes=14)).isoformat(timespec="seconds")),
        ("SOS-2026-003", 1, "SOS", "Critical", "First-aid support", "Leela Patel", "Sheesh Mahal", "Development sample incident; no external dispatch service connected.", "Assigned", "Medical team 01", 16, (anchor - timedelta(minutes=16)).isoformat(timespec="seconds")),
        ("SYS-2026-018", 1, "Crowd", "Medium", "Density threshold exceeded", "", "Main Gate", "Automated alert: zone density is above 90%.", "Monitoring", "Site supervisor", 21, (anchor - timedelta(minutes=21)).isoformat(timespec="seconds")),
        ("WX-2026-004", 1, "Weather", "Advisory", "Heavy rain advisory", "", "All zones", "Local weather feed reports heavy rain in Jaipur.", "Resolved", "Control room", 20, (anchor - timedelta(minutes=20)).isoformat(timespec="seconds")),
        ("SOS-2026-004", 2, "SOS", "Critical", "Visitor assistance", "Rohit Jain", "Pritam Chowk", "Development sample incident; no mobile app source connected.", "Submitted", None, 4, (anchor - timedelta(minutes=4)).isoformat(timespec="seconds")),
    ]
    conn.executemany(
        "INSERT INTO incidents (id,site_id,type,priority,title,visitor_name,zone_name,description,status,assigned_to,reported_minutes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        incident_rows,
    )

    visitor_rows = [
        ("V-10832", 1, "Aarav Sharma", None, stamp, "VF-10832", "Main Gate", "10:42 AM", "Family", "Assistance"),
        ("V-10791", 1, "Meera Kapoor", None, stamp, "VF-10791", "Courtyard", "10:21 AM", "VIP", "On site"),
        ("V-10662", 1, "Kabir Singh", None, stamp, "VF-10662", "Sheesh Mahal", "9:58 AM", "Standard", "On site"),
        ("V-10550", 1, "Priya Nair", None, stamp, "VF-10550", "Parking", "9:44 AM", "Family", "On site"),
        ("V-10412", 1, "Dev Malhotra", None, stamp, "VF-10412", "Main Gate", "9:31 AM", "VIP", "Assistance"),
        ("V-10220", 1, "Anaya Rao", None, stamp, "VF-10220", "Courtyard", "9:12 AM", "Standard", "On site"),
        ("V-20931", 2, "Rohit Jain", None, stamp, "CP-20931", "Pritam Chowk", "10:14 AM", "Standard", "Assistance"),
        ("V-30612", 3, "Isha Verma", None, stamp, "HM-30612", "Front Plaza", "11:02 AM", "Family", "On site"),
    ]
    conn.executemany("INSERT INTO visitors (id,site_id,full_name,phone,registered_at,pass_id,zone_name,entry_time,pass_type,status) VALUES (?,?,?,?,?,?,?,?,?,?)", visitor_rows)

    alert_rows = [
        ("ALT-2026-001", 1, "Crowd", "Main Gate over 90% density", "Suggest alternate route via East Gate · 3 min ago", (anchor - timedelta(minutes=3)).isoformat(timespec="seconds")),
        ("ALT-2026-002", 1, "Weather", "Heavy rain advisory for Jaipur", "Source: local weather feed · 20 min ago", (anchor - timedelta(minutes=20)).isoformat(timespec="seconds")),
        ("ALT-2026-003", 1, "Security", "Response team on standby", "Courtyard post · checked in 8 min ago", (anchor - timedelta(minutes=8)).isoformat(timespec="seconds")),
        ("ALT-2026-004", 2, "Crowd", "Pritam Chowk nearing high density", "Route guests through the museum wing.", (anchor - timedelta(minutes=7)).isoformat(timespec="seconds")),
        ("ALT-2026-005", 3, "Weather", "Light rain expected this afternoon", "Review covered waiting areas.", (anchor - timedelta(minutes=11)).isoformat(timespec="seconds")),
    ]
    conn.executemany("INSERT INTO alerts (id,site_id,category,title,description,created_at) VALUES (?,?,?,?,?,?)", alert_rows)

    admin_rows = [
        ("ADM-001", 1, "Ava Desai", DEMO_ADMIN_EMAIL, "Super Admin", "super_admin", "Full site access", "On shift", 1, hash_password(DEMO_ADMIN_PASSWORD), "Now", None, stamp),
        ("ADM-002", 1, "Rahul Mehta", "rahul.mehta@yatraflow.in", "Emergency Operator", "emergency_operator", "Incidents · alerts", "On shift", 1, None, "4 min ago", None, stamp),
        ("ADM-003", 1, "Sana Khan", "sana.khan@yatraflow.in", "Site Admin", "site_admin", "Visitors · reports", "On shift", 1, None, "18 min ago", None, stamp),
        ("ADM-004", 1, "Arjun Singh", "arjun.singh@yatraflow.in", "Analytics Viewer", "analytics_viewer", "Analytics · live map", "Off shift", 1, None, "Yesterday", None, stamp),
        ("ADM-005", 2, "Kavita Rao", "kavita.rao@yatraflow.in", "Site Admin", "site_admin", "Full site access", "On shift", 1, None, "Now", None, stamp),
        ("ADM-006", 3, "Devika Singh", "devika.singh@yatraflow.in", "Site Admin", "site_admin", "Full site access", "On shift", 1, None, "Now", None, stamp),
    ]
    conn.executemany("INSERT INTO admins (id,site_id,full_name,email,role,role_key,access_scope,shift_status,active,password_hash,last_active,last_login,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", admin_rows)

    broadcast_rows = [
        ("BR-2026-101", 1, "Heavy rain advisory", "Please move to covered areas and follow staff directions while the rain advisory is active.", "Weather", None, "warning", "all_visitors", "pending_provider", 0, (anchor - timedelta(hours=2, minutes=22)).isoformat(timespec="seconds")),
        ("BR-2026-102", 1, "Main Gate queue update", "For a quicker entry, please use the East Gate. Follow the wayfinding signs.", "Information", "Main Gate", "info", "zone:Main Gate", "pending_provider", 0, (anchor - timedelta(hours=3, minutes=50)).isoformat(timespec="seconds")),
        ("BR-2026-103", 1, "Lost child support point", "Families needing help can visit the visitor support desk beside the main entrance.", "Safety", None, "warning", "all_visitors", "pending_provider", 0, (anchor - timedelta(days=1)).isoformat(timespec="seconds")),
    ]
    conn.executemany("INSERT INTO broadcasts (id,site_id,title,message,category,zone_name,priority,audience,delivery_status,reached,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", broadcast_rows)

    # Historical daily series used by the dashboard and the 14/30/90-day analytics views.
    sites = [(1, 12480), (2, 7460), (3, 6210)]
    for site_id, people_now in sites:
        start = today - timedelta(days=89)
        site_factor = {1: 1.0, 2: 0.77, 3: 0.66}[site_id]
        for offset in range(90):
            day = start + timedelta(days=offset)
            weekend = 1.10 if day.weekday() >= 5 else 1.0
            wave = math.sin(offset * 0.23) * 1150 + math.sin(offset * 0.071) * 650
            v24 = max(3200, min(15500, int((7700 + wave) * site_factor * weekend)))
            v25 = max(3600, min(15800, int((9200 + wave * 1.05) * site_factor * weekend)))
            trend = (offset / 89) * 1600
            v26 = max(4000, min(15800, int((10400 + wave * 1.08 + trend) * site_factor * weekend)))
            label = day.strftime("%-d %b") if sys.platform != "win32" else day.strftime("%d %b").lstrip("0")
            rows = [
                (site_id, day.isoformat(), label, "avg_2024", v24),
                (site_id, day.isoformat(), label, "avg_2025", v25),
                (site_id, day.isoformat(), label, "actual_2026", v26),
            ]
            conn.executemany("INSERT INTO footfall (site_id,date_key,label,series,visitor_count) VALUES (?,?,?,?,?)", rows)


def migrate_sqlite(conn: sqlite3.Connection) -> None:
    additions = {
        "sites": {"checked_in_today": "INTEGER NOT NULL DEFAULT 0", "average_dwell_minutes": "INTEGER NOT NULL DEFAULT 120"},
        "zones": {"capacity": "INTEGER NOT NULL DEFAULT 0", "updated_at": "TEXT"},
        "incidents": {"visitor_id": "TEXT", "contact_phone": "TEXT", "latitude": "REAL", "longitude": "REAL", "resolved_at": "TEXT", "updated_at": "TEXT"},
        "visitors": {"phone": "TEXT", "registered_at": "TEXT"},
        "admins": {"role_key": "TEXT NOT NULL DEFAULT 'site_admin'", "active": "INTEGER NOT NULL DEFAULT 1", "password_hash": "TEXT", "last_login": "TEXT", "created_at": "TEXT"},
        "broadcasts": {"category": "TEXT NOT NULL DEFAULT 'Information'", "zone_name": "TEXT", "delivery_status": "TEXT NOT NULL DEFAULT 'pending_provider'"},
    }
    for table, fields in additions.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in fields.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    conn.execute("UPDATE sites SET checked_in_today=CASE id WHEN 1 THEN 18624 WHEN 2 THEN 11524 WHEN 3 THEN 9850 ELSE checked_in_today END WHERE checked_in_today=0")
    conn.execute("UPDATE sites SET average_dwell_minutes=CASE id WHEN 1 THEN 134 WHEN 2 THEN 117 WHEN 3 THEN 95 ELSE average_dwell_minutes END WHERE average_dwell_minutes=120")
    conn.execute("UPDATE zones SET capacity=CASE id WHEN 101 THEN 3000 WHEN 102 THEN 3100 WHEN 103 THEN 2400 WHEN 104 THEN 3900 WHEN 201 THEN 2450 WHEN 202 THEN 2400 WHEN 203 THEN 2780 WHEN 204 THEN 2700 WHEN 301 THEN 2830 WHEN 302 THEN 2100 WHEN 303 THEN 2375 WHEN 304 THEN 2828 ELSE capacity END WHERE capacity=0")
    conn.execute("UPDATE zones SET updated_at=? WHERE updated_at IS NULL", (now_iso(),))
    conn.execute("UPDATE admins SET email=? WHERE LOWER(email)='ava.desai@yatraflow.in' AND NOT EXISTS (SELECT 1 FROM admins WHERE LOWER(email)=LOWER(?))", (DEMO_ADMIN_EMAIL, DEMO_ADMIN_EMAIL))
    conn.execute("UPDATE admins SET role_key=CASE LOWER(role) WHEN 'super admin' THEN 'super_admin' WHEN 'site admin' THEN 'site_admin' WHEN 'emergency operator' THEN 'emergency_operator' WHEN 'response coordinator' THEN 'emergency_operator' WHEN 'analytics viewer' THEN 'analytics_viewer' ELSE role_key END")
    conn.execute("UPDATE admins SET role='Super Admin', role_key='super_admin', password_hash=? WHERE LOWER(email)=LOWER(?) AND (password_hash IS NULL OR password_hash='')", (hash_password(DEMO_ADMIN_PASSWORD), DEMO_ADMIN_EMAIL))
    conn.execute("UPDATE incidents SET status='Assigned', updated_at=COALESCE(updated_at,created_at) WHERE UPPER(type)='SOS' AND status='Responding'")
    conn.execute("UPDATE incidents SET status='Submitted', updated_at=COALESCE(updated_at,created_at) WHERE UPPER(type)='SOS' AND status='Open'")
    conn.execute("INSERT OR IGNORE INTO app_metadata (meta_key,meta_value) VALUES ('data_mode','demo')")
    conn.execute("INSERT OR IGNORE INTO app_metadata (meta_key,meta_value) VALUES ('mobile_integration','not_connected')")
    # The local SQLite store is always the development/sample dataset. Do not
    # allow environment variables to relabel its illustrative records as real.
    conn.execute("UPDATE app_metadata SET meta_value='demo' WHERE meta_key='data_mode'")
    mode = conn.execute("SELECT meta_value FROM app_metadata WHERE meta_key='data_mode'").fetchone()
    if mode and mode[0] == 'demo':
        conn.execute("UPDATE sites SET predicted_tomorrow=0,peak_window='' WHERE id IN (1,2,3)")
        conn.execute("UPDATE broadcasts SET audience=CASE WHEN id='BR-2026-102' THEN 'zone:Main Gate' ELSE 'all_visitors' END,reached=0,delivery_status='pending_provider' WHERE id IN ('BR-2026-101','BR-2026-102','BR-2026-103')")


def initialize_sqlite() -> None:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SQLITE_SCHEMA)
        migrate_sqlite(conn)
        count = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        if count == 0:
            seed_sqlite(conn)
        # A local seed is intentionally a demo-only login. Production SQL Server users must bootstrap credentials via .env.
        if DB_ENGINE in {"sqlite", "sqlite3"}:
            demo_admin = conn.execute("SELECT id FROM admins WHERE LOWER(email)=LOWER(?)", (DEMO_ADMIN_EMAIL,)).fetchone()
            if demo_admin is None:
                raise RuntimeError("Demo administrator account could not be initialized")
        conn.commit()
    finally:
        conn.close()


def _odbc_brace(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def sqlserver_connection_string() -> str:
    raw = os.getenv("SQLSERVER_CONNECTION_STRING", "").strip()
    if raw:
        return raw
    driver = os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
    server = os.getenv("SQLSERVER_SERVER", "").strip()
    database = os.getenv("SQLSERVER_DATABASE", "").strip()
    if not server or not database:
        raise RuntimeError("Set SQLSERVER_SERVER and SQLSERVER_DATABASE in .env")
    parts = [f"DRIVER={_odbc_brace(driver)}", f"SERVER={_odbc_brace(server)}", f"DATABASE={_odbc_brace(database)}"]
    trusted = os.getenv("SQLSERVER_TRUSTED_CONNECTION", "false").lower() in {"1", "true", "yes"}
    if trusted:
        parts.append("Trusted_Connection=yes")
    else:
        username = os.getenv("SQLSERVER_USERNAME", "")
        password = os.getenv("SQLSERVER_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("Set SQLSERVER_USERNAME and SQLSERVER_PASSWORD, or enable SQLSERVER_TRUSTED_CONNECTION")
        parts.extend([f"UID={_odbc_brace(username)}", f"PWD={_odbc_brace(password)}"])
    encrypt = os.getenv("SQLSERVER_ENCRYPT", "yes")
    trust_cert = os.getenv("SQLSERVER_TRUST_SERVER_CERTIFICATE", "no")
    parts.extend([f"Encrypt={encrypt}", f"TrustServerCertificate={trust_cert}", "Connection Timeout=5"])
    return ";".join(parts) + ";"


def db_connect():
    if DB_ENGINE in {"sqlite", "sqlite3"}:
        conn = sqlite3.connect(SQLITE_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    if DB_ENGINE in {"sqlserver", "mssql", "microsoft-sql-server"}:
        try:
            import pyodbc  # type: ignore
        except ImportError as exc:
            raise RuntimeError("SQL Server mode needs pyodbc. Install requirements.txt and the Microsoft ODBC Driver 18.") from exc
        return pyodbc.connect(sqlserver_connection_string(), timeout=5)
    raise RuntimeError("Unsupported YATRAFLOW_DB_ENGINE. Use 'sqlite' or 'sqlserver'.")


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        if cur.description is None:
            return []
        columns = [col[0].lower() for col in cur.description]
        result = []
        for row in cur.fetchall():
            result.append({column: value for column, value in zip(columns, row)})
        return result
    finally:
        conn.close()


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    conn = db_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        count = cur.rowcount
        conn.commit()
        return count
    finally:
        conn.close()


def transaction(statements: list[tuple[str, tuple[Any, ...]]]) -> list[int]:
    conn = db_connect()
    counts: list[int] = []
    try:
        cur = conn.cursor()
        for sql, params in statements:
            cur.execute(sql, params)
            counts.append(cur.rowcount)
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def json_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()} for row in rows]


def site_id_from(params: dict[str, list[str]]) -> int:
    try:
        return max(1, int(params.get("site_id", ["1"])[0]))
    except (ValueError, TypeError):
        return 1


def all_sites() -> list[dict[str, Any]]:
    rows = query("SELECT id, name, city FROM sites ORDER BY id")
    return [{"id": as_int(row["id"]), "name": row["name"], "city": row["city"], "full_name": f"{row['name']}, {row['city']}"} for row in rows]


def get_zones(site_id: int) -> list[dict[str, Any]]:
    rows = query("SELECT id, site_id, name, current_visitors, density_percent, level FROM zones WHERE site_id=? ORDER BY id", (site_id,))
    return json_rows(rows)


def get_incidents(site_id: int) -> list[dict[str, Any]]:
    rows = query("SELECT id, site_id, type, priority, title, visitor_name, zone_name, description, status, assigned_to, reported_minutes, created_at FROM incidents WHERE site_id=?", (site_id,))
    return sorted(json_rows(rows), key=lambda row: (as_int(row.get("reported_minutes"), 99999), str(row.get("created_at", ""))))


def is_open(row: dict[str, Any]) -> bool:
    return str(row.get("status", "")).strip().lower() not in {"resolved", "closed", "complete", "completed"}


def get_dashboard(site_id: int) -> dict[str, Any]:
    site_rows = query("SELECT id, name, city, capacity, people_inside, checked_in_today, average_dwell_minutes, people_change_percent, predicted_tomorrow, peak_window, updated_at FROM sites WHERE id=?", (site_id,))
    if not site_rows:
        raise ValueError(f"Site {site_id} was not found")
    site = site_rows[0]
    incidents = get_incidents(site_id)
    open_incidents = [row for row in incidents if is_open(row)]
    rescues = [row for row in open_incidents if str(row.get("type", "")).upper() == "SOS"]
    unassigned = [row for row in rescues if not row.get("assigned_to")]
    capacity = as_int(site.get("capacity"), 1)
    people = as_int(site.get("people_inside"))
    alerts = query("SELECT id, category, title, description, created_at FROM alerts WHERE site_id=?", (site_id,))
    alerts = sorted(json_rows(alerts), key=lambda row: str(row.get("created_at", "")), reverse=True)
    return {
        "site": {**site, "id": as_int(site["id"]), "full_name": f"{site['name']}, {site['city']}"},
        "metrics": {
            "people_inside": people,
            "checked_in_today": as_int(site.get("checked_in_today")),
            "average_dwell_minutes": as_int(site.get("average_dwell_minutes"), 120),
            "people_change_percent": float(site.get("people_change_percent") or 0),
            "capacity": capacity,
            "capacity_used": round((people / capacity) * 100) if capacity else 0,
            "active_rescue_requests": len(rescues),
            "unassigned_rescue_requests": len(unassigned),
            "open_incidents": len(open_incidents),
            "predicted_tomorrow": as_int(site.get("predicted_tomorrow")),
            "peak_window": site.get("peak_window", ""),
        },
        "zones": get_zones(site_id),
        "incidents": open_incidents[:3],
        "alerts": alerts[:5],
        "updated_at": site.get("updated_at"),
        "as_of": app_today().isoformat(),
    }


def get_visitors(site_id: int, search: str = "") -> list[dict[str, Any]]:
    rows = json_rows(query("SELECT id, site_id, full_name, pass_id, zone_name, entry_time, pass_type, status FROM visitors WHERE site_id=?", (site_id,)))
    if search:
        needle = search.lower()
        rows = [row for row in rows if needle in " ".join(str(value or "") for value in row.values()).lower()]
    return rows


def get_analytics(site_id: int, requested_days: int = 14) -> dict[str, Any]:
    days = requested_days if requested_days in {14, 30, 90} else 14
    today = app_today()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    rows = json_rows(query(
        "SELECT date_key, label, series, visitor_count FROM footfall WHERE site_id=? AND date_key>=? AND date_key<=? ORDER BY date_key",
        (site_id, start, end),
    ))
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        day_key = str(row["date_key"])
        entry = grouped.setdefault(day_key, {"date": day_key, "label": row["label"]})
        entry[str(row["series"])] = as_int(row.get("visitor_count"))
    dates = sorted(grouped)
    actual_index = max((index for index, key in enumerate(dates) if key <= today.isoformat()), default=0)
    averages_2024 = [grouped[key].get("avg_2024") for key in dates]
    averages_2025 = [grouped[key].get("avg_2025") for key in dates]
    actual = [grouped[key].get("actual_2026") for key in dates]
    forecast = [grouped[key].get("forecast_2026") for key in dates]
    # Connect the model forecast to the latest observed actual for a continuous forecast line.
    if dates and actual_index < len(forecast) and actual[actual_index] is not None:
        forecast[actual_index] = actual[actual_index]
    site_rows = query("SELECT checked_in_today, average_dwell_minutes, predicted_tomorrow, peak_window FROM sites WHERE id=?", (site_id,))
    summary = site_rows[0] if site_rows else {}
    return {
        "days": days,
        "summary": {"visitors_today": as_int(summary.get("checked_in_today")), "average_dwell_minutes": as_int(summary.get("average_dwell_minutes"), 120), "predicted_tomorrow": as_int(summary.get("predicted_tomorrow")), "peak_window": summary.get("peak_window", "")}, 
        "labels": [grouped[key]["label"] for key in dates],
        "date_keys": dates,
        "current_index": actual_index,
        "series": {"avg_2024": averages_2024, "avg_2025": averages_2025, "actual_2026": actual, "forecast_2026": forecast},
    }


def get_broadcasts(site_id: int) -> list[dict[str, Any]]:
    rows = json_rows(query("SELECT id, site_id, title, message, priority, audience, reached, created_at FROM broadcasts WHERE site_id=?", (site_id,)))
    return sorted(rows, key=lambda row: str(row.get("created_at", "")), reverse=True)


def get_health() -> dict[str, Any]:
    engine = "sqlserver" if DB_ENGINE in {"sqlserver", "mssql", "microsoft-sql-server"} else DB_ENGINE
    schema_checks = (
        "SELECT id,name,city,capacity,people_inside,checked_in_today,average_dwell_minutes,people_change_percent,predicted_tomorrow,peak_window,updated_at FROM sites WHERE 1=0",
        "SELECT id,site_id,name,current_visitors,density_percent,level FROM zones WHERE 1=0",
        "SELECT id,site_id,type,priority,title,visitor_name,zone_name,description,status,assigned_to,reported_minutes,created_at FROM incidents WHERE 1=0",
        "SELECT id,site_id,full_name,pass_id,zone_name,entry_time,pass_type,status FROM visitors WHERE 1=0",
        "SELECT site_id,date_key,label,series,visitor_count FROM footfall WHERE 1=0",
        "SELECT id,site_id,category,title,description,created_at FROM alerts WHERE 1=0",
        "SELECT id,site_id,full_name,email,role,access_scope,shift_status,last_active FROM admins WHERE 1=0",
        "SELECT id,site_id,title,message,priority,audience,reached,created_at FROM broadcasts WHERE 1=0",
        "SELECT id,site_id,entity_type,entity_id,action,actor,detail,created_at FROM audit_log WHERE 1=0",
    )
    if engine in {"sqlite", "sqlite3"}:
        try:
            for check in schema_checks:
                query(check)
            return {"ok": True, "connected": True, "engine": "sqlite", "display": "Local SQLite database", "database": SQLITE_PATH.name, "schema_ready": True}
        except Exception as exc:
            return {"ok": False, "connected": False, "engine": "sqlite", "display": "Local SQLite database", "schema_ready": False, "error": str(exc)}
    try:
        for check in schema_checks:
            query(check)
        server = os.getenv("SQLSERVER_SERVER", "SQL Server")
        database = os.getenv("SQLSERVER_DATABASE", "database")
        return {"ok": True, "connected": True, "engine": "sqlserver", "display": "Microsoft SQL Server", "database": database, "server": server, "schema_ready": True}
    except Exception as exc:
        return {"ok": False, "connected": False, "engine": "sqlserver", "display": "Microsoft SQL Server", "database": os.getenv("SQLSERVER_DATABASE", "not configured"), "schema_ready": False, "error": str(exc)}


def add_audit(site_id: int, entity_type: str, entity_id: str, action: str, detail: str) -> None:
    execute(
        "INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("AUD-" + uuid.uuid4().hex[:12].upper(), site_id, entity_type, entity_id, action, "Ava Desai", detail, now_iso()),
    )


def create_broadcast(payload: dict[str, Any]) -> dict[str, Any]:
    site_id = as_int(payload.get("site_id"), 1)
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("Message is required")
    if len(message) > 240:
        raise ValueError("Message must be 240 characters or fewer")
    priority = str(payload.get("priority", "Information")).strip()[:80] or "Information"
    audience = str(payload.get("audience", "All visitors on site")).strip()[:160] or "All visitors on site"
    title = str(payload.get("title", "")).strip()[:100] or (message[:72] + ("…" if len(message) > 72 else ""))
    site_rows = query("SELECT people_inside FROM sites WHERE id=?", (site_id,))
    if not site_rows:
        raise ValueError(f"Site {site_id} was not found")
    reached = as_int(site_rows[0].get("people_inside")) if "all visitors" in audience.lower() else 0
    broadcast_id = "BR-" + app_today().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:5].upper()
    created_at = now_iso()
    execute(
        "INSERT INTO broadcasts (id,site_id,title,message,priority,audience,reached,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (broadcast_id, site_id, title, message, priority, audience, reached, created_at),
    )
    add_audit(site_id, "broadcast", broadcast_id, "created", f"{priority}: {title}")
    return {"id": broadcast_id, "site_id": site_id, "title": title, "message": message, "priority": priority, "audience": audience, "reached": reached, "created_at": created_at}


def mutate_incident(incident_id: str, site_id: int, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = query("SELECT id, type, status, assigned_to, zone_name FROM incidents WHERE id=? AND site_id=?", (incident_id, site_id))
    if not rows:
        raise LookupError("Incident not found for this site")
    if action == "dispatch":
        assigned_to = str(payload.get("assigned_to", "Medical team 02"))[:100]
        new_status = "Responding"
        detail = f"Assigned to {assigned_to}"
    elif action == "assign":
        assigned_to = str(payload.get("assigned_to", "Facilities team"))[:100]
        new_status = "Assigned"
        detail = f"Assigned to {assigned_to}"
    elif action == "call":
        add_audit(site_id, "incident", incident_id, "call_requested", "Visitor contact requested by control room")
        return {**rows[0], "call_requested": True}
    else:
        raise ValueError("Unsupported incident action")
    execute("UPDATE incidents SET status=?, assigned_to=? WHERE id=? AND site_id=?", (new_status, assigned_to, incident_id, site_id))
    add_audit(site_id, "incident", incident_id, action, detail)
    updated = query("SELECT id, site_id, type, priority, title, visitor_name, zone_name, description, status, assigned_to, reported_minutes, created_at FROM incidents WHERE id=? AND site_id=?", (incident_id, site_id))
    return json_rows(updated)[0]


def create_admin(payload: dict[str, Any]) -> dict[str, Any]:
    site_id = as_int(payload.get("site_id"), 1)
    full_name = str(payload.get("full_name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    role = str(payload.get("role", "Response coordinator")).strip()
    if not full_name or not email or "@" not in email:
        raise ValueError("A name and valid work email are required")
    current = query("SELECT id FROM sites WHERE id=?", (site_id,))
    if not current:
        raise ValueError(f"Site {site_id} was not found")
    admin_id = "ADM-" + uuid.uuid4().hex[:8].upper()
    scopes = {"Site administrator": "Full site access", "Response coordinator": "Incidents · alerts", "Visitor services": "Visitors · reports", "Security supervisor": "Security · live map"}
    scope = scopes.get(role, "Limited site access")
    execute(
        "INSERT INTO admins (id,site_id,full_name,email,role,access_scope,shift_status,last_active) VALUES (?,?,?,?,?,?,?,?)",
        (admin_id, site_id, full_name, email, role, scope, "Pending invite", "Invite sent just now"),
    )
    add_audit(site_id, "admin", admin_id, "invited", f"{full_name} <{email}> · {role}")
    return {"id": admin_id, "site_id": site_id, "full_name": full_name, "email": email, "role": role, "access_scope": scope, "shift_status": "Pending invite", "last_active": "Invite sent just now"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def copyfile(self, source: Any, outputfile: Any) -> None:
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            # Preview clients can cancel a static-file request while navigating.
            pass

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()
        params = parse_qs(parsed.query)
        route = parsed.path.rstrip("/")
        if route == "/api/health":
            health = get_health()
            return self.send_json(health, 200 if health.get("connected") else 503)
        try:
            if route == "/api/sites":
                return self.send_json({"items": all_sites()})
            site_id = site_id_from(params)
            if route == "/api/dashboard":
                return self.send_json(get_dashboard(site_id))
            if route == "/api/zones":
                return self.send_json({"items": get_zones(site_id)})
            if route == "/api/incidents":
                return self.send_json({"items": get_incidents(site_id)})
            if route == "/api/visitors":
                return self.send_json({"items": get_visitors(site_id, params.get("q", [""])[0])})
            if route == "/api/analytics":
                days = as_int(params.get("days", ["14"])[0], 14)
                return self.send_json(get_analytics(site_id, days))
            if route == "/api/alerts":
                rows = query("SELECT id,site_id,category,title,description,created_at FROM alerts WHERE site_id=?", (site_id,))
                rows = sorted(json_rows(rows), key=lambda row: str(row.get("created_at", "")), reverse=True)
                return self.send_json({"items": rows})
            if route == "/api/admins":
                rows = query("SELECT id,site_id,full_name,email,role,access_scope,shift_status,last_active FROM admins WHERE site_id=?", (site_id,))
                return self.send_json({"items": json_rows(rows)})
            if route == "/api/broadcasts":
                return self.send_json({"items": get_broadcasts(site_id)})
            if route == "/api/audit":
                rows = query("SELECT id,site_id,entity_type,entity_id,action,actor,detail,created_at FROM audit_log WHERE site_id=?", (site_id,))
                rows = sorted(json_rows(rows), key=lambda row: str(row.get("created_at", "")), reverse=True)
                return self.send_json({"items": rows[:100]})
            return self.send_json({"error": "API route not found"}, 404)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 404)
        except Exception as exc:
            sys.stderr.write(f"API GET {route} failed: {exc}\n")
            return self.send_json({"error": "Database request failed", "detail": str(exc)}, 503)

    def do_POST(self) -> None:
        route = urlparse(self.path).path.rstrip("/")
        try:
            payload = self.read_json()
            site_id = as_int(payload.get("site_id"), 1)
            if route == "/api/broadcasts":
                item = create_broadcast(payload)
                return self.send_json({"ok": True, "item": item}, 201)
            if route == "/api/admins":
                item = create_admin(payload)
                return self.send_json({"ok": True, "item": item}, 201)
            if route == "/api/alerts/notify":
                category = str(payload.get("category", "Security"))[:80]
                detail = str(payload.get("message", f"{category} notification sent"))[:240]
                add_audit(site_id, "notification", "SITE", "notify", detail)
                return self.send_json({"ok": True, "message": detail}, 201)
            match = re.fullmatch(r"/api/incidents/([^/]+)/(dispatch|assign|call)", route)
            if match:
                item = mutate_incident(match.group(1), site_id, match.group(2), payload)
                return self.send_json({"ok": True, "item": item})
            return self.send_json({"error": "API route not found"}, 404)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except LookupError as exc:
            return self.send_json({"error": str(exc)}, 404)
        except Exception as exc:
            sys.stderr.write(f"API POST {route} failed: {exc}\n")
            return self.send_json({"error": "Database request failed", "detail": str(exc)}, 503)


def main() -> None:
    if DB_ENGINE in {"sqlite", "sqlite3"}:
        initialize_sqlite()
        print(f"YatraFlow API serving on 0.0.0.0:{PORT} · SQLite · {SQLITE_PATH}")
    else:
        print(f"YatraFlow API serving on 0.0.0.0:{PORT} · {DB_ENGINE} · checking database on request")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping YatraFlow API")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
