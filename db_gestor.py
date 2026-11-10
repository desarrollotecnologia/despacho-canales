"""
BD propia del gestor de canales (NO SIRT).

Persiste la capa operativa que antes vivía en local_data/canales_state.json:
OPLs, particulares, asignado congelado, etiquetas impresas, adicionales,
progreso, histórico y settings (rollo 2/4, operación finalizada).

SIRT (PostgreSQL) sigue siendo solo consulta. Esta BD no escribe ni lee
tablas de trazabilidad_proceso / organizaciones.

Foto del día = consulta SIRT + overrides de esta BD (tabla override_pieza;
los overrides quedan preparados y aún no se aplican en las consultas).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "local_data"
DEFAULT_DB_PATH = DATA_DIR / "gestor_canales.db"
STATE_JSON_NAME = "canales_state.json"
SCHEMA_VERSION = "1"

_LOCK = threading.Lock()
_READY = False
_READY_LOCK = threading.Lock()
_OPL_MAP_CACHE: Optional[Dict[str, str]] = None
_MYSQL_CFG: Optional[Dict[str, Any]] = None
_ENGINE = "sqlite"
_MYSQL_POOL: List[Any] = []
_MYSQL_POOL_LOCK = threading.Lock()
_MYSQL_CREATED = 0
_MYSQL_POOL_MAX = 8


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_gestor_url() -> Dict[str, Any]:
    url = (os.getenv("GESTOR_DB_URL") or "").strip()
    if not url:
        return {"engine": "sqlite", "path": DEFAULT_DB_PATH}
    lower = url.lower()
    if lower.startswith("mysql://") or lower.startswith("mysql+pymysql://"):
        parsed = urlparse(url)
        dbname = (parsed.path or "").lstrip("/")
        if not dbname:
            raise RuntimeError("GESTOR_DB_URL MySQL debe incluir la base, p.ej. mysql://user:pass@127.0.0.1:3306/gestor_canales")
        return {
            "engine": "mysql",
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or "root"),
            "password": unquote(parsed.password or ""),
            "database": dbname,
        }
    if lower.startswith("postgresql://") or lower.startswith("postgres://"):
        raise RuntimeError(
            "GESTOR_DB_URL PostgreSQL aún no está habilitado. "
            "Use mysql://.../gestor_canales o sqlite:///local_data/gestor_canales.db"
        )
    if lower.startswith("sqlite:////"):
        return {"engine": "sqlite", "path": Path(url[len("sqlite:////") :])}
    if lower.startswith("sqlite:///"):
        raw = url[len("sqlite:///") :]
        p = Path(raw)
        return {"engine": "sqlite", "path": p if p.is_absolute() else ROOT / p}
    return {"engine": "sqlite", "path": Path(url)}


def _cfg() -> Dict[str, Any]:
    global _MYSQL_CFG, _ENGINE
    if _MYSQL_CFG is None:
        _MYSQL_CFG = _parse_gestor_url()
        _ENGINE = str(_MYSQL_CFG.get("engine") or "sqlite")
    return _MYSQL_CFG


def engine_name() -> str:
    return str(_cfg().get("engine") or "sqlite")


def resolve_db_path() -> Path:
    """Ruta SQLite (si aplica). Con MySQL no se usa archivo."""
    cfg = _cfg()
    if cfg.get("engine") == "mysql":
        return DEFAULT_DB_PATH
    return Path(cfg["path"])


def db_label() -> str:
    cfg = _cfg()
    if cfg.get("engine") == "mysql":
        return f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"
    return str(cfg["path"])


def db_path() -> Path:
    return resolve_db_path()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS opl_propietario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    propietario TEXT NOT NULL UNIQUE,
    opl TEXT NOT NULL,
    extra INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS beneficio_opl_propietario (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    propietario TEXT NOT NULL UNIQUE,
    opl TEXT NOT NULL,
    extra INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opl_particular (
    opl TEXT PRIMARY KEY,
    orden INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asignado_congelado (
    fecha TEXT NOT NULL,
    turno TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (fecha, turno)
);

CREATE TABLE IF NOT EXISTS etiqueta_impresa (
    fecha TEXT NOT NULL,
    codigo TEXT NOT NULL,
    impresa_en TEXT NOT NULL,
    PRIMARY KEY (fecha, codigo)
);
CREATE INDEX IF NOT EXISTS idx_etiqueta_impresa_fecha ON etiqueta_impresa(fecha);

CREATE TABLE IF NOT EXISTS app_setting (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS adicionales_dia (
    fecha TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opl_progreso_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guardado_en TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historico_operacion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS override_pieza (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    codigo TEXT NOT NULL,
    campo TEXT NOT NULL,
    valor TEXT,
    usuario TEXT,
    motivo TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_override_pieza_fecha_codigo
    ON override_pieza(fecha, codigo);
"""

MYSQL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    clave VARCHAR(191) PRIMARY KEY,
    valor TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opl_propietario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    propietario VARCHAR(191) NOT NULL UNIQUE,
    opl VARCHAR(191) NOT NULL,
    extra INT NOT NULL DEFAULT 0,
    updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS beneficio_opl_propietario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    propietario VARCHAR(191) NOT NULL UNIQUE,
    opl VARCHAR(191) NOT NULL,
    extra INT NOT NULL DEFAULT 0,
    updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opl_particular (
    opl VARCHAR(191) PRIMARY KEY,
    orden INT NOT NULL,
    created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS asignado_congelado (
    fecha VARCHAR(16) NOT NULL,
    turno VARCHAR(32) NOT NULL,
    payload LONGTEXT NOT NULL,
    updated_at VARCHAR(32) NOT NULL,
    PRIMARY KEY (fecha, turno)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS etiqueta_impresa (
    fecha VARCHAR(16) NOT NULL,
    codigo VARCHAR(64) NOT NULL,
    impresa_en VARCHAR(64) NOT NULL,
    PRIMARY KEY (fecha, codigo),
    INDEX idx_etiqueta_impresa_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_setting (
    clave VARCHAR(191) PRIMARY KEY,
    valor TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS adicionales_dia (
    fecha VARCHAR(16) PRIMARY KEY,
    payload LONGTEXT NOT NULL,
    updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS opl_progreso_snapshot (
    id INT AUTO_INCREMENT PRIMARY KEY,
    guardado_en VARCHAR(32) NOT NULL,
    payload LONGTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS historico_operacion (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fecha VARCHAR(64),
    payload LONGTEXT NOT NULL,
    created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS override_pieza (
    id INT AUTO_INCREMENT PRIMARY KEY,
    fecha VARCHAR(16) NOT NULL,
    codigo VARCHAR(64) NOT NULL,
    campo VARCHAR(64) NOT NULL,
    valor TEXT,
    usuario VARCHAR(128),
    motivo TEXT,
    created_at VARCHAR(32) NOT NULL,
    INDEX idx_override_pieza_fecha_codigo (fecha, codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


_CONFLICT_OPL = re.compile(
    r"ON CONFLICT\(propietario\) DO UPDATE SET\s+opl=excluded\.opl,\s+updated_at=excluded\.updated_at",
    re.I | re.S,
)


def _to_mysql_sql(sql: str) -> str:
    s = _CONFLICT_OPL.sub(
        "ON DUPLICATE KEY UPDATE opl=VALUES(opl), updated_at=VALUES(updated_at)",
        sql,
    )
    s = s.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
    s = s.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
    s = s.replace("?", "%s")
    return s


class _Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _MysqlCursor:
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = getattr(cur, "lastrowid", None)
        self.rowcount = getattr(cur, "rowcount", -1)

    def fetchone(self):
        row = self._cur.fetchone()
        return _Row(row) if row is not None else None

    def fetchall(self):
        return [_Row(r) for r in (self._cur.fetchall() or [])]


class _MysqlConn:
    def __init__(self, raw, pooled: bool = True):
        self._raw = raw
        self._cur = raw.cursor()
        self._pooled = pooled

    def execute(self, sql, params=()):
        self._cur.execute(_to_mysql_sql(sql), tuple(params) if params is not None else ())
        return _MysqlCursor(self._cur)

    def executemany(self, sql, seq_of_params):
        self._cur.executemany(
            _to_mysql_sql(sql),
            [tuple(p) for p in seq_of_params],
        )
        return _MysqlCursor(self._cur)

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self._cur.execute(stmt)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        if self._pooled:
            _release_mysql(self._raw)
        else:
            try:
                self._raw.close()
            except Exception:
                pass


def _connect_sqlite() -> sqlite3.Connection:
    path = resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=8, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _new_mysql():
    import pymysql
    from pymysql.cursors import DictCursor

    cfg = _cfg()
    return pymysql.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=8,
        read_timeout=30,
        write_timeout=30,
    )


def _mysql_raw():
    global _MYSQL_CREATED
    with _MYSQL_POOL_LOCK:
        while _MYSQL_POOL:
            raw = _MYSQL_POOL.pop()
            try:
                raw.ping(reconnect=True)
                return raw
            except Exception:
                try:
                    raw.close()
                except Exception:
                    pass
                _MYSQL_CREATED = max(0, _MYSQL_CREATED - 1)
        if _MYSQL_CREATED < _MYSQL_POOL_MAX:
            _MYSQL_CREATED += 1
            create = True
        else:
            create = False
    if create:
        try:
            return _new_mysql()
        except Exception:
            with _MYSQL_POOL_LOCK:
                _MYSQL_CREATED = max(0, _MYSQL_CREATED - 1)
            raise
    deadline = time.time() + 8
    while time.time() < deadline:
        with _MYSQL_POOL_LOCK:
            if _MYSQL_POOL:
                raw = _MYSQL_POOL.pop()
                try:
                    raw.ping(reconnect=True)
                    return raw
                except Exception:
                    try:
                        raw.close()
                    except Exception:
                        pass
                    _MYSQL_CREATED = max(0, _MYSQL_CREATED - 1)
        time.sleep(0.01)
    return _new_mysql()


def _release_mysql(raw) -> None:
    if raw is None:
        return
    with _MYSQL_POOL_LOCK:
        if len(_MYSQL_POOL) < _MYSQL_POOL_MAX:
            _MYSQL_POOL.append(raw)
            return
    try:
        raw.close()
    except Exception:
        pass


def _connect_mysql() -> _MysqlConn:
    return _MysqlConn(_mysql_raw(), pooled=True)


def _connect():
    if engine_name() == "mysql":
        return _connect_mysql()
    return _connect_sqlite()


@contextmanager
def get_conn():
    mysql = engine_name() == "mysql"
    conn = _connect()
    try:
        if mysql:
            yield conn
            conn.commit()
        else:
            with _LOCK:
                yield conn
                conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> str:
    with get_conn() as conn:
        if engine_name() == "mysql":
            conn.executescript(MYSQL_SCHEMA_SQL)
        else:
            conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(clave, valor) VALUES (?, ?)",
            ("schema_version", SCHEMA_VERSION),
        )
    return db_label()


def _count(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[0])


def is_operationally_empty() -> bool:
    with get_conn() as conn:
        return (
            _count(conn, "opl_propietario")
            + _count(conn, "asignado_congelado")
            + _count(conn, "etiqueta_impresa")
            + _count(conn, "app_setting")
            + _count(conn, "opl_particular")
            + _count(conn, "adicionales_dia")
            + _count(conn, "opl_progreso_snapshot")
            + _count(conn, "historico_operacion")
        ) == 0


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _seed_opl_defaults(conn: Any, defaults: Sequence[Sequence[Any]]) -> None:
    now = _now()
    for row in defaults:
        if not row:
            continue
        prop = str(row[0] or "").strip().upper()
        opl = str(row[1] if len(row) > 1 else "").strip()
        extra = 1 if (len(row) > 2 and row[2]) else 0
        if not prop or "\t" in prop or len(prop) > 120:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO opl_propietario(propietario, opl, extra, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (prop, opl, extra, now),
        )
    _invalidate_opl_cache()


def import_state_dict(data: Dict[str, Any], opl_defaults: Sequence[Sequence[Any]] = ()) -> None:
    """Importa un dict con la forma de canales_state.json. Transacción única."""
    now = _now()
    with get_conn() as conn:
        imported_opl = 0
        for row in data.get("opl_config") or []:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            prop = str(row[0] or "").strip().upper()
            opl = str(row[1] if len(row) > 1 else "").strip()
            extra = 1 if (len(row) > 2 and row[2]) else 0
            if not prop or "\t" in prop or len(prop) > 120:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO opl_propietario(propietario, opl, extra, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (prop, opl, extra, now),
            )
            imported_opl += 1
        # Defaults solo si el JSON no traía opl_config (mismo criterio que el JSON anterior).
        if imported_opl == 0 and opl_defaults:
            _seed_opl_defaults(conn, opl_defaults)

        for i, name in enumerate(data.get("opls_particulares") or []):
            opl = str(name or "").strip()
            if not opl:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO opl_particular(opl, orden, created_at)
                VALUES (?, ?, ?)
                """,
                (opl, i, now),
            )

        bag = data.get("asignado_congelado") or {}
        if isinstance(bag, dict):
            for key, payload in bag.items():
                raw = str(key or "")
                if "|" not in raw:
                    continue
                fecha, turno = raw.split("|", 1)
                fecha = fecha.strip()
                turno = turno.strip()
                if not fecha:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO asignado_congelado(fecha, turno, payload, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (fecha, turno, _json_dumps(payload), now),
                )

        etiq = data.get("etiquetas_impresas") or {}
        if isinstance(etiq, dict):
            for fecha, mapa in etiq.items():
                if not isinstance(mapa, dict):
                    continue
                for codigo, ts in mapa.items():
                    cod = str(codigo or "").strip().upper()
                    if not str(fecha).strip() or not cod:
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO etiqueta_impresa(fecha, codigo, impresa_en)
                        VALUES (?, ?, ?)
                        """,
                        (str(fecha).strip(), cod, str(ts or now)),
                    )

        cols = data.get("etiquetas_rollo_columnas")
        if cols in (2, 4, "2", "4"):
            conn.execute(
                "INSERT OR REPLACE INTO app_setting(clave, valor) VALUES (?, ?)",
                ("etiquetas_rollo_columnas", str(int(cols))),
            )

        conn.execute(
            "INSERT OR REPLACE INTO app_setting(clave, valor) VALUES (?, ?)",
            ("operacion_finalizada", "1" if data.get("operacion_finalizada") else "0"),
        )

        adic = data.get("adicionales_por_fecha") or {}
        if isinstance(adic, dict):
            for fecha, payload in adic.items():
                if not str(fecha).strip():
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO adicionales_dia(fecha, payload, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (str(fecha).strip(), _json_dumps(payload if payload is not None else []), now),
                )

        progreso = data.get("opl_progreso") or []
        if progreso:
            conn.execute(
                "INSERT INTO opl_progreso_snapshot(guardado_en, payload) VALUES (?, ?)",
                (now, _json_dumps(progreso)),
            )

        for item in data.get("historico") or []:
            fecha = None
            if isinstance(item, dict):
                fecha = str(item.get("fecha") or "").strip() or None
            conn.execute(
                """
                INSERT INTO historico_operacion(fecha, payload, created_at)
                VALUES (?, ?, ?)
                """,
                (fecha, _json_dumps(item), now),
            )

        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(clave, valor) VALUES (?, ?)",
            ("json_migrated_at", now),
        )
    _invalidate_opl_cache()


def migrate_from_json(
    json_path: Optional[Path] = None,
    opl_defaults: Sequence[Sequence[Any]] = (),
    backup: bool = True,
) -> Dict[str, Any]:
    """
    One-shot: si existe canales_state.json y la BD está vacía, importa y
    renombra el JSON a .bak. Idempotente si la BD ya tiene datos.
    """
    init_schema()
    src = Path(json_path) if json_path else DATA_DIR / STATE_JSON_NAME
    out: Dict[str, Any] = {
        "db": db_label(),
        "json": str(src),
        "imported": False,
        "backup": None,
        "reason": "",
    }
    if not is_operationally_empty():
        out["reason"] = "bd_ya_tiene_datos"
        return out
    if not src.exists():
        if opl_defaults:
            with get_conn() as conn:
                _seed_opl_defaults(conn, opl_defaults)
            out["reason"] = "sin_json_seed_defaults"
        else:
            out["reason"] = "sin_json_bd_vacia"
        return out

    raw = src.read_text(encoding="utf-8").strip()
    if not raw:
        out["reason"] = "json_vacio"
        if opl_defaults:
            with get_conn() as conn:
                _seed_opl_defaults(conn, opl_defaults)
        return out

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Estado JSON inválido en {src}")
    import_state_dict(data, opl_defaults=opl_defaults)
    out["imported"] = True
    out["reason"] = "ok"
    if backup:
        bak = src.with_suffix(".json.bak")
        if bak.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = src.with_name(f"{src.stem}_{stamp}.json.bak")
        src.replace(bak)
        out["backup"] = str(bak)
    return out


def export_state_from_sqlite(path: Optional[Path] = None) -> Dict[str, Any]:
    """Lee el SQLite local (backup) sin usar GESTOR_DB_URL."""
    src = Path(path) if path else DEFAULT_DB_PATH
    if not src.exists():
        raise FileNotFoundError(str(src))
    con = sqlite3.connect(str(src), timeout=8)
    con.row_factory = sqlite3.Row
    try:
        def rows(sql: str):
            return con.execute(sql).fetchall()

        opl = [[r["propietario"], r["opl"], r["extra"]] for r in rows(
            "SELECT propietario, opl, extra FROM opl_propietario ORDER BY id"
        )]
        particulares = [r["opl"] for r in rows("SELECT opl FROM opl_particular ORDER BY orden, opl")]
        bag = {}
        for r in rows("SELECT fecha, turno, payload FROM asignado_congelado"):
            bag[f"{r['fecha']}|{r['turno']}"] = _json_loads(r["payload"], {})
        etiq: Dict[str, Dict[str, str]] = {}
        for r in rows("SELECT fecha, codigo, impresa_en FROM etiqueta_impresa"):
            etiq.setdefault(r["fecha"], {})[r["codigo"]] = r["impresa_en"]
        settings = {r["clave"]: r["valor"] for r in rows("SELECT clave, valor FROM app_setting")}
        adic = {}
        for r in rows("SELECT fecha, payload FROM adicionales_dia"):
            data = _json_loads(r["payload"], [])
            adic[r["fecha"]] = data if isinstance(data, list) else []
        prog_row = con.execute(
            "SELECT payload FROM opl_progreso_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
        progreso = _json_loads(prog_row["payload"] if prog_row else None, [])
        if not isinstance(progreso, list):
            progreso = []
        historico = []
        for r in rows("SELECT payload FROM historico_operacion ORDER BY id"):
            item = _json_loads(r["payload"], None)
            if item is not None:
                historico.append(item)
        cols = settings.get("etiquetas_rollo_columnas", "4")
        try:
            cols_int = int(cols)
        except (TypeError, ValueError):
            cols_int = 4
        return {
            "opl_config": opl,
            "opl_progreso": progreso,
            "historico": historico,
            "operacion_finalizada": settings.get("operacion_finalizada", "0") == "1",
            "asignado_congelado": bag,
            "opls_particulares": particulares,
            "adicionales_por_fecha": adic,
            "etiquetas_impresas": etiq,
            "etiquetas_rollo_columnas": 2 if cols_int == 2 else 4,
        }
    finally:
        con.close()


def migrate_from_sqlite(
    sqlite_path: Optional[Path] = None,
    opl_defaults: Sequence[Sequence[Any]] = (),
) -> Dict[str, Any]:
    """Copia SQLite → motor actual (MySQL) si el destino está vacío."""
    init_schema()
    src = Path(sqlite_path) if sqlite_path else DEFAULT_DB_PATH
    out: Dict[str, Any] = {
        "db": db_label(),
        "sqlite": str(src),
        "imported": False,
        "reason": "",
    }
    if engine_name() != "mysql":
        out["reason"] = "destino_no_es_mysql"
        return out
    if not is_operationally_empty():
        out["reason"] = "bd_ya_tiene_datos"
        return out
    if not src.exists():
        out["reason"] = "sin_sqlite"
        return out
    data = export_state_from_sqlite(src)
    import_state_dict(data, opl_defaults=())
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(clave, valor) VALUES (?, ?)",
            ("sqlite_migrated_at", _now()),
        )
    out["imported"] = True
    out["reason"] = "ok"
    return out


def ensure_ready(opl_defaults: Sequence[Sequence[Any]] = ()) -> Dict[str, Any]:
    global _READY
    with _READY_LOCK:
        if _READY:
            return {"ready": True, "skipped": True}
        init_schema()
        info: Dict[str, Any] = {"db": db_label(), "engine": engine_name()}
        if engine_name() == "mysql" and is_operationally_empty():
            info["sqlite"] = migrate_from_sqlite(opl_defaults=opl_defaults)
        if is_operationally_empty():
            info["json"] = migrate_from_json(opl_defaults=opl_defaults, backup=True)
        else:
            info.setdefault("json", {"imported": False, "reason": "bd_ya_tiene_datos"})
        if opl_defaults:
            with get_conn() as conn:
                if _count(conn, "opl_propietario") == 0:
                    _seed_opl_defaults(conn, opl_defaults)
        _READY = True
        info["ready"] = True
        return info


# ─── OPL propietario ─────────────────────────────────────────────

def list_opl_config() -> List[List[Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT propietario, opl, extra FROM opl_propietario ORDER BY id"
        ).fetchall()
    return [[r["propietario"], r["opl"], r["extra"]] for r in rows]


def _invalidate_opl_cache() -> None:
    global _OPL_MAP_CACHE
    _OPL_MAP_CACHE = None


def opl_map() -> Dict[str, str]:
    global _OPL_MAP_CACHE
    if _OPL_MAP_CACHE is not None:
        return _OPL_MAP_CACHE
    out: Dict[str, str] = {}
    for prop, opl, _extra in list_opl_config():
        key = str(prop or "").strip().upper()
        if key:
            out[key] = str(opl or "").strip()
    _OPL_MAP_CACHE = out
    return out


def upsert_opl(propietario: str, opl: str, extra: int = 0) -> None:
    prop = (propietario or "").strip().upper()
    if not prop:
        return
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO opl_propietario(propietario, opl, extra, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(propietario) DO UPDATE SET
                opl=excluded.opl,
                updated_at=excluded.updated_at
            """,
            (prop, (opl or "").strip(), 1 if extra else 0, _now()),
        )
    _invalidate_opl_cache()


def delete_opl_by_index(idx: int) -> None:
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM opl_propietario ORDER BY id").fetchall()
        if 0 <= idx < len(rows):
            conn.execute("DELETE FROM opl_propietario WHERE id=?", (rows[idx]["id"],))
    _invalidate_opl_cache()


# ─── OPL Beneficio (aparte de Despacho) ───────────────────────────

_BENEFICIO_OPL_MAP_CACHE: Optional[Dict[str, str]] = None


def list_beneficio_opl_config() -> List[List[Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT propietario, opl, extra FROM beneficio_opl_propietario ORDER BY id"
        ).fetchall()
    return [[r["propietario"], r["opl"], r["extra"]] for r in rows]


def _invalidate_beneficio_opl_cache() -> None:
    global _BENEFICIO_OPL_MAP_CACHE
    _BENEFICIO_OPL_MAP_CACHE = None


def beneficio_opl_map() -> Dict[str, str]:
    global _BENEFICIO_OPL_MAP_CACHE
    if _BENEFICIO_OPL_MAP_CACHE is not None:
        return _BENEFICIO_OPL_MAP_CACHE
    out: Dict[str, str] = {}
    for prop, opl, _extra in list_beneficio_opl_config():
        key = str(prop or "").strip().upper()
        if key:
            out[key] = str(opl or "").strip()
    _BENEFICIO_OPL_MAP_CACHE = out
    return out


def upsert_beneficio_opl(propietario: str, opl: str, extra: int = 0) -> None:
    prop = (propietario or "").strip().upper()
    if not prop:
        return
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO beneficio_opl_propietario(propietario, opl, extra, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(propietario) DO UPDATE SET
                opl=excluded.opl,
                updated_at=excluded.updated_at
            """,
            (prop, (opl or "").strip(), 1 if extra else 0, _now()),
        )
    _invalidate_beneficio_opl_cache()


def seed_beneficio_opl_from_despacho() -> int:
    """Si Beneficio no tiene config, copia la de Despacho para arrancar igual."""
    if list_beneficio_opl_config():
        return 0
    origen = list_opl_config()
    for row in origen:
        if row and row[0]:
            upsert_beneficio_opl(row[0], row[1] if len(row) > 1 else "", row[2] if len(row) > 2 else 0)
    return len(origen)


# ─── OPL particulares ────────────────────────────────────────────

def list_opls_particulares() -> List[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT opl FROM opl_particular ORDER BY orden, opl"
        ).fetchall()
    return [r["opl"] for r in rows]


def set_opls_particulares(nombres: Sequence[str]) -> List[str]:
    now = _now()
    seen = set()
    clean: List[str] = []
    for n in nombres or []:
        s = str(n or "").strip()
        if not s:
            continue
        key = s.upper()
        if key in seen:
            continue
        seen.add(key)
        clean.append(s)
    with get_conn() as conn:
        conn.execute("DELETE FROM opl_particular")
        for i, name in enumerate(clean):
            conn.execute(
                "INSERT INTO opl_particular(opl, orden, created_at) VALUES (?, ?, ?)",
                (name, i, now),
            )
    return clean


# ─── Asignado congelado ──────────────────────────────────────────

def get_asignado_bag() -> Dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT fecha, turno, payload FROM asignado_congelado"
        ).fetchall()
    out: Dict[str, Any] = {}
    for r in rows:
        out[f"{r['fecha']}|{r['turno']}"] = _json_loads(r["payload"], {})
    return out


def get_asignado(fecha: str, turno: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM asignado_congelado WHERE fecha=? AND turno=?",
            (fecha, turno),
        ).fetchone()
    if not row:
        return None
    data = _json_loads(row["payload"], None)
    return data if isinstance(data, dict) else None


def upsert_asignado(fecha: str, turno: str, payload: Dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO asignado_congelado(fecha, turno, payload, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (fecha, turno, _json_dumps(payload), _now()),
        )


def replace_asignado_bag(bag: Dict[str, Any]) -> None:
    now = _now()
    with get_conn() as conn:
        conn.execute("DELETE FROM asignado_congelado")
        for key, payload in (bag or {}).items():
            raw = str(key or "")
            if "|" not in raw:
                continue
            fecha, turno = raw.split("|", 1)
            conn.execute(
                """
                INSERT OR REPLACE INTO asignado_congelado(fecha, turno, payload, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (fecha.strip(), turno.strip(), _json_dumps(payload), now),
            )


def prune_asignado_before(cutoff: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM asignado_congelado WHERE fecha < ?", (cutoff,))


def delete_asignado_fecha(fecha: str) -> List[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT fecha, turno FROM asignado_congelado WHERE fecha=?",
            (fecha,),
        ).fetchall()
        conn.execute("DELETE FROM asignado_congelado WHERE fecha=?", (fecha,))
    return [f"{r['fecha']}|{r['turno']}" for r in rows]


# ─── Etiquetas impresas ──────────────────────────────────────────

def get_etiquetas_map() -> Dict[str, Dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT fecha, codigo, impresa_en FROM etiqueta_impresa"
        ).fetchall()
    out: Dict[str, Dict[str, str]] = {}
    for r in rows:
        out.setdefault(r["fecha"], {})[r["codigo"]] = r["impresa_en"]
    return out


def get_etiquetas_fecha(fecha: str) -> Dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT codigo, impresa_en FROM etiqueta_impresa WHERE fecha=?",
            (fecha,),
        ).fetchall()
    return {r["codigo"]: r["impresa_en"] for r in rows}


def marcar_etiquetas(
    fecha: str,
    codigos: Iterable[str],
    impresa_en: str,
    prune_keep: Optional[int] = None,
) -> int:
    rows = []
    for c in codigos:
        cod = str(c or "").strip().upper()
        if cod:
            rows.append((fecha, cod, impresa_en))
    if not rows:
        return 0
    sql = """
        INSERT OR REPLACE INTO etiqueta_impresa(fecha, codigo, impresa_en)
        VALUES (?, ?, ?)
    """
    with get_conn() as conn:
        if hasattr(conn, "executemany"):
            conn.executemany(sql, rows)
        else:
            for row in rows:
                conn.execute(sql, row)
        if prune_keep:
            fechas = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT fecha FROM etiqueta_impresa ORDER BY fecha"
                ).fetchall()
            ]
            if len(fechas) > prune_keep:
                drop = fechas[:-prune_keep]
                placeholders = ",".join("?" * len(drop))
                conn.execute(
                    f"DELETE FROM etiqueta_impresa WHERE fecha IN ({placeholders})",
                    drop,
                )
    return len(rows)


def limpiar_etiquetas_fecha(fecha: str, codigos: Optional[Sequence[str]] = None) -> Dict[str, int]:
    with get_conn() as conn:
        if not codigos:
            n = conn.execute(
                "SELECT COUNT(*) FROM etiqueta_impresa WHERE fecha=?", (fecha,)
            ).fetchone()[0]
            conn.execute("DELETE FROM etiqueta_impresa WHERE fecha=?", (fecha,))
            return {"limpiadas": int(n), "quedan": 0}
        n = 0
        for c in codigos:
            cod = str(c or "").strip().upper()
            if not cod:
                continue
            cur = conn.execute(
                "DELETE FROM etiqueta_impresa WHERE fecha=? AND codigo=?",
                (fecha, cod),
            )
            n += cur.rowcount
        quedan = conn.execute(
            "SELECT COUNT(*) FROM etiqueta_impresa WHERE fecha=?", (fecha,)
        ).fetchone()[0]
    return {"limpiadas": int(n), "quedan": int(quedan)}


def prune_etiquetas_keep_last_n(n: int) -> None:
    with get_conn() as conn:
        fechas = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT fecha FROM etiqueta_impresa ORDER BY fecha"
            ).fetchall()
        ]
        if len(fechas) <= n:
            return
        drop = fechas[:-n]
        placeholders = ",".join("?" * len(drop))
        conn.execute(
            f"DELETE FROM etiqueta_impresa WHERE fecha IN ({placeholders})",
            drop,
        )


def prune_etiquetas_keep_fechas(keep: Iterable[str]) -> None:
    keep_list = [str(x) for x in keep]
    with get_conn() as conn:
        if not keep_list:
            conn.execute("DELETE FROM etiqueta_impresa")
            return
        placeholders = ",".join("?" * len(keep_list))
        conn.execute(
            f"DELETE FROM etiqueta_impresa WHERE fecha NOT IN ({placeholders})",
            keep_list,
        )


# ─── Settings ────────────────────────────────────────────────────

def get_setting(clave: str, default: Optional[str] = None) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT valor FROM app_setting WHERE clave=?", (clave,)
        ).fetchone()
    return row["valor"] if row else default


def set_setting(clave: str, valor: Any) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_setting(clave, valor) VALUES (?, ?)",
            (clave, "" if valor is None else str(valor)),
        )


# ─── Adicionales ─────────────────────────────────────────────────

def get_adicionales(fecha: str) -> List[Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM adicionales_dia WHERE fecha=?", (fecha,)
        ).fetchone()
    data = _json_loads(row["payload"] if row else None, [])
    return data if isinstance(data, list) else []


def set_adicionales(fecha: str, piezas: Sequence[Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO adicionales_dia(fecha, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (fecha, _json_dumps(list(piezas or [])), _now()),
        )


def clear_adicionales(fecha: Optional[str] = None) -> None:
    with get_conn() as conn:
        if fecha:
            conn.execute("DELETE FROM adicionales_dia WHERE fecha=?", (fecha,))
        else:
            conn.execute("DELETE FROM adicionales_dia")


def adicionales_bag() -> Dict[str, List[Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT fecha, payload FROM adicionales_dia").fetchall()
    out: Dict[str, List[Any]] = {}
    for r in rows:
        data = _json_loads(r["payload"], [])
        out[r["fecha"]] = data if isinstance(data, list) else []
    return out


# ─── Progreso / histórico ────────────────────────────────────────

def get_opl_progreso() -> List[Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM opl_progreso_snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone()
    data = _json_loads(row["payload"] if row else None, [])
    return data if isinstance(data, list) else []


def save_opl_progreso(items: Sequence[Any]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM opl_progreso_snapshot")
        conn.execute(
            "INSERT INTO opl_progreso_snapshot(guardado_en, payload) VALUES (?, ?)",
            (_now(), _json_dumps(list(items or []))),
        )


def clear_opl_progreso() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM opl_progreso_snapshot")


def append_historico(item: Any, fecha: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO historico_operacion(fecha, payload, created_at)
            VALUES (?, ?, ?)
            """,
            (fecha, _json_dumps(item), _now()),
        )


def list_historico() -> List[Any]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT payload FROM historico_operacion ORDER BY id"
        ).fetchall()
    out = []
    for r in rows:
        item = _json_loads(r["payload"], None)
        if item is not None:
            out.append(item)
    return out


def clear_historico() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM historico_operacion")


# ─── Overrides (preparado; no se aplica aún a consultas SIRT) ────

def add_override(
    fecha: str,
    codigo: str,
    campo: str,
    valor: Optional[str] = None,
    usuario: str = "",
    motivo: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO override_pieza(fecha, codigo, campo, valor, usuario, motivo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fecha,
                str(codigo or "").strip().upper(),
                str(campo or "").strip(),
                valor,
                usuario or "",
                motivo or "",
                _now(),
            ),
        )
        return int(cur.lastrowid)


def list_overrides(fecha: str, codigo: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if codigo:
            rows = conn.execute(
                """
                SELECT id, fecha, codigo, campo, valor, usuario, motivo, created_at
                FROM override_pieza
                WHERE fecha=? AND codigo=?
                ORDER BY id
                """,
                (fecha, str(codigo).strip().upper()),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, fecha, codigo, campo, valor, usuario, motivo, created_at
                FROM override_pieza
                WHERE fecha=?
                ORDER BY id
                """,
                (fecha,),
            ).fetchall()
    return [dict(r) for r in rows]


def export_state_dict() -> Dict[str, Any]:
    """Reconstruye la forma del JSON para compatibilidad con _load_state/_opl_map."""
    cols = get_setting("etiquetas_rollo_columnas", "4")
    try:
        cols_int = int(cols) if cols is not None else 4
    except ValueError:
        cols_int = 4
    fin = get_setting("operacion_finalizada", "0") == "1"
    return {
        "opl_config": list_opl_config(),
        "opl_progreso": get_opl_progreso(),
        "historico": list_historico(),
        "operacion_finalizada": fin,
        "asignado_congelado": get_asignado_bag(),
        "opls_particulares": list_opls_particulares(),
        "adicionales_por_fecha": adicionales_bag(),
        "etiquetas_impresas": get_etiquetas_map(),
        "etiquetas_rollo_columnas": 2 if cols_int == 2 else 4,
    }


TABLES = (
    "opl_propietario",
    "beneficio_opl_propietario",
    "opl_particular",
    "asignado_congelado",
    "etiqueta_impresa",
    "app_setting",
    "adicionales_dia",
    "opl_progreso_snapshot",
    "historico_operacion",
    "override_pieza",
)


def stats() -> Dict[str, Any]:
    with get_conn() as conn:
        counts = {t: _count(conn, t) for t in TABLES}
        migrated = conn.execute(
            "SELECT valor FROM schema_meta WHERE clave=?", ("json_migrated_at",)
        ).fetchone()
    return {
        "db": db_label(),
        "engine": engine_name(),
        "json_migrated_at": migrated["valor"] if migrated else None,
        "counts": counts,
    }
