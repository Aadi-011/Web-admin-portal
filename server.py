#!/usr/bin/env python3
"""YatraFlow admin API with session auth/RBAC and SQL-backed operations.

This repository has no YatraFlow mobile application or production identity/notification
service. Local SQLite is explicitly demo data. SQL Server and a keyed mobile-ingestion
contract are available for integration when configured by the deploying team.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import backend_core as core

BASE_DIR = Path(__file__).resolve().parent
PORT = int(os.getenv("PORT", str(core.PORT)))
COOKIE_NAME = "yatraflow_session"
SESSION_HOURS = 8
ROLES = {"super_admin", "site_admin", "emergency_operator", "analytics_viewer"}
ROLE_LABELS = {
    "super_admin": "Super Admin", "site_admin": "Site Admin",
    "emergency_operator": "Emergency Operator", "analytics_viewer": "Analytics Viewer",
}
SOS_TRANSITIONS = {
    "Submitted": {"Awaiting Acknowledgement", "Cancelled", "Failed"},
    "Awaiting Acknowledgement": {"Acknowledged", "Cancelled", "Failed"},
    "Acknowledged": {"Assigned", "Cancelled", "Failed"},
    "Assigned": {"Resolved", "Cancelled", "Failed"},
    "Assistance Dispatched": {"Resolved", "Failed"}, 
    "Resolved": set(), "Cancelled": set(), "Failed": set(),
}
LOGIN_ATTEMPTS: dict[str, tuple[float, int]] = {}


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def plain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in rows]


def db_query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return core.query(sql, params)


def db_execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    return core.execute(sql, params)


def db_transaction(statements: list[tuple[str, tuple[Any, ...]]], expected_rows: dict[int, int] | None = None) -> list[int]:
    conn=core.db_connect(); counts: list[int]=[]
    try:
        cur=conn.cursor()
        for index,(sql,params) in enumerate(statements):
            cur.execute(sql,params); count=cur.rowcount; counts.append(count)
            if expected_rows and index in expected_rows and count!=expected_rows[index]:
                raise APIError(409,"The record changed during this request; reload and try again")
        conn.commit(); return counts
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def audit(site_id: int, entity_type: str, entity_id: str, action: str, detail: str, actor: str) -> None:
    db_execute(
        "INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("AUD-" + uuid.uuid4().hex[:12].upper(), site_id, entity_type, entity_id, action, actor[:160], detail[:1000], utc_iso()),
    )


def add_history(incident_id: str, site_id: int, old_status: str | None, new_status: str, actor_id: str, note: str = "") -> tuple[str, tuple[Any, ...]]:
    return (
        "INSERT INTO incident_status_history (id,incident_id,site_id,old_status,new_status,actor_admin_id,note,created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("ISH-" + uuid.uuid4().hex[:12].upper(), incident_id, site_id, old_status, new_status, actor_id, note[:1000], utc_iso()),
    )


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def data_mode() -> str:
    # SQLite is the standalone development store and must never be relabelled by
    # an environment flag. Only the conservative demo label may override SQL metadata.
    if os.getenv("YATRAFLOW_DATA_MODE", "").strip().lower() == "demo":
        return "demo"
    if core.DB_ENGINE in {"sqlite", "sqlite3"}:
        return "demo"
    try:
        rows = db_query("SELECT meta_value FROM app_metadata WHERE meta_key=?", ("data_mode",))
        stored = str(rows[0]["meta_value"]).strip().lower() if rows else "configured"
        return "demo" if stored == "demo" else "configured"
    except Exception:
        return "configured"


def row_to_api_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")), "site_id": as_int(row.get("site_id")),
        "full_name": row.get("full_name") or "Administrator", "email": row.get("email") or "",
        "role_key": row.get("role_key") or "site_admin",
        "role": ROLE_LABELS.get(str(row.get("role_key")), str(row.get("role") or "Site Admin")),
        "active": bool(row.get("active", 1)),
    }


def query_user_by_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    rows = db_query(
        "SELECT a.id,a.site_id,a.full_name,a.email,a.role,a.role_key,a.active,a.last_login,s.expires_at "
        "FROM admin_sessions s JOIN admins a ON a.id=s.admin_id WHERE s.token_hash=?",
        (token_hash,),
    )
    if not rows:
        return None
    row = rows[0]
    expiry = parse_dt(row.get("expires_at"))
    if not bool(row.get("active", 1)) or not expiry or expiry <= utc_now():
        db_execute("DELETE FROM admin_sessions WHERE token_hash=?", (token_hash,))
        return None
    user = row_to_api_user(row)
    user["last_login"] = row.get("last_login")
    user["_token_hash"] = token_hash
    return user


def user_from_handler(handler: SimpleHTTPRequestHandler) -> dict[str, Any] | None:
    jar = SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie", ""))
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    return query_user_by_session(morsel.value if morsel else None)


def require_permission(user: dict[str, Any], permission: str) -> None:
    role = user.get("role_key")
    if role == "super_admin" or role == "site_admin":
        return
    allowed = {
        "emergency_operator": {"read", "operate", "view_pii"},
        "analytics_viewer": {"read_analytics"},
    }.get(str(role), set())
    if permission not in allowed:
        raise APIError(403, "Your account does not have permission for this action")


def scoped_site(user: dict[str, Any], requested: Any = None) -> int:
    assigned = as_int(user.get("site_id"), 1)
    if requested not in (None, ""):
        try: wanted=int(str(requested))
        except (TypeError,ValueError): raise APIError(400,"site_id must be a positive integer")
    else:
        wanted=assigned
    if wanted<1: raise APIError(400,"site_id must be a positive integer")
    if user.get("role_key") == "super_admin":
        if not db_query("SELECT id FROM sites WHERE id=?", (wanted,)):
            raise APIError(404, "Site was not found")
        return wanted
    if wanted != assigned:
        raise APIError(403, "This administrator is not assigned to that site")
    return assigned


def get_sites_for_user(user: dict[str, Any]) -> list[dict[str, Any]]:
    if user.get("role_key") == "super_admin":
        rows = db_query("SELECT id,name,city,capacity FROM sites ORDER BY id")
    else:
        rows = db_query("SELECT id,name,city,capacity FROM sites WHERE id=?", (user["site_id"],))
    return [{**r, "id": as_int(r["id"]), "full_name": f"{r['name']}, {r['city']}"} for r in rows]


def find_forecast(site_id: int, forecast_date: str) -> dict[str, Any] | None:
    rows = db_query("SELECT id,site_id,forecast_date,period_start,period_end,predicted_visitors,model_name,model_version,confidence,generated_at,source FROM crowd_forecasts WHERE site_id=? AND forecast_date=?", (site_id, forecast_date))
    if not rows:
        return None
    return sorted(plain(rows), key=lambda r: str(r.get("generated_at", "")), reverse=True)[0]


def get_zones(site_id: int) -> list[dict[str, Any]]:
    rows = plain(db_query("SELECT id,site_id,name,current_visitors,capacity,density_percent,level,updated_at FROM zones WHERE site_id=? ORDER BY id", (site_id,)))
    latest: dict[str, dict[str, Any]] = {}
    if data_mode() != "demo":
        observations = plain(db_query("SELECT id,zone_name,visitor_count,capacity,density_percent,source,observed_at FROM crowd_observations WHERE site_id=?", (site_id,)))
        for item in sorted(observations, key=lambda r: str(r.get("observed_at", ""))):
            latest[str(item["zone_name"])] = item
    result = []
    for row in rows:
        name = str(row["name"])
        obs = latest.get(name)
        if data_mode() != "demo":
            if obs:
                row["current_visitors"] = as_int(obs.get("visitor_count"))
                row["capacity"] = as_int(obs.get("capacity"), as_int(row.get("capacity")))
                row["density_percent"] = round(float(obs.get("density_percent") or 0))
                row["updated_at"] = obs.get("observed_at")
                row["source"] = obs.get("source")
            else:
                row["current_visitors"] = None
                row["density_percent"] = None
                row["level"] = "No observation"
                row["source"] = "No crowd observation received"
        else:
            row["source"] = "Development sample aggregate"
        density = row.get("density_percent")
        row["warning_status"] = "No observation" if density is None else "Critical" if float(density) >= 90 else "High" if float(density) >= 75 else "Moderate" if float(density) >= 50 else "Low"
        row["capacity"] = as_int(row.get("capacity")) or None
        result.append(row)
    return result


def get_zone_detail(site_id: int, zone_id: int, user: dict[str, Any]) -> dict[str, Any]:
    rows = db_query("SELECT id,site_id,name,current_visitors,capacity,density_percent,level,updated_at FROM zones WHERE id=? AND site_id=?", (zone_id, site_id))
    if not rows:
        raise APIError(404, "Zone not found")
    zone = get_zones(site_id)
    item = next((z for z in zone if as_int(z.get("id")) == zone_id), plain(rows)[0])
    history = plain(db_query("SELECT visitor_count,density_percent,source,observed_at FROM crowd_observations WHERE site_id=? AND zone_name=?", (site_id, rows[0]["name"])))
    history = sorted(history, key=lambda x: str(x.get("observed_at", "")), reverse=True)[:12]
    incident_rows = get_incidents(site_id)
    warnings = [i for i in incident_rows if i.get("zone_name") == rows[0]["name"] and is_active(i)]
    item["recent_trend"] = list(reversed(history))
    item["active_warnings"] = warnings if user.get("role_key") in {"super_admin","site_admin","emergency_operator"} else []
    item["recommended_action"] = "Manage the active warning and follow the site response plan." if warnings else "Continue monitoring; no active incident is linked to this zone."
    return item


def is_active(item: dict[str, Any]) -> bool:
    return str(item.get("status", "")).lower() not in {"resolved", "cancelled", "failed", "closed", "complete", "completed"}


def get_incidents(site_id: int) -> list[dict[str, Any]]:
    rows = plain(db_query(
        "SELECT id,site_id,type,priority,title,visitor_name,visitor_id,zone_name,description,status,assigned_to,latitude,longitude,reported_minutes,created_at,updated_at "
        "FROM incidents WHERE site_id=?", (site_id,)
    ))
    if data_mode()!="demo":
        now=utc_now()
        for row in rows:
            created=parse_dt(row.get("created_at"))
            if created: row["reported_minutes"]=max(0,int((now-created).total_seconds()//60))
    return sorted(rows, key=lambda r: (as_int(r.get("reported_minutes"), 99999), str(r.get("created_at", ""))))


def get_incident_detail(site_id: int, incident_id: str, user: dict[str, Any]) -> dict[str, Any]:
    rows = plain(db_query(
        "SELECT id,site_id,type,priority,title,visitor_name,visitor_id,zone_name,description,status,assigned_to,contact_phone,latitude,longitude,resolved_at,updated_at,reported_minutes,created_at "
        "FROM incidents WHERE id=? AND site_id=?", (incident_id, site_id)
    ))
    if not rows:
        raise APIError(404, "Incident not found")
    item = rows[0]
    if data_mode()!="demo":
        created=parse_dt(item.get("created_at"))
        if created: item["reported_minutes"]=max(0,int((utc_now()-created).total_seconds()//60))
    if item.get("visitor_id"): 
        visitors = db_query("SELECT id,full_name,phone,pass_id FROM visitors WHERE id=? AND site_id=?", (item["visitor_id"], site_id))
        if visitors:
            item["visitor"] = {"id": visitors[0]["id"], "full_name": visitors[0]["full_name"], "pass_id": visitors[0]["pass_id"]}
            if user.get("role_key") in {"super_admin", "site_admin", "emergency_operator"}:
                item["visitor"]["phone"] = visitors[0].get("phone")
    if user.get("role_key") not in {"super_admin", "site_admin", "emergency_operator"}:
        item.pop("contact_phone", None); item.pop("latitude", None); item.pop("longitude", None); item.pop("visitor", None)
    item["status_history"] = plain(db_query("SELECT id,old_status,new_status,actor_admin_id,note,created_at FROM incident_status_history WHERE incident_id=? AND site_id=?", (incident_id, site_id)))
    item["notes"] = plain(db_query("SELECT id,author_admin_id,note,created_at FROM incident_notes WHERE incident_id=? AND site_id=?", (incident_id, site_id)))
    item["assignments"] = plain(db_query("SELECT id,responder,provider,request_status,external_reference,created_by,created_at,updated_at FROM emergency_assignments WHERE incident_id=? AND site_id=?", (incident_id, site_id)))
    return item


def is_active_incident(row: dict[str, Any]) -> bool:
    return is_active(row)


def sample_mode() -> bool:
    return data_mode() == "demo"


def get_dashboard(site_id: int, user: dict[str, Any]) -> dict[str, Any]:
    site_rows = db_query("SELECT id,name,city,capacity,people_inside,checked_in_today,average_dwell_minutes,people_change_percent,updated_at FROM sites WHERE id=?", (site_id,))
    if not site_rows:
        raise APIError(404, "Site not found")
    site = site_rows[0]
    mode = data_mode()
    today = core.app_today().isoformat()
    session_rows = plain(db_query("SELECT id,visitor_id,checked_in_at,checked_out_at,zone_name FROM visitor_sessions WHERE site_id=?", (site_id,)))
    obs = plain(db_query("SELECT zone_name,visitor_count,observed_at FROM crowd_observations WHERE site_id=?", (site_id,)))
    if mode == "demo":
        people_inside: int | None = as_int(site.get("people_inside"))
        checked_in_today: int | None = as_int(site.get("checked_in_today"))
        change_percent: float | None = float(site.get("people_change_percent") or 0)
        occupancy_source = "Development sample data"
    elif session_rows:
        now = utc_now()
        previous = now - timedelta(hours=1)
        active_now = 0
        active_previous = 0
        checkins_today = 0
        for session in session_rows:
            checkin = parse_dt(session.get("checked_in_at"))
            checkout = parse_dt(session.get("checked_out_at"))
            if not checkin:
                continue
            if checkin.astimezone(core.KOLKATA).date().isoformat() == today:
                checkins_today += 1
            if checkin <= now and (checkout is None or checkout > now):
                active_now += 1
            if checkin <= previous and (checkout is None or checkout > previous):
                active_previous += 1
        people_inside = active_now
        checked_in_today = checkins_today
        change_percent = ((active_now - active_previous) / active_previous * 100) if active_previous else None
        occupancy_source = "Visitor session records"
    elif obs:
        latest: dict[str, dict[str, Any]] = {}
        for observation in sorted(obs, key=lambda x: str(x.get("observed_at", ""))):
            latest[str(observation["zone_name"])] = observation
        people_inside = sum(as_int(o.get("visitor_count")) for o in latest.values())
        checked_in_today = as_int(site.get("checked_in_today")) if site.get("checked_in_today") is not None else None
        change_percent = None
        occupancy_source = "Aggregated crowd observations"
    elif site.get("people_inside") is not None:
        people_inside = as_int(site.get("people_inside"))
        checked_in_today = as_int(site.get("checked_in_today")) if site.get("checked_in_today") is not None else None
        change_percent = float(site.get("people_change_percent") or 0) if site.get("people_change_percent") is not None else None
        occupancy_source = "Site-level database snapshot"
    else:
        people_inside = None
        checked_in_today = None
        change_percent = None
        occupancy_source = "No occupancy source configured"
    capacity = as_int(site.get("capacity"))
    capacity_used = round(people_inside / capacity * 100) if people_inside is not None and capacity else None
    incidents = get_incidents(site_id)
    active_incidents = [i for i in incidents if is_active_incident(i)]
    rescues = [i for i in active_incidents if str(i.get("type", "")).upper() == "SOS"]
    unassigned = [i for i in rescues if not i.get("assigned_to")]
    forecast_date = (core.app_today() + timedelta(days=1)).isoformat()
    forecast = find_forecast(site_id, forecast_date)
    alerts = plain(db_query("SELECT id,category,title,description,created_at FROM alerts WHERE site_id=?", (site_id,)))
    alerts.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    zones = get_zones(site_id)
    exposed_incidents = active_incidents[:3]
    if user.get("role_key") == "analytics_viewer":
        exposed_incidents = []
    return {
        "site": {"id": as_int(site["id"]), "name": site["name"], "city": site["city"], "full_name": f"{site['name']}, {site['city']}", "capacity": capacity},
        "metrics": {
            "people_inside": people_inside, "checked_in_today": checked_in_today,
            "people_change_percent": change_percent, "capacity": capacity, "capacity_used": capacity_used,
            "active_rescue_requests": len(rescues), "unassigned_rescue_requests": len(unassigned),
            "open_incidents": len(active_incidents), "forecast_available": bool(forecast),
            "predicted_tomorrow": as_int(forecast.get("predicted_visitors")) if forecast else None,
            "peak_window": (f"{forecast.get('period_start') or ''}–{forecast.get('period_end') or ''}".strip("–")) if forecast and (forecast.get("period_start") or forecast.get("period_end")) else None,
            "forecast_model": forecast.get("model_name") if forecast else None,
            "forecast_updated_at": forecast.get("generated_at") if forecast else None,
            "average_dwell_minutes": (as_int(site.get("average_dwell_minutes")) if as_int(site.get("average_dwell_minutes")) > 0 else None),
            "occupancy_source": occupancy_source, "source_updated_at": site.get("updated_at"),
        },
        "zones": zones, "incidents": exposed_incidents, "alerts": alerts[:5],
        "updated_at": site.get("updated_at"), "as_of": core.app_today().isoformat(),
        "data_mode": mode, "refresh_interval_seconds": 30,
    }


def get_visitors(site_id: int, query_text: str = "", status: str = "", start_date: str = "", end_date: str = "", page: int = 1, page_size: int = 25, reveal_pii: bool = False, filter_kind: str = "") -> dict[str, Any]:
    if filter_kind not in {"", "all", "assistance", "vip"}: raise APIError(400,"Invalid visitor filter")
    rows = plain(db_query("SELECT id,site_id,full_name,phone,registered_at,pass_id,zone_name,entry_time,pass_type,status FROM visitors WHERE site_id=?", (site_id,)))
    sessions = plain(db_query("SELECT visitor_id,zone_name,checked_in_at,checked_out_at FROM visitor_sessions WHERE site_id=?", (site_id,)))
    incident_rows=db_query("SELECT visitor_id,status FROM incidents WHERE site_id=? AND visitor_id IS NOT NULL",(site_id,))
    terminal={"resolved","cancelled","failed","closed","complete","completed"}
    assistance_ids={str(r["visitor_id"]) for r in incident_rows if str(r.get("status","")).lower() not in terminal}
    active_by_visitor = {}
    history_by_visitor: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        history_by_visitor.setdefault(str(session["visitor_id"]), []).append(session)
        if not session.get("checked_out_at"):
            active_by_visitor[str(session["visitor_id"])] = session
    for visitor in rows:
        visitor_id=str(visitor["id"]); active = active_by_visitor.get(visitor_id)
        visitor_history=history_by_visitor.get(visitor_id,[])
        visitor["needs_assistance"]="assistance" in str(visitor.get("status","")).lower() or visitor_id in assistance_ids
        if active:
            visitor["status"] = "Checked in"
            visitor["zone_name"] = active.get("zone_name") or visitor.get("zone_name")
            visitor["checked_in_at"] = active.get("checked_in_at")
        elif visitor_history:
            visitor["status"] = "Checked out"
        visitor["session_count"] = len(visitor_history)
        if not reveal_pii:
            visitor.pop("phone", None)
    site_summary={"needs_assistance":sum(1 for r in rows if r.get("needs_assistance")),"vip_passes":sum(1 for r in rows if str(r.get("pass_type","")).lower()=="vip"),"visitor_records":len(rows)}
    if query_text:
        needle = query_text.strip().lower()
        rows = [r for r in rows if needle in " ".join(str(v or "") for v in r.values()).lower()]
    if status:
        rows = [r for r in rows if bool(r.get("needs_assistance"))] if status.lower() in {"assistance","needs assistance"} else [r for r in rows if str(r.get("status", "")).lower() == status.lower()]
    if filter_kind=="assistance": rows=[r for r in rows if r["needs_assistance"]]
    elif filter_kind=="vip": rows=[r for r in rows if str(r.get("pass_type","")).lower()=="vip"]
    if start_date:
        rows = [r for r in rows if str(r.get("registered_at") or "")[:10] >= start_date]
    if end_date:
        rows = [r for r in rows if str(r.get("registered_at") or "")[:10] <= end_date]
    total = len(rows)
    page = max(1, page); page_size = max(1, min(100, page_size))
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "page": page, "page_size": page_size, "total": total, "pages": max(1, (total + page_size - 1) // page_size), "summary":site_summary}


def get_analytics(site_id: int, requested_days: int = 14, aggregation: str = "daily") -> dict[str, Any]:
    days = requested_days if requested_days in {14, 30, 90} else 14
    aggregation = aggregation if aggregation in {"daily", "weekly", "monthly"} else "daily"
    today = core.app_today()
    start = (today - timedelta(days=days - 1)).isoformat()
    rows = plain(db_query("SELECT date_key,label,series,visitor_count FROM footfall WHERE site_id=? AND date_key>=? AND date_key<=? ORDER BY date_key", (site_id, start, today.isoformat())))
    buckets: dict[str, dict[str, Any]] = {}
    def bucket_for(day: date, label: str | None = None) -> tuple[str, str]:
        if aggregation == "weekly":
            bucket_day = day - timedelta(days=day.weekday())
            return bucket_day.isoformat(), bucket_day.strftime("%-d %b") if sys.platform != "win32" else bucket_day.strftime("%d %b").lstrip("0")
        if aggregation == "monthly":
            return day.strftime("%Y-%m"), day.strftime("%b %Y")
        default_label = day.strftime("%-d %b") if sys.platform != "win32" else day.strftime("%d %b").lstrip("0")
        return day.isoformat(), label or default_label
    for row in rows:
        day = date.fromisoformat(str(row["date_key"])[:10])
        bucket_key, label = bucket_for(day, str(row.get("label") or ""))
        entry = buckets.setdefault(bucket_key, {"date": bucket_key, "label": label})
        series = str(row.get("series"))
        if series not in {"avg_2024", "avg_2025", "actual_2026"}:
            continue
        entry[series] = as_int(entry.get(series)) + as_int(row.get("visitor_count"))
    forecast_rows = plain(db_query("SELECT forecast_date,predicted_visitors,model_name,generated_at,period_start,period_end,confidence,source FROM crowd_forecasts WHERE site_id=?", (site_id,)))
    forecasts_by_date: dict[str, dict[str, Any]] = {}
    for forecast_row in sorted(forecast_rows, key=lambda r: str(r.get("generated_at", ""))):
        forecast_key = str(forecast_row.get("forecast_date", ""))[:10]
        try:
            forecast_day = date.fromisoformat(forecast_key)
        except ValueError:
            continue
        if today <= forecast_day <= today + timedelta(days=days):
            forecasts_by_date[forecast_key] = forecast_row
    for forecast_key, forecast_row in forecasts_by_date.items():
        forecast_day = date.fromisoformat(forecast_key)
        bucket_key, label = bucket_for(forecast_day)
        entry = buckets.setdefault(bucket_key, {"date": bucket_key, "label": label})
        entry["forecast_2026"] = as_int(entry.get("forecast_2026")) + as_int(forecast_row.get("predicted_visitors"))
    keys = sorted(buckets)
    labels = [buckets[k]["label"] for k in keys]
    actual = [buckets[k].get("actual_2026") for k in keys]
    avg24 = [buckets[k].get("avg_2024") for k in keys]
    avg25 = [buckets[k].get("avg_2025") for k in keys]
    prediction = [buckets[k].get("forecast_2026") for k in keys]
    today_bucket, _ = bucket_for(today)
    current_index = keys.index(today_bucket) if today_bucket in keys else max(0, len(keys) - 1)
    model = find_forecast(site_id, (today + timedelta(days=1)).isoformat())
    summary_site = db_query("SELECT checked_in_today,average_dwell_minutes FROM sites WHERE id=?", (site_id,))
    summary_site = summary_site[0] if summary_site else {}
    return {
        "days": days, "aggregation": aggregation, "labels": labels, "date_keys": keys,
        "current_index": current_index,
        "summary": {"visitors_today": as_int(summary_site.get("checked_in_today")) if summary_site.get("checked_in_today") is not None else None,
                    "average_dwell_minutes": as_int(summary_site.get("average_dwell_minutes")) if as_int(summary_site.get("average_dwell_minutes")) > 0 else None,
                    "predicted_tomorrow": as_int(model.get("predicted_visitors")) if model else None,
                    "peak_window": (f"{model.get('period_start') or ''}–{model.get('period_end') or ''}".strip("–")) if model and (model.get('period_start') or model.get('period_end')) else None,
                    "forecast_available": bool(model)},
        "forecast": {"available": bool(model), "model_name": model.get("model_name") if model else None,
                     "generated_at": model.get("generated_at") if model else None,
                     "source": model.get("source") if model else None},
        "series": {"avg_2024": avg24, "avg_2025": avg25, "actual_2026": actual, "forecast_2026": prediction},
        "data_mode": data_mode(),
    }


def get_broadcasts(site_id: int) -> list[dict[str, Any]]:
    rows = plain(db_query("SELECT id,site_id,title,message,category,zone_name,priority,audience,delivery_status,reached,created_at FROM broadcasts WHERE site_id=?", (site_id,)))
    return sorted(rows, key=lambda r: str(r.get("created_at", "")), reverse=True)


def get_health() -> dict[str, Any]:
    engine = "sqlserver" if core.DB_ENGINE in {"sqlserver", "mssql", "microsoft-sql-server"} else "sqlite"
    checks = [
        "SELECT id,name,city,capacity FROM sites WHERE 1=0", "SELECT id,site_id,name,capacity FROM zones WHERE 1=0",
        "SELECT id,site_id,type,status,visitor_id,latitude,longitude FROM incidents WHERE 1=0",
        "SELECT id,site_id,full_name,phone FROM visitors WHERE 1=0", "SELECT token_hash,admin_id,expires_at FROM admin_sessions WHERE 1=0",
        "SELECT id,visitor_id,site_id,checked_in_at,checked_out_at FROM visitor_sessions WHERE 1=0",
        "SELECT id,incident_id,new_status FROM incident_status_history WHERE 1=0",
        "SELECT id,site_id,zone_name,visitor_count,observed_at FROM crowd_observations WHERE 1=0",
        "SELECT id,site_id,forecast_date,model_name,generated_at FROM crowd_forecasts WHERE 1=0",
        "SELECT id,site_id,delivery_status FROM notifications WHERE 1=0", "SELECT id,site_id,password_hash,role_key,active FROM admins WHERE 1=0",
        "SELECT id,site_id,delivery_status FROM broadcasts WHERE 1=0", "SELECT id,site_id,entity_id FROM audit_log WHERE 1=0",
        "SELECT meta_key,meta_value FROM app_metadata WHERE 1=0",
    ]
    try:
        for check in checks: db_query(check)
        mode = data_mode()
        forecast_count = len(db_query("SELECT id FROM crowd_forecasts WHERE forecast_date>?", (core.app_today().isoformat(),)))
        return {"ok": True, "connected": True, "engine": engine,
                "display": "Local SQLite · development sample" if engine == "sqlite" else "Microsoft SQL Server",
                "database": core.SQLITE_PATH.name if engine == "sqlite" else os.getenv("SQLSERVER_DATABASE", "configured database"),
                "schema_ready": True, "mode": mode, "sample_data": mode == "demo",
                "mobile_api_key_configured": bool(os.getenv("YATRAFLOW_MOBILE_API_KEY")),
                "push_delivery_available": False, "emergency_dispatch_available": False,
                "forecast_available": forecast_count > 0, "refresh_interval_seconds": 30}
    except Exception:
        return {"ok": False, "connected": False, "engine": engine, "schema_ready": False,
                "mode": "unavailable", "error": "Database unavailable or required schema is missing", "sample_data": engine == "sqlite"}


def bootstrap_sqlserver_admin() -> None:
    email = os.getenv("YATRAFLOW_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("YATRAFLOW_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email and not password:
        print("SQL Server bootstrap admin not configured; set YATRAFLOW_BOOTSTRAP_ADMIN_EMAIL/PASSWORD in .env")
        return
    if not email or len(password) < 12:
        raise RuntimeError("SQL Server bootstrap credentials require an email and a 12+ character password")
    existing = db_query("SELECT id FROM admins WHERE LOWER(email)=LOWER(?)", (email,))
    if existing:
        return
    site_id = int(os.getenv("YATRAFLOW_BOOTSTRAP_SITE_ID", "1"))
    name = os.getenv("YATRAFLOW_BOOTSTRAP_ADMIN_NAME", "YatraFlow Administrator").strip()
    role = os.getenv("YATRAFLOW_BOOTSTRAP_ADMIN_ROLE", "super_admin").strip().lower()
    if role not in ROLES:
        raise RuntimeError("YATRAFLOW_BOOTSTRAP_ADMIN_ROLE must be one of: " + ", ".join(sorted(ROLES)))
    if not db_query("SELECT id FROM sites WHERE id=?", (site_id,)):
        raise RuntimeError("YATRAFLOW_BOOTSTRAP_SITE_ID does not exist in sites")
    admin_id = "ADM-" + uuid.uuid4().hex[:10].upper()
    label = ROLE_LABELS[role]
    now = utc_iso()
    db_execute("INSERT INTO admins (id,site_id,full_name,email,role,role_key,access_scope,shift_status,active,password_hash,last_active,last_login,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (admin_id, site_id, name, email, label, role, "Full site access" if role in {"super_admin","site_admin"} else label, "On shift", 1, core.hash_password(password), "Now", now, now))
    print(f"Created bootstrap administrator {email} for site {site_id}.")


def get_visitor_detail(site_id: int, visitor_id: str, reveal_pii: bool) -> dict[str, Any]:
    rows = plain(db_query("SELECT id,site_id,full_name,phone,registered_at,pass_id,zone_name,entry_time,pass_type,status FROM visitors WHERE id=? AND site_id=?", (visitor_id, site_id)))
    if not rows: raise APIError(404, "Visitor not found")
    item = rows[0]
    sessions = plain(db_query("SELECT id,zone_name,checked_in_at,checked_out_at,source FROM visitor_sessions WHERE visitor_id=? AND site_id=?", (visitor_id, site_id)))
    if not reveal_pii: item.pop("phone", None)
    item["sessions"] = sorted(sessions, key=lambda r: str(r.get("checked_in_at", "")), reverse=True)
    return item


def get_admins(site_id: int) -> list[dict[str, Any]]:
    rows = plain(db_query("SELECT a.id,a.site_id,a.full_name,a.email,a.role,a.role_key,a.access_scope,a.shift_status,a.active,a.last_active,a.last_login,a.created_at,CASE WHEN a.password_hash IS NULL OR a.password_hash='' THEN 0 ELSE 1 END AS credential_ready,s.name AS site_name,s.city AS site_city FROM admins a LEFT JOIN sites s ON s.id=a.site_id WHERE a.site_id=?", (site_id,)))
    for row in rows:
        row["active"] = bool(row.get("active"))
        row["account_status"] = "Active" if row["active"] and bool(row.get("credential_ready")) else "Setup required" if row["active"] else "Inactive"
        row.pop("password_hash", None)
        row["role"] = ROLE_LABELS.get(str(row.get("role_key")), row.get("role"))
    return rows


def occupancy_from_sessions(site_id: int) -> dict[str, Any]:
    sessions = plain(db_query("SELECT checked_in_at,checked_out_at FROM visitor_sessions WHERE site_id=?", (site_id,)))
    if not sessions: return {"available": False, "people_inside": None, "checked_in_today": None, "change_percent": None}
    now = utc_now(); previous = now - timedelta(hours=1); today = core.app_today().isoformat()
    active_now = active_previous = checkins = 0
    for row in sessions:
        checked_in = parse_dt(row.get("checked_in_at")); checked_out = parse_dt(row.get("checked_out_at"))
        if not checked_in: continue
        if checked_in.astimezone(core.KOLKATA).date().isoformat() == today: checkins += 1
        if checked_in <= now and (checked_out is None or checked_out > now): active_now += 1
        if checked_in <= previous and (checked_out is None or checked_out > previous): active_previous += 1
    change = ((active_now-active_previous) / active_previous * 100) if active_previous else None
    return {"available": True, "people_inside": active_now, "checked_in_today": checkins, "change_percent": change}


def resolve_notification_delivery(site_id: int, zone_name: str | None, category: str, severity: str, title: str, message: str, audience: str, created_by: str) -> dict[str, Any]:
    notification_id = "NTF-" + uuid.uuid4().hex[:12].upper()
    created = utc_iso()
    delivery_status = "queued_for_mobile_polling" if os.getenv("YATRAFLOW_MOBILE_API_KEY") else "stored_pending_integration"
    db_transaction([
        ("INSERT INTO notifications (id,site_id,zone_name,category,severity,title,message,target_audience,delivery_status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
         (notification_id, site_id, zone_name, category, severity, title, message, audience, delivery_status, created_by, created)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",
         ("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"notification",notification_id,"created",created_by,f"{severity}: {title} · {delivery_status}",created)),
    ])
    return {"id": notification_id, "site_id": site_id, "zone_name": zone_name, "category": category, "severity": severity,
            "title": title, "message": message, "target_audience": audience, "delivery_status": delivery_status, "created_at": created,
            "push_delivered": False}


def create_broadcast(payload: dict[str, Any], site_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title", "")).strip()
    message = str(payload.get("message", "")).strip()
    category = str(payload.get("category", "Information")).strip()
    severity = str(payload.get("severity", "info")).strip().lower()
    zone_name = str(payload.get("zone_name", "")).strip() or None
    audience = str(payload.get("audience", "all_visitors")).strip()
    if not title or not message: raise APIError(400, "Title and message are required")
    if len(title) > 160 or len(message) > 500 or len(category)>60: raise APIError(400, "Title, category or message exceeds the allowed length")
    if severity not in {"info", "warning", "urgent", "critical"}: raise APIError(400, "Invalid severity")
    if audience not in {"all_visitors", "staff"} and not audience.startswith("zone:"):
        raise APIError(400, "Select a valid audience")
    if audience.startswith("zone:"):
        requested_zone=audience.split(":",1)[1].strip()
        if not zone_name: zone_name=requested_zone
        if requested_zone!=zone_name: raise APIError(400, "Audience and selected zone do not match")
        if not db_query("SELECT id FROM zones WHERE site_id=? AND name=?", (site_id, zone_name)): raise APIError(400, "Selected zone does not belong to this site")
    else:
        zone_name=None
    broadcast_id = "BR-" + uuid.uuid4().hex[:10].upper()
    created = utc_iso()
    delivery = "queued_for_mobile_polling" if os.getenv("YATRAFLOW_MOBILE_API_KEY") else "stored_pending_integration"
    notification_id = "NTF-" + uuid.uuid4().hex[:12].upper()
    statements = [
        ("INSERT INTO broadcasts (id,site_id,title,message,category,zone_name,priority,audience,delivery_status,reached,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
         (broadcast_id,site_id,title,message,category,zone_name,severity,audience,delivery,0,created)),
        ("INSERT INTO notifications (id,site_id,zone_name,category,severity,title,message,target_audience,delivery_status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
         (notification_id,site_id,zone_name,category,severity,title,message,audience,delivery,actor["id"],created)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",
         ("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"broadcast",broadcast_id,"created",actor["id"],f"{severity}: {title}",created)),
    ]
    db_transaction(statements)
    return {"id":broadcast_id,"notification_id":notification_id,"site_id":site_id,"title":title,"message":message,
            "category":category,"zone_name":zone_name,"severity":severity,"audience":audience,"delivery_status":delivery,
            "push_delivered":False,"created_at":created}


def update_incident_status(incident_id: str, site_id: int, target: str, actor: dict[str, Any], note: str = "") -> dict[str, Any]:
    rows = db_query("SELECT id,type,status,assigned_to FROM incidents WHERE id=? AND site_id=?", (incident_id, site_id))
    if not rows: raise APIError(404, "Incident not found")
    item = rows[0]; old = str(item["status"])
    if str(item["type"]).upper() == "SOS":
        allowed = SOS_TRANSITIONS.get(old, set())
        if target not in allowed:
            raise APIError(409, f"Invalid SOS transition: {old} → {target}")
        if target == "Assistance Dispatched":
            raise APIError(409, "Only a configured dispatch-provider callback can confirm assistance dispatched")
    else:
        allowed_report = {"Open": {"Assigned", "Resolved", "Cancelled"}, "Assigned": {"Resolved", "Cancelled"}, "In Review": {"Assigned", "Resolved", "Cancelled"}, "Monitoring": {"Assigned", "Resolved", "Cancelled"}}
        if target not in allowed_report.get(old, set()): raise APIError(409, f"Invalid report transition: {old} → {target}")
    if target in {"Resolved", "Cancelled", "Failed"} and not note.strip():
        raise APIError(400, "A factual note is required to resolve, cancel or fail an incident")
    now = utc_iso()
    resolved_at = now if target == "Resolved" else None
    db_transaction([
        ("UPDATE incidents SET status=?,resolved_at=?,updated_at=? WHERE id=? AND site_id=? AND status=?", (target,resolved_at,now,incident_id,site_id,old)),
        add_history(incident_id,site_id,old,target,actor["id"],note),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)", ("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"incident",incident_id,"status_changed",actor["id"],f"{old} -> {target}. {note}".strip(),now)),
    ], expected_rows={0:1})
    updated = db_query("SELECT id,status,assigned_to,updated_at FROM incidents WHERE id=? AND site_id=?", (incident_id,site_id))[0]
    return updated


def assign_incident(incident_id: str, site_id: int, responder: str, actor: dict[str, Any]) -> dict[str, Any]:
    responder = responder.strip()
    if not responder or len(responder) > 160: raise APIError(400, "Select or enter a responder")
    rows = db_query("SELECT id,type,status FROM incidents WHERE id=? AND site_id=?", (incident_id,site_id))
    if not rows: raise APIError(404,"Incident not found")
    old = str(rows[0]["status"]); kind = str(rows[0]["type"]).upper()
    if kind == "SOS" and old != "Acknowledged": raise APIError(409,"Acknowledge the SOS before assigning a responder")
    if kind != "SOS" and old not in {"Open","In Review"}: raise APIError(409,"Only open reports can be assigned")
    new_status = "Assigned"
    now = utc_iso(); assignment_id="ASN-"+uuid.uuid4().hex[:12].upper()
    db_transaction([
        ("UPDATE incidents SET assigned_to=?,status=?,updated_at=? WHERE id=? AND site_id=? AND status=?",(responder,new_status,now,incident_id,site_id,old)),
        add_history(incident_id,site_id,old,new_status,actor["id"],f"Assigned to {responder}"),
        ("INSERT INTO emergency_assignments (id,incident_id,site_id,responder,provider,request_status,external_reference,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(assignment_id,incident_id,site_id,responder,"local_operator_record","assigned_local",None,actor["id"],now,now)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"incident",incident_id,"assigned",actor["id"],f"Assignment stored locally: {responder}; no external contact",now)),
    ], expected_rows={0:1})
    return {"id":incident_id,"status":new_status,"assigned_to":responder,"assignment_id":assignment_id,"external_dispatch_sent":False}


def create_dispatch_request(incident_id: str, site_id: int, responder: str, service: str, actor: dict[str, Any]) -> dict[str, Any]:
    rows = db_query("SELECT id,type,status,zone_name FROM incidents WHERE id=? AND site_id=?", (incident_id,site_id))
    if not rows: raise APIError(404,"Incident not found")
    if str(rows[0]["type"]).upper() != "SOS": raise APIError(400,"Dispatch is only available for SOS records")
    if str(rows[0]["status"]) != "Assigned": raise APIError(409,"Acknowledge and assign the SOS before requesting dispatch")
    pending = db_query("SELECT id FROM emergency_assignments WHERE incident_id=? AND site_id=? AND request_status=?",(incident_id,site_id,"pending_external_integration"))
    if pending: raise APIError(409,"A dispatch request is already recorded as pending external integration")
    assignment_id = "DSP-" + uuid.uuid4().hex[:12].upper()
    now = utc_iso()
    provider = "not_configured"
    request_status = "pending_external_integration"
    db_transaction([
        ("INSERT INTO emergency_assignments (id,incident_id,site_id,responder,provider,request_status,external_reference,created_by,created_at,updated_at) SELECT ?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM emergency_assignments WHERE incident_id=? AND site_id=? AND request_status=?)",(assignment_id,incident_id,site_id,responder,provider,request_status,None,actor["id"],now,now,incident_id,site_id,request_status)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"incident",incident_id,"dispatch_request_recorded",actor["id"],f"{service} request saved; no external provider is connected",now)),
    ],expected_rows={0:1})
    return {"id":assignment_id,"incident_id":incident_id,"request_status":request_status,"external_action_sent":False,"message":"Request was recorded, not sent to emergency services; provider integration is not configured."}


def add_incident_note(incident_id: str, site_id: int, note: str, actor: dict[str, Any]) -> dict[str, Any]:
    note = note.strip()
    if not note or len(note) > 1000: raise APIError(400,"Note is required and must be at most 1000 characters")
    if not db_query("SELECT id FROM incidents WHERE id=? AND site_id=?",(incident_id,site_id)): raise APIError(404,"Incident not found")
    note_id = "NOTE-"+uuid.uuid4().hex[:12].upper(); created = utc_iso()
    db_transaction([
        ("INSERT INTO incident_notes (id,incident_id,site_id,author_admin_id,note,created_at) VALUES (?,?,?,?,?,?)",(note_id,incident_id,site_id,actor["id"],note,created)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"incident",incident_id,"note_added",actor["id"],note,created)),
    ])
    return {"id":note_id,"note":note,"created_at":created}


def create_admin(payload: dict[str, Any], site_id: int, actor: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("full_name", "")).strip(); email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", "")); role = str(payload.get("role_key", "analytics_viewer")).strip().lower()
    if not name or not email or "@" not in email: raise APIError(400,"Enter a full name and valid email")
    if len(name)>160 or len(email)>200: raise APIError(400,"Name or email exceeds the allowed length")
    if len(password) < 12: raise APIError(400,"Initial password must contain at least 12 characters")
    if role not in ROLES: raise APIError(400,"Select a valid role")
    if actor["role_key"] != "super_admin" and role not in {"emergency_operator","analytics_viewer"}:
        raise APIError(403,"Site admins may only provision Emergency Operator or Analytics Viewer roles")
    if db_query("SELECT id FROM admins WHERE LOWER(email)=LOWER(?)",(email,)): raise APIError(409,"An administrator with that email already exists")
    if not db_query("SELECT id FROM sites WHERE id=?",(site_id,)): raise APIError(404,"Site not found")
    admin_id="ADM-"+uuid.uuid4().hex[:10].upper(); now=utc_iso(); label=ROLE_LABELS[role]
    scope = "All sites" if role=="super_admin" else "Site operations" if role in {"site_admin","emergency_operator"} else "Read-only analytics"
    db_transaction([
        ("INSERT INTO admins (id,site_id,full_name,email,role,role_key,access_scope,shift_status,active,password_hash,last_active,last_login,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(admin_id,site_id,name,email,label,role,scope,"Off shift",1,core.hash_password(password),"Never",None,now)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"admin",admin_id,"created",actor["id"],f"{email} · {role}",now)),
    ])
    return {"id":admin_id,"site_id":site_id,"full_name":name,"email":email,"role":label,"role_key":role,"access_scope":scope,"active":True,"account_status":"Active"}


def update_admin(admin_id: str, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    if actor["role_key"] != "super_admin": raise APIError(403,"Only a Super Admin can change administrator roles or account status")
    rows=db_query("SELECT id,site_id,full_name,email,role_key,active FROM admins WHERE id=?",(admin_id,))
    if not rows: raise APIError(404,"Administrator not found")
    current=rows[0]
    if admin_id==actor["id"] and (not bool(payload.get("active",True)) or payload.get("role_key",current["role_key"])!="super_admin"):
        raise APIError(409,"You cannot deactivate or demote your own account")
    role=str(payload.get("role_key",current["role_key"])).lower(); active=1 if bool(payload.get("active",current["active"])) else 0
    if role not in ROLES: raise APIError(400,"Invalid role")
    if current["role_key"]=="super_admin" and (role!="super_admin" or not active):
        other=db_query("SELECT id FROM admins WHERE role_key=? AND active=1 AND id<>?",("super_admin",admin_id))
        if not other: raise APIError(409,"Cannot remove the last active Super Admin")
    label=ROLE_LABELS[role]; now=utc_iso()
    statements = [("UPDATE admins SET role=?,role_key=?,active=? WHERE id=?",(label,role,active,admin_id))]
    if not active:
        statements.append(("DELETE FROM admin_sessions WHERE admin_id=?",(admin_id,)))
    statements.append(("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),as_int(current["site_id"]),"admin",admin_id,"access_updated",actor["id"],f"role={role}; active={active}",now)))
    db_transaction(statements,expected_rows={0:1})
    return {"id":admin_id,"role":label,"role_key":role,"active":bool(active)}


def mobile_key_valid(handler: SimpleHTTPRequestHandler) -> bool:
    expected = os.getenv("YATRAFLOW_MOBILE_API_KEY", "")
    supplied = handler.headers.get("X-YatraFlow-Integration-Key", "")
    return bool(expected) and hmac.compare_digest(expected, supplied)


def require_mobile_key(handler: SimpleHTTPRequestHandler) -> None:
    if not os.getenv("YATRAFLOW_MOBILE_API_KEY"):
        raise APIError(503,"Mobile integration is not configured. Set YATRAFLOW_MOBILE_API_KEY server-side.")
    if not mobile_key_valid(handler): raise APIError(401,"Invalid mobile integration key")


def mobile_event_time(value: Any, field: str, optional: bool = False) -> str | None:
    if value in (None, ""):
        return None if optional else utc_iso()
    parsed=parse_dt(value)
    if not parsed: raise APIError(400,f"{field} must be a valid ISO-8601 timestamp")
    if parsed>utc_now()+timedelta(minutes=5): raise APIError(400,f"{field} cannot be more than five minutes in the future")
    return parsed.isoformat(timespec="seconds")


def mobile_ingest_visitor(payload: dict[str, Any]) -> dict[str, Any]:
    visitor_id=str(payload.get("external_id") or payload.get("visitor_id") or "").strip()
    name=str(payload.get("full_name") or payload.get("name") or "").strip()
    site_id=as_int(payload.get("site_id"),0)
    if not visitor_id or not name or not site_id: raise APIError(400,"external_id, full_name and site_id are required")
    if len(visitor_id)>80 or len(name)>160: raise APIError(400,"Visitor ID or name exceeds the allowed length")
    if not db_query("SELECT id FROM sites WHERE id=?",(site_id,)): raise APIError(404,"Site not found")
    zone=str(payload.get("zone_name") or "").strip()
    phone=str(payload.get("phone") or "").strip() or None
    pass_id=str(payload.get("pass_id") or visitor_id).strip()
    pass_type=str(payload.get("pass_type") or "Mobile").strip()
    entry_time=str(payload.get("entry_time") or "")
    if zone and len(zone)>120: raise APIError(400,"Zone name exceeds the allowed length")
    if zone and not db_query("SELECT id FROM zones WHERE site_id=? AND name=?",(site_id,zone)): raise APIError(400,"Visitor zone does not belong to this site")
    if phone and len(phone)>40 or len(pass_id)>80 or len(pass_type)>60 or len(entry_time)>40: raise APIError(400,"Visitor contact, pass type or entry field exceeds the allowed length")
    now=mobile_event_time(payload.get("registered_at"),"registered_at") or utc_iso()
    checkin=mobile_event_time(payload.get("checked_in_at"),"checked_in_at",optional=True)
    checkout=mobile_event_time(payload.get("checked_out_at"),"checked_out_at",optional=True)
    if checkout and not checkin: raise APIError(400,"checked_in_at is required when checked_out_at is supplied")
    if checkin and checkout and parse_dt(checkout)<parse_dt(checkin): raise APIError(400,"checked_out_at cannot precede checked_in_at")
    existing=db_query("SELECT id,site_id FROM visitors WHERE id=?",(visitor_id,))
    if existing and as_int(existing[0].get("site_id"))!=site_id: raise APIError(409,"Visitor ID is already linked to a different site")
    statements=[]; audit_id="AUD-"+uuid.uuid4().hex[:12].upper()
    if existing:
        statements.append(("UPDATE visitors SET full_name=?,phone=?,zone_name=? WHERE id=? AND site_id=?",(name,phone,zone,visitor_id,site_id)))
        profile_action="profile_updated_from_mobile"
    else:
        statements.append(("INSERT INTO visitors (id,site_id,full_name,phone,registered_at,pass_id,zone_name,entry_time,pass_type,status) VALUES (?,?,?,?,?,?,?,?,?,?)",(visitor_id,site_id,name,phone,now,pass_id,zone,entry_time,pass_type,"On site")))
        profile_action="profile_received_from_mobile"
    created=utc_iso()
    statements.append(("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",(audit_id,site_id,"visitor",visitor_id,profile_action,"mobile_integration","Visitor profile synchronized; contact fields omitted from audit",created)))
    if checkin:
        session_id=str(payload.get("session_id") or "VS-"+uuid.uuid4().hex[:12].upper()).strip()
        if not session_id or len(session_id)>120: raise APIError(400,"session_id is invalid or too long")
        prior_session=db_query("SELECT id,site_id,visitor_id FROM visitor_sessions WHERE external_id=?",(session_id,))
        if prior_session and (as_int(prior_session[0].get("site_id"))!=site_id or str(prior_session[0].get("visitor_id"))!=visitor_id):
            raise APIError(409,"Session ID is already linked to a different visitor or site")
        if not prior_session:
            statements.append(("INSERT INTO visitor_sessions (id,external_id,visitor_id,site_id,zone_name,checked_in_at,checked_out_at,source) VALUES (?,?,?,?,?,?,?,?)",(session_id,session_id,visitor_id,site_id,zone,checkin,checkout,"mobile")))
            statements.append(("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"visitor_session",session_id,"checked_in","mobile_integration",f"Visitor {visitor_id} checked in; zone {zone or 'unspecified'}",created)))
    expected={0:1} if existing else None
    db_transaction(statements,expected_rows=expected)
    return {"ok":True,"visitor_id":visitor_id,"site_id":site_id,"updated":bool(existing)}


def mobile_ingest_incident(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    external_id=str(payload.get("external_id") or payload.get("id") or "").strip()
    site_id=as_int(payload.get("site_id"),0)
    if not site_id: raise APIError(400,"site_id is required")
    if not db_query("SELECT id FROM sites WHERE id=?",(site_id,)): raise APIError(404,"Site not found")
    if external_id and len(external_id)>80: raise APIError(400,"External incident ID exceeds the allowed length")
    if external_id:
        duplicate=db_query("SELECT id,site_id FROM incidents WHERE id=?",(external_id,))
        if duplicate:
            if as_int(duplicate[0].get("site_id"))!=site_id: raise APIError(409,"External incident ID is already linked to another site")
            return {"ok":True,"id":external_id,"duplicate":True}
    incident_id=external_id or ("SOS-" if kind=="SOS" else "RPT-")+core.app_today().strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:6].upper()
    visitor_id=str(payload.get("visitor_id") or "").strip() or None
    visitor_name=str(payload.get("visitor_name") or "").strip()
    phone=str(payload.get("phone") or "").strip() or None
    if visitor_id:
        visitor=db_query("SELECT full_name FROM visitors WHERE id=? AND site_id=?",(visitor_id,site_id))
        if not visitor: raise APIError(404,"Linked visitor does not belong to this site")
        if not visitor_name: visitor_name=str(visitor[0]["full_name"])
    zone=str(payload.get("zone_name") or "").strip()
    description=str(payload.get("description") or "").strip()
    if zone and (len(zone)>120 or not db_query("SELECT id FROM zones WHERE site_id=? AND name=?",(site_id,zone))): raise APIError(400,"Incident zone does not belong to this site")
    if len(description)>1000 or len(visitor_name)>160 or (phone and len(phone)>40): raise APIError(400,"Incident description, visitor name or phone exceeds the allowed length")
    lat=payload.get("latitude"); lon=payload.get("longitude")
    try:
        lat=float(lat) if lat is not None else None; lon=float(lon) if lon is not None else None
        if lat is not None and not -90<=lat<=90: raise ValueError()
        if lon is not None and not -180<=lon<=180: raise ValueError()
    except (ValueError,TypeError): raise APIError(400,"Invalid GPS coordinates")
    status="Submitted" if kind=="SOS" else "Open"
    created=mobile_event_time(payload.get("created_at"),"created_at") or utc_iso()
    title=str(payload.get("title") or (payload.get("emergency_type") if kind=="SOS" else "Visitor report") or ("Emergency assistance" if kind=="SOS" else "Visitor report")).strip()
    priority=str(payload.get("priority") or ("Critical" if kind=="SOS" else "Medium")).strip().title()
    if not title or len(title)>200: raise APIError(400,"Incident title is required and must be at most 200 characters")
    if priority not in {"Critical","High","Medium","Low","Advisory"}: raise APIError(400,"Invalid incident priority")
    db_transaction([
        ("INSERT INTO incidents (id,site_id,type,priority,title,visitor_name,visitor_id,zone_name,description,status,assigned_to,contact_phone,latitude,longitude,resolved_at,updated_at,reported_minutes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (incident_id,site_id,kind,priority,title,visitor_name,visitor_id,zone,description,status,None,phone,lat,lon,None,created,0,created)),
        add_history(incident_id,site_id,None,status,"mobile_integration","Received through authenticated mobile integration"),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"incident",incident_id,"received_from_mobile","mobile_integration",kind,created)),
    ])
    return {"ok":True,"id":incident_id,"site_id":site_id,"status":status,"duplicate":False}


def mobile_ingest_crowd(payload: dict[str, Any]) -> dict[str, Any]:
    site_id=as_int(payload.get("site_id"),0); zone=str(payload.get("zone_name") or "").strip()
    if not site_id or not zone: raise APIError(400,"site_id and zone_name are required")
    if len(zone)>120: raise APIError(400,"Zone name exceeds the allowed length")
    records=db_query("SELECT id,capacity FROM zones WHERE site_id=? AND name=?",(site_id,zone))
    if not records: raise APIError(404,"Zone not found")
    count=as_int(payload.get("visitor_count"),-1); capacity=as_int(records[0].get("capacity"))
    if count<0 or count>2147483647: raise APIError(400,"visitor_count must be a non-negative 32-bit integer")
    if capacity<=0: raise APIError(400,"Configure zone capacity before publishing density")
    density=round(count/capacity*100,1); observed=mobile_event_time(payload.get("observed_at"),"observed_at") or utc_iso(); obs_id="CO-"+uuid.uuid4().hex[:12].upper()
    db_transaction([
        ("INSERT INTO crowd_observations (id,site_id,zone_name,visitor_count,capacity,density_percent,source,observed_at) VALUES (?,?,?,?,?,?,?,?)",(obs_id,site_id,zone,count,capacity,density,str(payload.get("source") or "mobile_aggregator")[:100],observed)),
        ("UPDATE zones SET current_visitors=?,capacity=?,density_percent=?,level=?,updated_at=? WHERE site_id=? AND name=?",(count,capacity,density,"Critical" if density>=90 else "High" if density>=75 else "Moderate" if density>=50 else "Low",observed,site_id,zone)),
        ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),site_id,"crowd_observation",obs_id,"received_from_mobile","mobile_integration",f"{zone}: {count} visitors · {density}% density · source {str(payload.get('source') or 'mobile_aggregator')[:80]}",utc_iso())),
    ],expected_rows={1:1})
    return {"ok":True,"observation_id":obs_id,"zone_name":zone,"visitor_count":count,"density_percent":density,"observed_at":observed}


def notification_visible_to_zone(notification: dict[str, Any], zone_name: str) -> bool:
    audience=str(notification.get("target_audience", "")).strip()
    if audience.lower() in {"all", "all_visitors", "all visitors", "visitors"}: return True
    if not audience.lower().startswith("zone:"): return False
    target_zone=audience.split(":",1)[1].strip()
    actual_zone=str(notification.get("zone_name") or "").strip()
    return bool(target_zone and target_zone.casefold()==zone_name.strip().casefold() and actual_zone.casefold()==target_zone.casefold())


def mobile_notifications(site_id: int, visitor_id: str, zone_name: str = "") -> list[dict[str, Any]]:
    if not visitor_id: raise APIError(400,"visitor_id is required")
    rows=plain(db_query("SELECT id,site_id,zone_name,category,severity,title,message,target_audience,delivery_status,created_at FROM notifications WHERE site_id=?",(site_id,)))
    result=[row for row in rows if notification_visible_to_zone(row,zone_name)]
    return sorted(result,key=lambda r:str(r.get("created_at","")),reverse=True)


class AdminHandler(SimpleHTTPRequestHandler):
    def __init__(self,*args:Any,**kwargs:Any)->None:
        super().__init__(*args,directory=str(BASE_DIR),**kwargs)

    def log_message(self,fmt:str,*args:Any)->None:
        sys.stdout.write("[%s] %s\n"%(self.log_date_time_string(),fmt%args))

    def copyfile(self,source:Any,outputfile:Any)->None:
        try: super().copyfile(source,outputfile)
        except (BrokenPipeError,ConnectionResetError): pass

    def send_json(self,payload:Any,status:int=200,headers:dict[str,str]|None=None)->None:
        body=json.dumps(payload,ensure_ascii=False,default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers(); self.wfile.write(body)

    def read_json(self)->dict[str,Any]:
        length=int(self.headers.get("Content-Length","0"))
        if length>1024*1024: raise APIError(413,"Request body is too large")
        try: data=json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise APIError(400,"Request body must be valid JSON") from exc
        if not isinstance(data,dict): raise APIError(400,"Request body must be a JSON object")
        return data

    def auth_user(self)->dict[str,Any]|None:
        return user_from_handler(self)

    def site_for_user(self,user:dict[str,Any],params:dict[str,list[str]]|dict[str,Any])->int:
        requested=params.get("site_id")
        if isinstance(requested,list): requested=requested[0] if requested else None
        return scoped_site(user,requested)

    def check_origin(self)->None:
        origin=self.headers.get("Origin")
        host=self.headers.get("Host")
        if origin and host and urlparse(origin).netloc.lower()!=host.lower(): raise APIError(403,"Cross-origin request rejected")

    def session_cookie(self,token:str|None)->str:
        secure=os.getenv("YATRAFLOW_COOKIE_SECURE","").lower() in {"1","true","yes"} or self.headers.get("X-Forwarded-Proto","").lower()=="https"
        if not token: return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"+("; Secure" if secure else "")
        value=f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS*3600}"
        return value+("; Secure" if secure else "")

    def create_session(self,user:dict[str,Any])->tuple[str,str]:
        token=secrets.token_urlsafe(36); token_hash=hashlib.sha256(token.encode()).hexdigest()
        created=utc_iso(); expires=utc_iso(utc_now()+timedelta(hours=SESSION_HOURS)); ip=self.client_address[0] if self.client_address else ""
        db_transaction([
            ("INSERT INTO admin_sessions (token_hash,admin_id,created_at,expires_at,last_seen,ip_address) VALUES (?,?,?,?,?,?)",(token_hash,user["id"],created,expires,created,ip)),
            ("UPDATE admins SET last_login=?,last_active=? WHERE id=?",(created,"Now",user["id"])),
            ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),user["site_id"],"admin",user["id"],"login",user["id"],"Successful administrator login",created)),
        ])
        return token,token_hash

    def login(self,payload:dict[str,Any])->None:
        ip=self.client_address[0] if self.client_address else "unknown"
        started,count=LOGIN_ATTEMPTS.get(ip,(0.0,0))
        now=utc_now().timestamp()
        if now-started>600: started,count=now,0
        if count>=10: raise APIError(429,"Too many login attempts. Try again in ten minutes.")
        email=str(payload.get("email","")).strip().lower(); password=str(payload.get("password", ""))
        rows=db_query("SELECT id,site_id,full_name,email,role,role_key,active,password_hash,last_login FROM admins WHERE LOWER(email)=LOWER(?)",(email,)) if email else []
        if not rows or not bool(rows[0].get("active")) or not core.verify_password(password,rows[0].get("password_hash")):
            LOGIN_ATTEMPTS[ip]=(started,count+1)
            raise APIError(401,"Email or password is incorrect")
        LOGIN_ATTEMPTS.pop(ip,None)
        user=row_to_api_user(rows[0]); token,_=self.create_session(user)
        self.send_json({"authenticated":True,"user":user,"expires_in_seconds":SESSION_HOURS*3600},200,{"Set-Cookie":self.session_cookie(token)})

    def logout(self,user:dict[str,Any]|None)->None:
        if user and user.get("_token_hash"):
            db_transaction([
                ("DELETE FROM admin_sessions WHERE token_hash=?",(user["_token_hash"],)),
                ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),user["site_id"],"admin",user["id"],"logout",user["id"],"Administrator logged out",utc_iso())),
            ])
        self.send_json({"ok":True,"authenticated":False},200,{"Set-Cookie":self.session_cookie(None)})

    def require_admin(self)->dict[str,Any]:
        user=self.auth_user()
        if not user: raise APIError(401,"Please sign in to continue")
        return user

    def api_get(self,route:str,params:dict[str,list[str]],user:dict[str,Any]|None)->None:
        if route=="/api/health": return self.send_json(get_health())
        if route=="/api/session": return self.send_json({"authenticated":bool(user),"user":{k:v for k,v in (user or {}).items() if not k.startswith("_")}})
        if route.startswith("/api/integrations/mobile/"):
            require_mobile_key(self)
            if route=="/api/integrations/mobile/notifications":
                site_values=params.get("site_id",[])
                if not site_values or not site_values[0]: raise APIError(400,"site_id is required")
                try: sid=int(site_values[0])
                except (TypeError,ValueError): raise APIError(400,"site_id must be a positive integer")
                if sid<1: raise APIError(400,"site_id must be a positive integer")
                if not db_query("SELECT id FROM sites WHERE id=?",(sid,)): raise APIError(404,"Site not found")
                visitor_id=params.get("visitor_id",[""])[0].strip()
                if not visitor_id: raise APIError(400,"visitor_id is required")
                visitor=db_query("SELECT id,zone_name FROM visitors WHERE id=? AND site_id=?",(visitor_id,sid))
                if not visitor: raise APIError(404,"Visitor not found for this site")
                active_zone=db_query("SELECT zone_name FROM visitor_sessions WHERE visitor_id=? AND site_id=? AND checked_out_at IS NULL ORDER BY checked_in_at DESC",(visitor_id,sid))
                zone_value=active_zone[0].get("zone_name") if active_zone else visitor[0].get("zone_name")
                visitor_zone=str(zone_value or "")
                return self.send_json({"items":mobile_notifications(sid,visitor_id,visitor_zone)})
            return self.send_json({"error":"Integration route not found"},404)
        user=self.require_admin()
        if route=="/api/sites":
            require_permission(user,"read" if user["role_key"]!="analytics_viewer" else "read_analytics")
            return self.send_json({"items":get_sites_for_user(user)})
        sid=self.site_for_user(user,params)
        role=user.get("role_key")
        if route=="/api/dashboard":
            require_permission(user,"read" if role!="analytics_viewer" else "read_analytics")
            return self.send_json(get_dashboard(sid,user))
        if route=="/api/zones":
            require_permission(user,"read" if role!="analytics_viewer" else "read_analytics")
            return self.send_json({"items":get_zones(sid)})
        m=re.fullmatch(r"/api/zones/(\d+)",route)
        if m:
            require_permission(user,"read" if role!="analytics_viewer" else "read_analytics")
            return self.send_json(get_zone_detail(sid,int(m.group(1)),user))
        if route=="/api/incidents":
            require_permission(user,"operate")
            items=get_incidents(sid)
            status=params.get("status",[""])[0].lower(); kind=params.get("type",[""])[0].lower(); q=params.get("q",[""])[0].lower()
            if status: items=[i for i in items if str(i.get("status","")).lower()==status]
            if kind: items=[i for i in items if str(i.get("type","")).lower()==kind]
            if q: items=[i for i in items if q in " ".join(str(v or "") for v in i.values()).lower()]
            return self.send_json({"items":items,"total":len(items)})
        m=re.fullmatch(r"/api/incidents/([^/]+)",route)
        if m:
            require_permission(user,"operate")
            return self.send_json(get_incident_detail(sid,unquote(m.group(1)),user))
        if route=="/api/visitors":
            require_permission(user,"view_pii")
            result=get_visitors(sid,params.get("q",[""])[0],params.get("status",[""])[0],params.get("start_date",[""])[0],params.get("end_date",[""])[0],as_int(params.get("page",["1"])[0],1),as_int(params.get("page_size",["25"])[0],25),False,params.get("filter",[""])[0])
            return self.send_json(result)
        m=re.fullmatch(r"/api/visitors/([^/]+)",route)
        if m:
            require_permission(user,"view_pii")
            return self.send_json(get_visitor_detail(sid,unquote(m.group(1)),True))
        if route=="/api/analytics":
            require_permission(user,"read" if role!="analytics_viewer" else "read_analytics")
            days=as_int(params.get("days",["14"])[0],14); agg=params.get("aggregation",["daily"])[0]
            return self.send_json(get_analytics(sid,days,agg))
        if route=="/api/alerts":
            require_permission(user,"read" if role!="analytics_viewer" else "read_analytics")
            rows=plain(db_query("SELECT id,site_id,category,title,description,created_at FROM alerts WHERE site_id=?",(sid,)))
            return self.send_json({"items":sorted(rows,key=lambda r:str(r.get("created_at","")),reverse=True)})
        if route=="/api/admins":
            require_permission(user,"manage_admins")
            return self.send_json({"items":get_admins(sid)})
        if route=="/api/broadcasts":
            require_permission(user,"read" if role!="analytics_viewer" else "read_analytics")
            return self.send_json({"items":get_broadcasts(sid)})
        if route=="/api/audit":
            require_permission(user,"audit")
            rows=plain(db_query("SELECT id,site_id,entity_type,entity_id,action,actor,detail,created_at FROM audit_log WHERE site_id=?",(sid,)))
            return self.send_json({"items":sorted(rows,key=lambda r:str(r.get("created_at","")),reverse=True)[:200]})
        return self.send_json({"error":"API route not found"},404)

    def api_post(self,route:str,payload:dict[str,Any],user:dict[str,Any]|None)->None:
        if route=="/api/login": return self.login(payload)
        if route=="/api/logout": return self.logout(self.auth_user())
        if route.startswith("/api/integrations/mobile/"):
            require_mobile_key(self)
            if route=="/api/integrations/mobile/visitors": return self.send_json(mobile_ingest_visitor(payload),201)
            if route=="/api/integrations/mobile/sos": return self.send_json(mobile_ingest_incident(payload,"SOS"),201)
            if route=="/api/integrations/mobile/reports": return self.send_json(mobile_ingest_incident(payload,"Report"),201)
            if route=="/api/integrations/mobile/crowd": return self.send_json(mobile_ingest_crowd(payload),201)
            m=re.fullmatch(r"/api/integrations/mobile/notifications/([^/]+)/ack",route)
            if m:
                notification_id=unquote(m.group(1)); visitor_id=str(payload.get("visitor_id") or "").strip()
                if not visitor_id: raise APIError(400,"visitor_id is required")
                notification=db_query("SELECT id,site_id,zone_name,target_audience FROM notifications WHERE id=?",(notification_id,))
                if not notification: raise APIError(404,"Notification not found")
                sid=as_int(notification[0]["site_id"])
                visitor=db_query("SELECT id,zone_name FROM visitors WHERE id=? AND site_id=?",(visitor_id,sid))
                if not visitor: raise APIError(404,"Visitor not found for this site")
                active_zone=db_query("SELECT zone_name FROM visitor_sessions WHERE visitor_id=? AND site_id=? AND checked_out_at IS NULL ORDER BY checked_in_at DESC",(visitor_id,sid))
                zone_value=active_zone[0].get("zone_name") if active_zone else visitor[0].get("zone_name")
                if not notification_visible_to_zone(notification[0],str(zone_value or "")):
                    raise APIError(404,"Notification is not available to this visitor")
                prior=db_query("SELECT id FROM notification_deliveries WHERE notification_id=? AND visitor_id=? AND channel=? AND delivery_status=? ORDER BY created_at DESC",(notification_id,visitor_id,"mobile_poll","acknowledged"))
                if prior: return self.send_json({"ok":True,"delivery_status":"acknowledged","delivery_id":prior[0]["id"],"already_acknowledged":True})
                delivery_id="DLV-"+uuid.uuid4().hex[:12].upper(); now=utc_iso()
                db_transaction([
                    ("INSERT INTO notification_deliveries (id,notification_id,visitor_id,channel,delivery_status,created_at,delivered_at,error) VALUES (?,?,?,?,?,?,?,?)",(delivery_id,notification_id,visitor_id,"mobile_poll","acknowledged",now,now,None)),
                    ("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),sid,"notification",notification_id,"acknowledged","mobile_integration",f"Delivery acknowledgement from visitor {visitor_id}",now)),
                ])
                return self.send_json({"ok":True,"delivery_status":"acknowledged","delivery_id":delivery_id,"already_acknowledged":False})
            return self.send_json({"error":"Integration route not found"},404)
        user=self.require_admin(); self.check_origin()
        if route=="/api/broadcasts":
            require_permission(user,"operate")
            sid=self.site_for_user(user,payload); return self.send_json({"ok":True,"item":create_broadcast(payload,sid,user)},201)
        if route=="/api/admins":
            require_permission(user,"manage_admins")
            sid=self.site_for_user(user,payload); return self.send_json({"ok":True,"item":create_admin(payload,sid,user)},201)
        m=re.fullmatch(r"/api/admins/([^/]+)/update",route)
        if m:
            require_permission(user,"manage_admins")
            item=update_admin(unquote(m.group(1)),payload,user); return self.send_json({"ok":True,"item":item})
        if route=="/api/alerts/notify":
            require_permission(user,"operate")
            sid=self.site_for_user(user,payload); category=str(payload.get("category") or "").strip()
            title=str(payload.get("title") or category).strip(); message=str(payload.get("message") or "").strip()
            severity=str(payload.get("severity") or "").strip().lower(); zone_name=str(payload.get("zone_name") or "").strip() or None
            if not category or len(category)>60 or not title or len(title)>160 or not message or len(message)>500: raise APIError(400,"Notification category/title/message is required and must fit the allowed length")
            if severity not in {"info","warning","urgent","critical"}: raise APIError(400,"Invalid notification severity")
            if zone_name and (len(zone_name)>120 or not db_query("SELECT id FROM zones WHERE site_id=? AND name=?",(sid,zone_name))): raise APIError(400,"Selected notification zone does not belong to this site")
            item=resolve_notification_delivery(sid,zone_name,category,severity,title,message,"staff",user["id"])
            return self.send_json({"ok":True,"item":item,"delivered":False},201)
        m=re.fullmatch(r"/api/incidents/([^/]+)/(transition|assign|dispatch|call|notes)",route)
        if m:
            require_permission(user,"operate")
            sid=self.site_for_user(user,payload); incident_id=unquote(m.group(1)); action=m.group(2)
            if action=="transition":
                item=update_incident_status(incident_id,sid,str(payload.get("status", "")),user,str(payload.get("note", "")))
                return self.send_json({"ok":True,"item":item})
            if action=="assign":
                item=assign_incident(incident_id,sid,str(payload.get("responder",payload.get("assigned_to",""))),user)
                return self.send_json({"ok":True,"item":item})
            if action=="dispatch":
                item=create_dispatch_request(incident_id,sid,str(payload.get("responder",payload.get("assigned_to","Unspecified responder"))),str(payload.get("service","medical")),user)
                return self.send_json({"ok":True,"item":item,"external_action_sent":False},202)
            if action=="call":
                detail=get_incident_detail(sid,incident_id,user)
                phone=(detail.get("visitor") or {}).get("phone") or detail.get("contact_phone")
                audit(sid,"incident",incident_id,"call_requested","Contact workflow opened",user["id"])
                return self.send_json({"ok":True,"phone":phone,"message":"No contact number is stored for this visitor." if not phone else "Confirm the phone call on your device."})
            item=add_incident_note(incident_id,sid,str(payload.get("note","")),user)
            return self.send_json({"ok":True,"item":item},201)
        m=re.fullmatch(r"/api/visitors/([^/]+)/checkout",route)
        if m:
            require_permission(user,"operate")
            sid=self.site_for_user(user,payload); visitor_id=unquote(m.group(1))
            if not db_query("SELECT id FROM visitors WHERE id=? AND site_id=?",(visitor_id,sid)): raise APIError(404,"Visitor not found")
            sessions=db_query("SELECT id FROM visitor_sessions WHERE visitor_id=? AND site_id=? AND (checked_out_at IS NULL OR checked_out_at='')",(visitor_id,sid))
            if not sessions: raise APIError(409,"No active visitor session to check out")
            now=utc_iso(); statements=[("UPDATE visitor_sessions SET checked_out_at=? WHERE id=? AND (checked_out_at IS NULL OR checked_out_at='')",(now,s["id"])) for s in sessions]
            statements.append(("UPDATE visitors SET status='Checked out' WHERE id=? AND site_id=?",(visitor_id,sid)))
            statements.append(("INSERT INTO audit_log (id,site_id,entity_type,entity_id,action,actor,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",("AUD-"+uuid.uuid4().hex[:12].upper(),sid,"visitor",visitor_id,"checked_out",user["id"],now,now)))
            db_transaction(statements,expected_rows={index:1 for index in range(len(sessions)+1)})
            return self.send_json({"ok":True,"visitor_id":visitor_id,"checked_out_at":now})
        return self.send_json({"error":"API route not found"},404)

    def do_GET(self)->None:
        parsed=urlparse(self.path); route=parsed.path.rstrip("/") or "/"
        if not route.startswith("/api/"):
            # Only the client-facing assets are public. Never expose .env, databases,
            # SQL scripts, or server-side source through SimpleHTTPRequestHandler.
            if route not in {"/", "/index.html", "/app.js"}:
                self.send_error(404,"Not found")
                return
            return super().do_GET()
        params=parse_qs(parsed.query)
        try:
            self.api_get(route,params,self.auth_user())
        except APIError as exc: self.send_json({"error":exc.message},exc.status)
        except Exception as exc:
            sys.stderr.write(f"API GET {route} failed: {exc}\n")
            self.send_json({"error":"The request failed. Check server/database configuration."},503)

    def do_HEAD(self)->None:
        route=urlparse(self.path).path.rstrip("/") or "/"
        if route in {"/", "/index.html", "/app.js"}:
            super().do_HEAD()
        else:
            self.send_error(404,"Not found")

    def do_POST(self)->None:
        route=urlparse(self.path).path.rstrip("/")
        try:
            self.check_origin()
            payload=self.read_json()
            self.api_post(route,payload,self.auth_user())
        except APIError as exc: self.send_json({"error":exc.message},exc.status)
        except Exception as exc:
            sys.stderr.write(f"API POST {route} failed: {exc}\n")
            self.send_json({"error":"The operation failed. Check server/database configuration."},503)


def main()->None:
    if core.DB_ENGINE in {"sqlite","sqlite3"}:
        core.initialize_sqlite()
        print(f"YatraFlow admin API on 0.0.0.0:{PORT} · SQLite DEVELOPMENT SAMPLE · {core.SQLITE_PATH}")
        print(f"Demo sign-in: {core.DEMO_ADMIN_EMAIL} / {core.DEMO_ADMIN_PASSWORD} (local demo only)")
    else:
        try: bootstrap_sqlserver_admin()
        except Exception as exc: print(f"SQL Server bootstrap check: {exc}")
        print(f"YatraFlow admin API on 0.0.0.0:{PORT} · {core.DB_ENGINE} · credentials required in .env")
    server=ThreadingHTTPServer(("0.0.0.0",PORT),AdminHandler)
    server.daemon_threads=True
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nStopping YatraFlow admin API")
    finally: server.server_close()


if __name__=="__main__": main()
