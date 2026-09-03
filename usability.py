"""Telemetría de usabilidad (mismo esquema que Gestor de Vísceras)."""
import json
import os
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

DATA_DIR = Path(__file__).resolve().parent / "local_data"
EVENTS_PATH = DATA_DIR / "usability_events.json"
MAX_EVENTS = 50000
TOKEN_TTL_S = 24 * 60 * 60
DEFAULT_ADMIN_PASSWORD = "123456789"

_lock = Lock()
_tokens = {}
_cache = None


def _admin_password() -> str:
    pwd = str(os.getenv("USABILITY_ADMIN_PASSWORD") or "").strip()
    if (pwd.startswith('"') and pwd.endswith('"')) or (pwd.startswith("'") and pwd.endswith("'")):
        pwd = pwd[1:-1].strip()
    return pwd or DEFAULT_ADMIN_PASSWORD


def _blank():
    return {"events": [], "version": 1}


def _load():
    global _cache
    if _cache is not None:
        return _cache
    if EVENTS_PATH.exists():
        try:
            with EVENTS_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data.get("events"), list):
                data["events"] = []
            _cache = data
            return _cache
        except Exception:
            pass
    _cache = _blank()
    return _cache


def _save():
    DATA_DIR.mkdir(exist_ok=True)
    with EVENTS_PATH.open("w", encoding="utf-8") as fh:
        json.dump(_cache, fh, ensure_ascii=False, indent=2)


def record_event(payload: dict, ip: str = "", user_agent: str = "") -> dict:
    evt = {
        "id": secrets.token_hex(8),
        "ts": datetime.now(timezone.utc).isoformat(),
        "usuario": str(payload.get("usuario") or "anonimo").strip()[:120] or "anonimo",
        "action": str(payload.get("action") or "event").strip()[:80],
        "module": str(payload.get("module") or "").strip()[:80],
        "detail": str(payload.get("detail") or "").strip()[:240],
        "sessionId": str(payload.get("sessionId") or "").strip()[:64],
        "page": str(payload.get("page") or "").strip()[:80],
        "ip": (ip or "")[:64],
        "userAgent": (user_agent or "")[:280],
    }
    with _lock:
        data = _load()
        data["events"].append(evt)
        if len(data["events"]) > MAX_EVENTS:
            data["events"] = data["events"][-MAX_EVENTS:]
        _save()
    return {"success": True, "id": evt["id"]}


def login_admin(password: str):
    if str(password or "") != _admin_password():
        return None
    token = secrets.token_hex(24)
    _tokens[token] = time.time() + TOKEN_TTL_S
    return token


def verify_admin(token: str) -> bool:
    t = str(token or "").strip()
    exp = _tokens.get(t)
    if not exp or time.time() > exp:
        _tokens.pop(t, None)
        return False
    return True


def _sorted_counts(counter: Counter, limit: int):
    return [{"name": k, "count": v} for k, v in counter.most_common(limit)]


def get_stats(days: int = 30) -> dict:
    days = max(1, min(365, int(days or 30)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with _lock:
        events = list(_load().get("events") or [])

    filtered = []
    for e in events:
        try:
            ts = datetime.fromisoformat(str(e.get("ts", "")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= cutoff:
            filtered.append(e)

    by_user, by_action, by_module, by_day = Counter(), Counter(), Counter(), Counter()
    sessions = set()
    last = None
    for e in filtered:
        u = e.get("usuario") or "anonimo"
        by_user[u] += 1
        by_action[e.get("action") or "event"] += 1
        if e.get("module"):
            by_module[e["module"]] += 1
        by_day[str(e.get("ts", ""))[:10]] += 1
        if e.get("sessionId"):
            sessions.add(f"{u}::{e['sessionId']}")
        if last is None or str(e.get("ts", "")) > str(last.get("ts", "")):
            last = e

    recent = sorted(filtered, key=lambda x: str(x.get("ts", "")), reverse=True)[:200]
    return {
        "success": True,
        "store": "json",
        "totalEvents": len(filtered),
        "uniqueUsers": len(by_user),
        "uniqueSessions": len(sessions),
        "days": days,
        "lastActivity": (
            {"ts": last.get("ts"), "usuario": last.get("usuario"), "action": last.get("action")}
            if last else None
        ),
        "byUsuario": _sorted_counts(by_user, 50),
        "byAction": _sorted_counts(by_action, 40),
        "byModule": _sorted_counts(by_module, 30),
        "byDay": [{"date": d, "count": by_day[d]} for d in sorted(by_day)],
        "recent": [
            {
                "ts": e.get("ts"),
                "usuario": e.get("usuario"),
                "action": e.get("action"),
                "module": e.get("module"),
                "detail": e.get("detail"),
                "page": e.get("page"),
            }
            for e in recent
        ],
    }
