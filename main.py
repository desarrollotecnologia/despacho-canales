"""
Despacho de Canales — Colbeef
Backend FastAPI con conexión directa a PostgreSQL (solo lectura)
v1.0 — Medias canales: Media Canal 1 (sufijo -1001) y Media Canal 2 (sufijo -1002)
"""
from fastapi import FastAPI, HTTPException, Header, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from io import BytesIO
from threading import Lock
from typing import Optional, List
from urllib.parse import quote

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
import apps_script_local
import usability

load_dotenv()

app = FastAPI(title="Despacho de Canales Colbeef", version="1.0")

# ─── IDs tipo_parte_producto para canales (verificados en BD sirt) ──
ID_MC1 = 4   # "Media Canal 1"
ID_MC2 = 5   # "Media Canal 2 Cola"
IDS_CANAL = (ID_MC1, ID_MC2)

# Turno según weekday de la fecha (Python: lunes=0 ... domingo=6)
TURNO_POR_WEEKDAY = {0: "LxM", 1: "MxM", 2: "MxJ", 3: "JxV", 4: "VxS", 5: "SxD", 6: "DxL"}


def turno_de_fecha(fecha_str: Optional[str] = None) -> str:
    d = date.fromisoformat(fecha_str) if fecha_str else date.today()
    return TURNO_POR_WEEKDAY[d.weekday()]


def resolver_turno(fecha_str: Optional[str], turno: Optional[str]) -> Optional[str]:
    """Si turno viene vacío o None, usa el de la fecha. 'Todos' = sin filtro."""
    if turno is None:
        return turno_de_fecha(fecha_str)
    t = str(turno).strip()
    if t.lower() == "todos":
        return None
    if t == "":
        return turno_de_fecha(fecha_str)
    return t


TURNOS_CODIGO = ("DxL", "LxM", "MxM", "MxJ", "JxV", "VxS", "SxD")

# Corte horario: pistoleo ≥ esta hora = salida adicional (desglose).
# Override: CANALES_SALIDA_ADICIONAL_HORA / CANALES_SALIDA_ADICIONAL_MINUTO
# Pendientes y progreso cuentan normales + adicionales por igual.
def get_salida_adicional_corte():
    try:
        hora = int(os.getenv("CANALES_SALIDA_ADICIONAL_HORA", "15"))
    except ValueError:
        hora = 15
    try:
        minuto = int(os.getenv("CANALES_SALIDA_ADICIONAL_MINUTO", "20"))
    except ValueError:
        minuto = 20
    return {
        "hora": max(0, min(23, hora)),
        "minuto": max(0, min(59, minuto)),
    }


def get_salida_adicional_corte_label() -> str:
    c = get_salida_adicional_corte()
    return f"{c['hora']:02d}:{c['minuto']:02d}"


def parse_hora_desde_celda(celda) -> Optional[tuple]:
    """Devuelve (h, m) si la celda trae hora; None si no."""
    if celda is None or celda == "":
        return None
    if isinstance(celda, datetime):
        return (celda.hour, celda.minute)
    s = str(celda).strip()
    if not s:
        return None
    m = re.search(r"(?:^|[T\s])(\d{1,2}):(\d{2})(?::\d{2})?", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d.hour, d.minute)
    except Exception:
        return None


def es_salida_adicional_por_hora(celda, corte=None) -> bool:
    """
    Salida física adicional: fecha_salida con hora >= corte (defecto 15:20).
    Sin hora → no se marca adicional.
    """
    hm = parse_hora_desde_celda(celda)
    if not hm:
        return False
    corte = corte or get_salida_adicional_corte()
    return (hm[0] * 60 + hm[1]) >= (int(corte["hora"]) * 60 + int(corte["minuto"]))


# Destinos/puestos de stock interno (no despacho a plaza), igual idea que Gestor Vísceras
PUESTOS_EXCLUIDOS = {
    "01305", "03105", "05200", "12157", "379P",
    "CAVA AJR", "CAVA FORTUNATO", "CAVA MIREYA", "CAVA.", "CAVA",
    "CCARNES CAVA", "OLIMPICA", "RH32", "DRA CAVA", "CAVA WO",
    "CAVAYERSON", "CAVA JUDITH", "CAVA CV", "CAVA EMERGENCIA",
}

# Joins de ruta (puesto/zona). Sin filtrar por fecha de programación.
# Importante: en SIRT pp.id NO es único (MC1=4, MC2=5); siempre cruzar también id_producto.
SQL_JOINS_LOGISTICA = """
        LEFT JOIN trazabilidad_proceso.producto_empresa pe
            ON pe.id_producto::text = pp.id_producto::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3
            ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe
            ON ppe.id_producto::text = pp.id_producto::text
           AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel
            ON ppel.id_parte_producto_empresa = ppe.id
        LEFT JOIN organizaciones.sucursal s
            ON s.id = ppel.id_local
        LEFT JOIN trazabilidad_proceso.destino de
            ON de.id = s.id_destino
"""

# Solo piezas con fecha_programacion_despacho del día (dato real de despacho).
# El %s del JOIN es la fecha seleccionada.
SQL_JOINS_PROGRAMADO = """
        LEFT JOIN trazabilidad_proceso.producto_empresa pe
            ON pe.id_producto::text = pp.id_producto::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3
            ON e3.id = pe.id_empresa
        JOIN trazabilidad_proceso.parte_producto_empresa ppe
            ON ppe.id_producto::text = pp.id_producto::text
           AND ppe.id_parte_producto = pp.id
        JOIN trazabilidad_proceso.parte_producto_empresa_local ppel
            ON ppel.id_parte_producto_empresa = ppe.id
           AND ppel.fecha_programacion_despacho IS NOT NULL
           AND ppel.fecha_programacion_despacho::date = %s::date
        LEFT JOIN organizaciones.sucursal s
            ON s.id = ppel.id_local
        LEFT JOIN trazabilidad_proceso.destino de
            ON de.id = s.id_destino
"""

SQL_PPCR_JOIN = """
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr
            ON ppcr.id_parte_producto = pp.id
           AND ppcr.id_producto::text = pp.id_producto::text
"""

SQL_EXISTS_PROGRAMADO = """
          AND EXISTS (
            SELECT 1
            FROM trazabilidad_proceso.parte_producto_empresa ppe_p
            JOIN trazabilidad_proceso.parte_producto_empresa_local ppel_p
              ON ppel_p.id_parte_producto_empresa = ppe_p.id
            WHERE ppe_p.id_parte_producto = pp.id
              AND ppe_p.id_producto::text = pp.id_producto::text
              AND ppel_p.fecha_programacion_despacho IS NOT NULL
              AND ppel_p.fecha_programacion_despacho::date = %s::date
          )
"""


def patron_turno(turno: Optional[str]) -> Optional[str]:
    """Patrón ILIKE si el texto trae el código (ej. /JxV/). None = sin filtro."""
    return f"%{turno}%" if turno else None


def formatear_codigo_sucursal(codigo) -> str:
    """09404 → 9404 (como Gestor Vísceras)."""
    raw = str(codigo or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        try:
            return str(int(raw))
        except ValueError:
            return raw
    return raw


def parse_puesto_operacion(puesto_full: str) -> dict:
    """
    Descompone ruta SIRT:
    09404/Floridablanca/PLAZA DE MERCADO... /JxV/
    → puesto 9404, zona Floridablanca, dirección, turno JxV
    """
    raw = str(puesto_full or "").strip()
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    sin_turno = [p for p in parts if p not in TURNOS_CODIGO]
    codigo = formatear_codigo_sucursal(sin_turno[0] if sin_turno else "")
    zona = sin_turno[1].strip() if len(sin_turno) > 1 else ""
    direccion = sin_turno[2] if len(sin_turno) > 2 else ""
    turno = next((p for p in parts if p in TURNOS_CODIGO), "")
    etiqueta = f"{codigo} · {zona}" if codigo and zona else (codigo or zona or raw[:96])
    zona_key = zona.upper()
    if codigo and zona_key:
        clave = f"{str(codigo).upper()}|{zona_key}"
    elif codigo:
        clave = str(codigo).upper()
    else:
        clave = " ".join(raw.split()).upper()
    return {
        "codigo": codigo,
        "zona": zona,
        "direccion": direccion,
        "turno": turno,
        "etiqueta": etiqueta,
        "ruta": " / ".join(sin_turno),
        "ruta_completa": raw,
        "clave": clave,
    }


def construir_ruta(puesto: str, zona: str, direccion: str = "", turno: str = "") -> str:
    bits = [str(puesto or "").strip(), str(zona or "").strip(), str(direccion or "").strip()]
    bits = [b for b in bits if b]
    base = "/".join(bits)
    if turno:
        base = f"{base}/{turno}" if base else str(turno)
    return f"{base}/" if base else ""


def _ruta_cruda_desde_campos(con_destino: str, observaciones: str) -> str:
    """Prioriza con_destino u observaciones si ya traen la ruta con / (estilo Vísceras)."""
    for cand in (con_destino, observaciones):
        t = str(cand or "").strip()
        if not t or t.upper() in ("S", "N", "SI", "NO"):
            continue
        if "/" in t:
            return t
    return ""


def resolver_logistica_pieza(row: dict, turno_calendario: Optional[str] = None) -> dict:
    """Arma puesto/zona/turno como el Gestor de Vísceras."""
    con_dest = str(row.get("destino") or row.get("con_destino") or "").strip()
    obs = str(row.get("observaciones") or "").strip()
    suc = str(row.get("sucursal_origen") or row.get("sucursal") or "").strip()
    zona_db = str(row.get("destino_real") or row.get("zona") or "").strip()
    dir_db = str(row.get("direccion_entrega") or row.get("direccion") or "").strip()

    ruta_cruda = _ruta_cruda_desde_campos(con_dest, obs)
    if ruta_cruda:
        po = parse_puesto_operacion(ruta_cruda)
        puesto = po["codigo"] or formatear_codigo_sucursal(suc)
        zona = po["zona"] or zona_db
        direccion = po["direccion"] or dir_db
        turno = po["turno"] or (turno_calendario or "")
    else:
        puesto = formatear_codigo_sucursal(suc) or suc
        zona = zona_db
        direccion = dir_db
        turno = turno_calendario or ""

    ruta = construir_ruta(puesto, zona, direccion, turno)
    po2 = parse_puesto_operacion(ruta)
    return {
        "puesto": puesto,
        "zona": zona,
        "direccion": direccion,
        "turno_ruta": turno,
        "ruta": ruta,
        "etiqueta": po2["etiqueta"] or (f"{puesto} · {zona}" if puesto or zona else "Sin ruta"),
        "clave": po2["clave"] or "SIN RUTA",
    }


def es_destino_despacho(log: dict) -> bool:
    """Excluye stock de cava / puestos internos."""
    zona = str(log.get("zona") or "").strip().upper()
    puesto = str(log.get("puesto") or "").strip().upper()
    if not zona and not puesto:
        return False
    if zona in ("CAVA", "PLANTA"):
        return False
    if puesto.startswith("CAVA") or puesto in {p.upper() for p in PUESTOS_EXCLUIDOS}:
        return False
    return True


def pasa_filtro_turno(log: dict, turno: Optional[str]) -> bool:
    if not turno:
        return True
    return str(log.get("turno_ruta") or "").upper() == str(turno).upper()


def enriquecer_logistica(rows: List[dict], fecha_filtro: str, turno: Optional[str], solo_despacho: bool = True) -> List[dict]:
    """Aplica parseo de ruta + filtro de turno (calendario si la ruta no trae código)."""
    cal = turno or turno_de_fecha(fecha_filtro)
    out = []
    for r in rows:
        log = resolver_logistica_pieza(r, cal)
        if solo_despacho and not es_destino_despacho(log):
            continue
        if not pasa_filtro_turno(log, turno):
            continue
        r.update(log)
        # Destino visible = zona (Floridablanca), no la bandera "S"
        r["destino"] = log["zona"] or log["ruta"] or r.get("destino")
        out.append(r)
    return out


def sql_filtro_turno(col: str = "pp.con_destino") -> str:
    """Filtro SQL legacy (ILIKE). Preferir enriquecer_logistica en despacho/planilla."""
    return f"(%s::text IS NULL OR {col} ILIKE %s::text)"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_CONFIG = {
    "host":            os.getenv("POSTGRES_HOST", "10.64.1.47"),
    "port":            int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":          os.getenv("POSTGRES_DB", "sirt"),
    "user":            os.getenv("POSTGRES_USER", "acceso"),
    "password":        os.getenv("POSTGRES_PASSWORD", ""),
    "connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5")),
    "options": f"-c statement_timeout={os.getenv('POSTGRES_STATEMENT_TIMEOUT_MS', '20000')}",
}

_POOL = None
_POOL_LOCK = Lock()
_CACHE = {}
_CACHE_LOCK = Lock()
# Equilibrio velocidad / frescura: máx. ~1 min; Refrescar invalida de inmediato
CACHE_TTL_SEG = 60


def get_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadedConnectionPool(2, 10, **DB_CONFIG)
        return _POOL


def get_conn():
    try:
        return get_pool().getconn()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"No se pudo conectar a la BD: {str(e)}")


def put_conn(conn):
    try:
        get_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def query(sql: str, params=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def cache_get(key):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (time.time() - hit[0]) < CACHE_TTL_SEG:
            return hit[1]
    return None


def cache_set(key, value):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def cache_invalidate_fecha(fecha: Optional[str] = None):
    """Borra caché de una fecha (o toda si fecha es None)."""
    with _CACHE_LOCK:
        if not fecha:
            _CACHE.clear()
            return 0
        drop = [k for k in _CACHE if isinstance(k, tuple) and fecha in k]
        for k in drop:
            del _CACHE[k]
        return len(drop)


def es_refresh(refresh: Optional[str] = None) -> bool:
    return str(refresh or "").strip().lower() in ("1", "true", "yes", "si", "sí")



def safe_query(sql: str, params=None, label: str = "consulta"):
    try:
        return query(sql, params)
    except Exception as e:
        print(f"[WARN] {label} falló: {e}")
        return []


def safe_query_many(tasks):
    if not tasks:
        return {}
    max_workers = min(len(tasks), 6)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(safe_query, sql, params, label): key
            for key, sql, params, label in tasks
        }
        return {key: future.result() for future, key in futures.items()}


def serializable(rows):
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if isinstance(v, (date, datetime)):
                clean[k] = v.isoformat()
            else:
                clean[k] = v
        result.append(clean)
    return result


def tipo_canal_label(id_tipo: int) -> str:
    if id_tipo == ID_MC1:
        return "Media Canal 1"
    if id_tipo == ID_MC2:
        return "Media Canal 2"
    return str(id_tipo)


def codigo_completo_canal(codigo: str, id_tipo: int) -> str:
    """
    Devuelve el código completo con sufijo numérico:
    Media Canal 1 → ...-1001
    Media Canal 2 → ...-1002
    Si la BD ya trae el sufijo, se respeta.
    """
    codigo = (codigo or "").strip()
    if not codigo:
        return ""
    partes = codigo.split("-")
    # Ya trae sufijo 1001/1002
    if len(partes) >= 3 and partes[-1] in ("1001", "1002", "001", "002"):
        return codigo
    sufijo_num = "1001" if id_tipo == ID_MC1 else ("1002" if id_tipo == ID_MC2 else "")
    if not sufijo_num:
        return codigo
    return f"{codigo}-{sufijo_num}"


def enriquecer_codigo(row: dict) -> dict:
    """Código completo tipo 2608-09418-1001 (nunca letras MC1/MC2)."""
    id_tipo = row.get("id_tipo", 0)
    codigo = row.get("codigo") or ""
    completo = codigo_completo_canal(codigo, id_tipo)
    row["sufijo"] = tipo_canal_label(id_tipo)
    row["codigo_completo"] = completo
    row["codigo_sufijo"] = completo
    row["codigo"] = completo  # el frontend muestra siempre el completo
    return row


def consultar_salidas_fisicas(fecha_filtro: str) -> dict:
    """
    Salidas físicas del día **programado** (MC1/MC2).
    Parte en normales (< corte) vs adicionales (≥ corte, defecto 15:20).
    El progreso / pendientes cuentan las dos: aquí solo se desglosa.
    """
    corte = get_salida_adicional_corte()
    corte_lbl = get_salida_adicional_corte_label()
    sql = f"""
        WITH programadas AS (
            SELECT DISTINCT
                pp.id_producto::text AS id_producto,
                pp.id AS id_parte_producto,
                pp.id_tipo_parte_producto
            FROM trazabilidad_proceso.parte_producto pp
            WHERE pp.id_tipo_parte_producto IN %s
              {SQL_EXISTS_PROGRAMADO}
        ),
        ultimo AS (
            SELECT DISTINCT ON (p.id_producto, p.id_parte_producto)
                p.id_producto AS codigo,
                p.id_tipo_parte_producto AS id_tipo,
                tpp.nombre AS tipo_nombre,
                COALESCE(NULLIF(TRIM(e3.nombre), ''), 'Sin propietario') AS propietario,
                c.nombre AS cava,
                r.nombre AS riel,
                mov.fecha_ingreso,
                mov.fecha_salida,
                COALESCE(NULLIF(TRIM(de.nombre), ''), NULLIF(TRIM(s.nombre), ''), '') AS zona
            FROM programadas p
            JOIN trazabilidad_proceso.parte_producto_cava_riel mov
              ON mov.id_parte_producto = p.id_parte_producto
             AND mov.id_producto::text = p.id_producto
            JOIN trazabilidad_proceso.tipo_parte_producto tpp
              ON tpp.id = p.id_tipo_parte_producto
            LEFT JOIN trazabilidad_proceso.cava c ON c.id = mov.id_cava
            LEFT JOIN trazabilidad_proceso.riel r ON r.id = mov.id_riel
            LEFT JOIN trazabilidad_proceso.producto_empresa pe
              ON pe.id_producto::text = p.id_producto AND pe.activo = true
            LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
            LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe
              ON ppe.id_producto::text = p.id_producto
             AND ppe.id_parte_producto = p.id_parte_producto
            LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel
              ON ppel.id_parte_producto_empresa = ppe.id
             AND ppel.fecha_programacion_despacho::date = %s::date
            LEFT JOIN organizaciones.sucursal s ON s.id = ppel.id_local
            LEFT JOIN trazabilidad_proceso.destino de ON de.id = s.id_destino
            WHERE mov.fecha_salida IS NOT NULL
              AND mov.fecha_salida::date = %s::date
            ORDER BY
                p.id_producto, p.id_parte_producto,
                mov.fecha_ingreso DESC NULLS LAST,
                mov.id DESC
        )
        SELECT * FROM ultimo
        ORDER BY fecha_salida DESC NULLS LAST, codigo
    """
    rows = serializable(
        safe_query(sql, (IDS_CANAL, fecha_filtro, fecha_filtro, fecha_filtro), "salidas_fisicas")
    )
    filas = []
    for r in rows:
        id_tipo = int(r.get("id_tipo") or 0)
        fs = r.get("fecha_salida")
        adicional = es_salida_adicional_por_hora(fs, corte)
        item = {
            "fecha_salida": str(fs) if fs is not None else "",
            "fecha_ingreso": str(r.get("fecha_ingreso") or ""),
            "codigo": codigo_completo_canal(r.get("codigo") or "", id_tipo),
            "id_tipo": id_tipo,
            "tipo": tipo_canal_label(id_tipo),
            "tipo_nombre": r.get("tipo_nombre") or "",
            "propietario": r.get("propietario") or "",
            "cava": r.get("cava") or "",
            "riel": r.get("riel") or "",
            "zona": r.get("zona") or "",
            "puesto": "",
            "adicional": adicional,
            "tipoSalida": "adicional" if adicional else "normal",
            "corteAdicional": corte_lbl,
        }
        enriquecer_codigo(item)
        item["opl"] = resolver_opl_de_propietario(item.get("propietario") or "")
        filas.append(item)

    normales = [f for f in filas if not f["adicional"]]
    adicionales = [f for f in filas if f["adicional"]]
    n_nor = len(normales)
    n_adi = len(adicionales)
    return {
        "success": True,
        "fecha": fecha_filtro,
        "corteAdicional": corte_lbl,
        "soloProgramadas": True,
        "totalFilas": len(filas),
        "totalNormales": n_nor,
        "totalAdicionales": n_adi,
        "totalCanalesNormales": round(n_nor * 0.5, 2),
        "totalMediasNormales": n_nor,
        "totalCanalesAdicionales": round(n_adi * 0.5, 2),
        "totalMediasAdicionales": n_adi,
        "totalCanalesSalida": round(len(filas) * 0.5, 2),
        "filas": filas,
        "filasNormales": normales,
        "filasAdicionales": adicionales,
        "nota": (
            f"Normales (< {corte_lbl}) + Adicionales (≥ {corte_lbl}) = total salidas. "
            "Ambas cuentan en el progreso / pendientes."
        ),
    }


def contar_adicionales_dashboard(fecha_filtro: str) -> dict:
    """Desglose visual: antes / adicionales / total (progreso usa el total)."""
    ck = ("salidas_fisicas", fecha_filtro, "v2_prog")
    out = cache_get(ck)
    if out is None:
        out = consultar_salidas_fisicas(fecha_filtro)
        cache_set(ck, out)
    return {
        "totalCanalesAdicionales": out.get("totalCanalesAdicionales") or 0,
        "totalMediasAdicionales": out.get("totalMediasAdicionales") or 0,
        "totalCanalesNormales": out.get("totalCanalesNormales") or 0,
        "totalMediasNormales": out.get("totalMediasNormales") or 0,
        "totalCanalesSalida": out.get("totalCanalesSalida") or 0,
        "corteAdicional": out.get("corteAdicional") or get_salida_adicional_corte_label(),
    }


def _nombres_opl_conocidos():
    cfg = apps_script_local.getOplConfig()
    names = {apps_script_local._as_str(o).upper() for o in (cfg.get("opls") or [])}
    names.add(apps_script_local.OPL_DEFAULT.upper())
    return names


def resolver_opl_de_propietario(propietario: str) -> str:
    prop = (propietario or "").strip()
    if not prop:
        return apps_script_local.OPL_DEFAULT
    conocidos = _nombres_opl_conocidos()
    prop_up = prop.upper()
    if prop_up in conocidos:
        return prop
    mapa = apps_script_local._opl_map(apps_script_local._load_state())
    return apps_script_local._resolver_opl(prop, mapa)


def consultar_canales_planilla(fecha_filtro: str, turno: Optional[str]):
    """
    Medias en cava programadas para la fecha (fecha_programacion_despacho),
    con ruta puesto/zona como Gestor Vísceras.
    """
    sql = f"""
        WITH programadas AS MATERIALIZED (
            SELECT pp.*
            FROM trazabilidad_proceso.parte_producto pp
            WHERE pp.id_tipo_parte_producto IN %s
              {SQL_EXISTS_PROGRAMADO}
        ),
        actuales AS MATERIALIZED (
            SELECT DISTINCT ON (pp.id_producto, pp.id)
                pp.*,
                mov.id_cava AS movimiento_id_cava,
                mov.id_riel AS movimiento_id_riel
            FROM programadas pp
            JOIN trazabilidad_proceso.parte_producto_cava_riel mov
              ON mov.id_parte_producto = pp.id
             AND mov.id_producto::text = pp.id_producto::text
            WHERE mov.fecha_salida IS NULL
            ORDER BY
                pp.id_producto,
                pp.id,
                mov.fecha_ingreso DESC NULLS LAST,
                mov.id DESC
        )
        SELECT DISTINCT ON (pp.id_producto, pp.id)
            pp.id_producto                          AS codigo,
            tpp.id                                  AS id_tipo,
            COALESCE(NULLIF(TRIM(e3.nombre), ''), 'Sin propietario') AS propietario,
            c.nombre                                AS cava,
            r.nombre                                AS riel,
            pp.con_destino                          AS con_destino,
            pp.observaciones                        AS observaciones,
            s.nombre                                AS sucursal_origen,
            s.direccion                             AS direccion_entrega,
            de.nombre                               AS destino_real
        FROM actuales pp
        JOIN trazabilidad_proceso.tipo_parte_producto tpp ON tpp.id = pp.id_tipo_parte_producto
        LEFT JOIN trazabilidad_proceso.cava c ON c.id = pp.movimiento_id_cava
        LEFT JOIN trazabilidad_proceso.riel r ON r.id = pp.movimiento_id_riel
        {SQL_JOINS_PROGRAMADO}
        ORDER BY pp.id_producto, pp.id, e3.nombre NULLS LAST, c.orden NULLS LAST, r.nombre
    """
    rows = safe_query(
        sql,
        (IDS_CANAL, fecha_filtro, fecha_filtro),
        "planilla_puntos",
    )
    data = enriquecer_logistica(serializable(rows), fecha_filtro, turno, solo_despacho=True)
    for r in data:
        enriquecer_codigo(r)
        r["opl"] = resolver_opl_de_propietario(r.get("propietario") or "")
        r["destino"] = r.get("zona") or ""
    data.sort(key=lambda x: (
        str(x.get("zona") or "").upper(),
        str(x.get("puesto") or ""),
        str(x.get("propietario") or ""),
        str(x.get("codigo") or ""),
    ))
    return data


def obtener_piezas_programadas(fecha_filtro: str, turno: Optional[str]) -> List[dict]:
    """
    Listado único de medias programadas (fecha + turno), compartido por
    despachos / planilla / detalle. TTL = CACHE_TTL_SEG.
    Incluye adicionales locales del día que aún no están en SIRT.
    """
    ck = ("programados", fecha_filtro, turno, "v2_adic")
    hit = cache_get(ck)
    if hit is not None:
        return hit
    data = consultar_canales_planilla(fecha_filtro, turno)
    data = fusionar_adicionales(data, fecha_filtro, turno)
    cache_set(ck, data)
    return data


def _inferir_id_tipo(codigo: str, descripcion: str = "") -> int:
    c = (codigo or "").strip().upper()
    d = (descripcion or "").strip().upper()
    if c.endswith("-1002") or c.endswith("-002") or "MEDIA CANAL 2" in d or "MC2" in d:
        return ID_MC2
    if c.endswith("-1001") or c.endswith("-001") or "MEDIA CANAL 1" in d or "MC1" in d:
        return ID_MC1
    # Por defecto MC1 si no se puede inferir
    return ID_MC1


def _leer_filas_excel_adicionales(raw: bytes, nombre: str = "") -> List[list]:
    """Lee Excel adicionales (.xlsx/.xls). Datos desde fila 16 (1-based) como Vísceras."""
    nombre_l = (nombre or "").lower()
    # openpyxl para xlsx
    if nombre_l.endswith(".xlsx") or raw[:2] == b"PK":
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise HTTPException(status_code=500, detail="Falta openpyxl")
        wb = load_workbook(BytesIO(raw), data_only=True, read_only=True)
        ws = wb.active
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if i < 16:
                continue
            vals = list(row[:15]) if row else []
            while len(vals) < 15:
                vals.append("")
            rows.append(vals)
        return rows

    # .xls con xlrd si está disponible
    try:
        import xlrd
    except ImportError:
        raise HTTPException(
            status_code=400,
            detail="Para archivos .xls instala xlrd, o sube .xlsx",
        )
    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    rows = []
    for r in range(15, sheet.nrows):  # 0-based → desde fila 16
        vals = [sheet.cell_value(r, c) if c < sheet.ncols else "" for c in range(15)]
        rows.append(vals)
    return rows


def _detectar_tipo_fila_adicional(fila: list) -> str:
    col_j = str((fila[9] if len(fila) > 9 else "") or "").strip().upper()
    col_o = str((fila[14] if len(fila) > 14 else "") or "").strip().upper()
    if "QUEDA EN CAVA" in col_j:
        return "CANCELACION"
    if "CAMBIO DE DESTINO" in col_o:
        return "CAMBIO"
    return "ADICIONAL"


def _fila_excel_a_pieza(fila: list, fecha: str) -> Optional[dict]:
    codigo_raw = str((fila[1] if len(fila) > 1 else "") or "").strip()
    if not codigo_raw:
        return None
    desc = str((fila[2] if len(fila) > 2 else "") or "").strip()
    prop = str((fila[4] if len(fila) > 4 else "") or "").strip() or "Sin propietario"
    destino = str((fila[9] if len(fila) > 9 else "") or "").strip()
    cava = str((fila[10] if len(fila) > 10 else "") or "").strip()
    riel = str((fila[11] if len(fila) > 11 else "") or "").strip()
    id_tipo = _inferir_id_tipo(codigo_raw, desc)
    pieza = {
        "codigo": codigo_raw,
        "id_tipo": id_tipo,
        "propietario": prop,
        "cava": cava,
        "riel": riel,
        "zona": destino,
        "destino": destino,
        "puesto": "",
        "direccion": "",
        "ruta": destino,
        "etiqueta": destino or "Adicional",
        "clave": f"ADIC|{destino}|{prop}|{codigo_raw}",
        "con_destino": destino,
        "observaciones": str((fila[14] if len(fila) > 14 else "") or "").strip(),
        "origen": "adicional",
        "fecha_adicional": fecha,
    }
    enriquecer_codigo(pieza)
    pieza["opl"] = resolver_opl_de_propietario(prop)
    return pieza


def procesar_excel_adicionales(raw: bytes, nombre: str, fecha: str) -> dict:
    filas = _leer_filas_excel_adicionales(raw, nombre)
    filas = [f for f in filas if str((f[1] if len(f) > 1 else "") or "").strip()]
    adicionales, cancelaciones, cambios = [], [], []
    for f in filas:
        tipo = _detectar_tipo_fila_adicional(f)
        if tipo == "CANCELACION":
            cancelaciones.append(f)
        elif tipo == "CAMBIO":
            cambios.append(f)
        else:
            adicionales.append(f)

    piezas_nuevas = []
    for f in adicionales:
        p = _fila_excel_a_pieza(f, fecha)
        if p:
            piezas_nuevas.append(p)

    res_add = apps_script_local.agregarAdicionales(fecha, piezas_nuevas)

    codigos_cancel = [
        str((f[1] if len(f) > 1 else "") or "").strip()
        for f in cancelaciones
        if str((f[1] if len(f) > 1 else "") or "").strip()
    ]
    res_cancel = (
        apps_script_local.quitarAdicionalesPorCodigos(fecha, codigos_cancel)
        if codigos_cancel else {"eliminados": 0}
    )

    cambios_payload = []
    for f in cambios:
        codigo = str((f[1] if len(f) > 1 else "") or "").strip()
        dest = str((f[9] if len(f) > 9 else "") or "").strip()
        if codigo and dest:
            cambios_payload.append({"codigo": codigo, "zona": dest})
    res_cambio = (
        apps_script_local.actualizarDestinoAdicionales(fecha, cambios_payload)
        if cambios_payload else {"actualizados": 0}
    )

    cache_invalidate_fecha(fecha)
    return {
        "success": True,
        "nombreArchivo": nombre,
        "fecha": fecha,
        "totalAdicional": int(res_add.get("agregados") or 0),
        "ignorados": int(res_add.get("ignorados") or 0),
        "totalCancel": int(res_cancel.get("eliminados") or 0),
        "totalCambio": int(res_cambio.get("actualizados") or 0),
        "totalLocal": int(res_add.get("total") or 0),
        "mensaje": (
            f"✅ {int(res_add.get('agregados') or 0)} medias adicionales agregadas"
            + (f" ({int(res_add.get('ignorados') or 0)} ya existían)" if res_add.get("ignorados") else "")
            + (f". {int(res_cancel.get('eliminados') or 0)} cancelaciones aplicadas" if res_cancel.get("eliminados") else "")
            + (f". {int(res_cambio.get('actualizados') or 0)} cambios de destino" if res_cambio.get("actualizados") else "")
        ),
    }


def _codigos_sirt_programados_dia(fecha_filtro: str) -> set:
    """Códigos (completos) ya presentes en SIRT para la fecha de programación."""
    sql = f"""
        WITH programadas AS (
            SELECT DISTINCT
                pp.id_producto::text AS id_producto,
                pp.id AS id_parte,
                pp.id_tipo_parte_producto AS id_tipo
            FROM trazabilidad_proceso.parte_producto pp
            WHERE pp.id_tipo_parte_producto IN %s
              {SQL_EXISTS_PROGRAMADO}
        )
        SELECT id_producto, id_tipo FROM programadas
    """
    rows = safe_query(sql, (IDS_CANAL, fecha_filtro), "adicionales.codigos_sirt")
    out = set()
    for r in serializable(rows):
        code = codigo_completo_canal(r.get("id_producto") or "", int(r.get("id_tipo") or 0))
        if code:
            out.add(code.upper())
            # también base sin sufijo
            base = str(r.get("id_producto") or "").strip().upper()
            if base:
                out.add(base)
    return out


def _adicionales_efectivos(fecha: str) -> List[dict]:
    """Adicionales locales que aún no están en SIRT (evita doble conteo)."""
    adic = apps_script_local.getAdicionales(fecha).get("filas") or []
    if not adic:
        return []
    sirt = _codigos_sirt_programados_dia(fecha)
    out = []
    for a in adic:
        code = str(a.get("codigo") or "").strip().upper()
        if not code:
            continue
        if code in sirt:
            continue
        # base sin últimos 5 chars -1001
        partes = code.split("-")
        if len(partes) >= 3 and partes[-1] in ("1001", "1002", "001", "002"):
            base = "-".join(partes[:-1]).upper()
            if base in sirt:
                continue
        out.append(a)
    return out


def fusionar_adicionales(piezas: List[dict], fecha: str, turno: Optional[str]) -> List[dict]:
    """Une piezas SIRT + adicionales locales no duplicados."""
    adic = _adicionales_efectivos(fecha)
    if not adic:
        return piezas
    vistos = set()
    out = []
    for r in piezas:
        code = str(r.get("codigo") or "").strip().upper()
        if code:
            vistos.add(code)
        out.append(r)
    cal = turno or turno_de_fecha(fecha)
    for a in adic:
        code = str(a.get("codigo") or "").strip().upper()
        if not code or code in vistos:
            continue
        row = dict(a)
        log = resolver_logistica_pieza(row, cal)
        row.update(log)
        row["destino"] = row.get("zona") or row.get("destino") or ""
        row["opl"] = resolver_opl_de_propietario(row.get("propietario") or "")
        enriquecer_codigo(row)
        vistos.add(str(row.get("codigo") or "").strip().upper())
        out.append(row)
    out.sort(key=lambda x: (
        str(x.get("zona") or "").upper(),
        str(x.get("puesto") or ""),
        str(x.get("propietario") or ""),
        str(x.get("codigo") or ""),
    ))
    return out


def _aplicar_adicionales_a_planilla_opl(lista: List[dict], fecha: str) -> List[dict]:
    """Suma adicionales pendientes (locales, no en SIRT) a la lista por propietario."""
    adic = _adicionales_efectivos(fecha)
    if not adic:
        return lista
    by_prop = {r["propietario"]: dict(r) for r in lista}
    for a in adic:
        prop = (a.get("propietario") or "Sin propietario").strip() or "Sin propietario"
        id_tipo = int(a.get("id_tipo") or ID_MC1)
        row = by_prop.get(prop)
        if not row:
            row = {
                "propietario": prop,
                "mc1_pendiente": 0,
                "mc2_pendiente": 0,
                "mc1_despachado": 0,
                "mc2_despachado": 0,
                "total_pendiente": 0,
                "total_despachado": 0,
                "canales_pendiente": 0,
                "canales_despachado": 0,
                "total_inicial": 0,
                "progreso_pct": 0,
                "opl": resolver_opl_de_propietario(prop),
            }
            by_prop[prop] = row
        if id_tipo == ID_MC2:
            row["mc2_pendiente"] = int(row.get("mc2_pendiente") or 0) + 1
        else:
            row["mc1_pendiente"] = int(row.get("mc1_pendiente") or 0) + 1
        row["total_pendiente"] = int(row["mc1_pendiente"]) + int(row["mc2_pendiente"])
        row["canales_pendiente"] = row["total_pendiente"] * 0.5
        total_ini = row["total_pendiente"] + int(row.get("total_despachado") or 0)
        row["total_inicial"] = total_ini
        pct = round((int(row.get("total_despachado") or 0) / total_ini) * 100) if total_ini else 0
        if row["total_pendiente"] > 0:
            pct = min(99, pct)
        elif total_ini > 0:
            pct = 100
        row["progreso_pct"] = pct
    out = list(by_prop.values())
    out.sort(key=lambda x: x["total_pendiente"], reverse=True)
    return out


def agrupar_despachos_por_puesto(piezas: List[dict]) -> tuple:
    grupos = {}
    for r in piezas:
        clave = r.get("clave") or "SIN RUTA"
        g = grupos.get(clave)
        if not g:
            g = {
                "clave": clave,
                "puesto": r.get("puesto") or "",
                "zona": r.get("zona") or "",
                "direccion": r.get("direccion") or "",
                "ruta": r.get("ruta") or "",
                "etiqueta": r.get("etiqueta") or "",
                "destino": r.get("zona") or r.get("ruta") or "",
                "propietario": r.get("propietario") or "Sin propietario",
                "codigo": r.get("codigo"),
                "id_tipo": r.get("id_tipo"),
                "mc1": 0,
                "mc2": 0,
                "total_medias": 0,
                "props": {},
            }
            grupos[clave] = g
        if r.get("id_tipo") == ID_MC1:
            g["mc1"] += 1
        elif r.get("id_tipo") == ID_MC2:
            g["mc2"] += 1
        g["total_medias"] += 1
        prop = r.get("propietario") or "Sin propietario"
        g["props"][prop] = g["props"].get(prop, 0) + 1
        if not g.get("codigo"):
            g["codigo"] = r.get("codigo")
            g["id_tipo"] = r.get("id_tipo")

    data = []
    for g in grupos.values():
        if g["props"]:
            g["propietario"] = max(g["props"].items(), key=lambda kv: kv[1])[0]
        g["total_partes"] = g["total_medias"] * 0.5
        g["total_canales"] = g["total_partes"]
        g["mas_de_una"] = g["total_medias"] > 1
        g["clientes"] = len(g["props"])
        enriquecer_codigo(g)
        del g["props"]
        data.append(g)

    data.sort(key=lambda x: (str(x.get("zona") or "").upper(), str(x.get("puesto") or "")))
    total_partes = sum(float(r.get("total_partes") or 0) for r in data)
    totales = {
        "mc1": sum(r.get("mc1", 0) or 0 for r in data),
        "mc2": sum(r.get("mc2", 0) or 0 for r in data),
        "total_partes": total_partes,
        "total_canales": total_partes,
        "clientes": len(data),
        "puestos": len(data),
    }
    return data, totales


def resumen_planilla_puntos(items: List[dict]):
    por_opl = {}
    for r in items:
        opl = r.get("opl") or apps_script_local.OPL_DEFAULT
        bucket = por_opl.setdefault(opl, {"opl": opl, "mc1": 0, "mc2": 0, "total_medias": 0})
        if r.get("id_tipo") == ID_MC1:
            bucket["mc1"] += 1
        elif r.get("id_tipo") == ID_MC2:
            bucket["mc2"] += 1
        bucket["total_medias"] += 1
    total_general = sum(b["total_medias"] for b in por_opl.values()) * 0.5
    lista = []
    for bucket in por_opl.values():
        partes = bucket["total_medias"] * 0.5
        bucket["total_partes"] = partes
        bucket["totalJuegos"] = partes  # alias estilo Vísceras (aquí = canales)
        bucket["totalCanales"] = partes
        pct = (partes / total_general * 100) if total_general > 0 else 0
        bucket["porcentaje"] = round(pct, 1)
        lista.append(bucket)
    lista.sort(key=lambda x: (-x["total_partes"], x["opl"]))
    return lista, round(total_general, 2)


def _sanear_nombre_hoja(nombre: str, usados: set) -> str:
    raw = re.sub(r'[\\/*?:\[\]]', "-", str(nombre or "OPL")).strip() or "OPL"
    base = raw[:31]
    candidato = base
    i = 2
    while candidato.upper() in usados:
        suf = f"_{i}"
        candidato = (base[: max(1, 31 - len(suf))] + suf)[:31]
        i += 1
    usados.add(candidato.upper())
    return candidato


def _escribir_hoja_excel_opl(ws, opl: str, fecha: str, turno, filas: List[dict]):
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    verde = PatternFill("solid", fgColor="259C39")
    verde_claro = PatternFill("solid", fgColor="E8F5E9")
    blanco = Font(color="FFFFFF", bold=True, name="Calibri", size=11)
    titulo = Font(color="FFFFFF", bold=True, name="Calibri", size=16)
    normal = Font(name="Calibri", size=11)
    thin = Border(
        left=Side(style="thin", color="C8E6C9"),
        right=Side(style="thin", color="C8E6C9"),
        top=Side(style="thin", color="C8E6C9"),
        bottom=Side(style="thin", color="C8E6C9"),
    )

    ws.merge_cells("A1:F1")
    ws["A1"] = f"OPL {opl}"
    ws["A1"].font = titulo
    ws["A1"].fill = verde
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    for col in range(2, 7):
        ws.cell(1, col).fill = verde
    ws.row_dimensions[1].height = 28

    turno_txt = turno or "Todos"
    ws.merge_cells("A2:F2")
    ws["A2"] = (
        f"Medias canales pendientes · {fecha} · turno {turno_txt} · {len(filas)} registros"
    )
    ws["A2"].font = Font(name="Calibri", size=10, italic=True, color="374151")
    ws["A2"].alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 18

    headers = ["Código", "Propietario/Cliente", "Zona / Destino", "Puesto", "Cava", "Riel"]
    for i, h in enumerate(headers, 1):
        cell = ws.cell(3, i, h)
        cell.font = blanco
        cell.fill = verde
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin

    for idx, r in enumerate(filas, 4):
        vals = [
            r.get("codigo") or "",
            r.get("propietario") or "",
            r.get("zona") or r.get("destino") or "",
            r.get("puesto") or "",
            r.get("cava") or "",
            r.get("riel") or "",
        ]
        for col, val in enumerate(vals, 1):
            cell = ws.cell(idx, col, val)
            cell.font = normal
            cell.border = thin
            if idx % 2 == 0:
                cell.fill = verde_claro

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 36
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 14
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A3:F{max(3, 3 + len(filas))}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:3"


def construir_excel_opl(opl: str, fecha: str, turno, filas: List[dict]) -> BytesIO:
    try:
        from openpyxl import Workbook
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Falta openpyxl. En el servidor ejecuta: pip install openpyxl",
        )

    wb = Workbook()
    ws = wb.active
    ws.title = _sanear_nombre_hoja(opl or "OPL", set())
    _escribir_hoja_excel_opl(ws, opl, fecha, turno, filas)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def construir_excel_particulares(fecha: str, turno, por_opl: dict) -> BytesIO:
    """
    Un Excel con una hoja por OPL particular.
    Solo incluye lo pendiente (sin pistolear) de cada OPL.
    """
    try:
        from openpyxl import Workbook
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Falta openpyxl. En el servidor ejecuta: pip install openpyxl",
        )

    wb = Workbook()
    usados = set()
    primero = True
    for opl, filas in por_opl.items():
        titulo = _sanear_nombre_hoja(opl, usados)
        if primero:
            ws = wb.active
            ws.title = titulo
            primero = False
        else:
            ws = wb.create_sheet(titulo)
        _escribir_hoja_excel_opl(ws, opl, fecha, turno, filas)

    if primero:
        # Sin datos: hoja vacía informativa
        ws = wb.active
        ws.title = "Sin pendientes"
        _escribir_hoja_excel_opl(ws, "PARTICULARES", fecha, turno, [])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ═══════════════════════════════════════════════════════
# PING
# ═══════════════════════════════════════════════════════
@app.get("/api/ping")
def ping():
    try:
        rows = query("SELECT current_database() AS db, now() AS ts")
        return {"ok": True, "db": rows[0]["db"], "ts": str(rows[0]["ts"])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════
# TIPOS DE CANAL — detección automática de IDs reales
# ═══════════════════════════════════════════════════════
@app.get("/api/diagnostico")
def get_diagnostico():
    """Diagnóstico ultraligero: muestra los últimos 5 registros de medias canales."""
    sql_sample = """
        SELECT id, id_producto, id_tipo_parte_producto, con_destino
        FROM trazabilidad_proceso.parte_producto
        WHERE id_tipo_parte_producto IN (4,5)
        LIMIT 5
    """
    return {
        "muestra_registros": serializable(safe_query(sql_sample, label="diag.sample")),
        "ids_buscados": [4, 5],
        "nombres": ["Media Canal 1", "Media Canal 2 Cola"],
    }


@app.get("/api/tipos_canal")
def get_tipos_canal():
    """Devuelve TODOS los tipos de parte_producto para identificar cuáles son canales."""
    sql = """
        SELECT id, nombre, abreviatura
        FROM trazabilidad_proceso.tipo_parte_producto
        ORDER BY id
    """
    rows = safe_query(sql, label="tipos_canal")
    return {"tipos": serializable(rows)}


# ═══════════════════════════════════════════════════════
# CAVAS — canales en cava a una fecha dada
# ═══════════════════════════════════════════════════════
@app.get("/api/cavas")
def get_cavas(
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    refresh: Optional[str] = None,
):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    ck = ("cavas", fecha_filtro, turno, "v1")
    hit = cache_get(ck)
    if hit is not None:
        return hit
    turno_filtro = None  # turno se aplica en logística (despachos/planilla); cavas = stock en cava
    sql = """
        SELECT
            pp.id                               AS id_parte_producto,
            pp.id_producto                      AS codigo,
            tpp.id                              AS id_tipo,
            tpp.nombre                          AS descripcion,
            tpp.abreviatura                     AS abrev_tipo,
            pp.identificacion                   AS identificacion,
            pp.con_destino                      AS destino,
            pp.observaciones                    AS observaciones,
            pp.reetiquetado,
            c.nombre                            AS cava,
            c.orden                             AS cava_orden,
            r.nombre                            AS riel,
            ppcr.fecha_ingreso                  AS fecha_ingreso_cava,
            ppcr.fecha_salida                   AS fecha_salida_cava,
            ppcr.numero_informacion_ingreso     AS numero_ingreso,
            p.peso_animal_pie                   AS peso_pie_kg,
            e3.nombre                           AS propietario,
            s.nombre                            AS sucursal_origen,
            de.nombre                           AS destino_real
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.tipo_parte_producto tpp
            ON tpp.id = pp.id_tipo_parte_producto
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr
            ON ppcr.id_parte_producto = pp.id
           AND ppcr.id_producto::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.cava c
            ON c.id = ppcr.id_cava
        LEFT JOIN trazabilidad_proceso.riel r
            ON r.id = ppcr.id_riel
        LEFT JOIN trazabilidad_proceso.producto p
            ON p.id::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.producto_empresa pe
            ON pe.id_producto::text = p.id::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3
            ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe
            ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel
            ON ppel.id_parte_producto_empresa = ppe.id
        LEFT JOIN organizaciones.sucursal s
            ON s.id = ppel.id_local
        LEFT JOIN trazabilidad_proceso.destino de
            ON de.id = s.id_destino
        WHERE
            pp.id_tipo_parte_producto IN %s
            AND ppcr.fecha_salida IS NULL
            AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
            AND """ + sql_filtro_turno() + """
        ORDER BY
            c.orden NULLS LAST,
            r.nombre,
            pp.id_producto
    """
    rows = safe_query(sql, (IDS_CANAL, fecha_filtro, turno_filtro, turno_filtro), "cavas")
    data = serializable(rows)
    for r in data:
        enriquecer_codigo(r)
    payload = {"fecha": fecha_filtro, "turno": turno, "total": len(data), "data": data}
    cache_set(ck, payload)
    return payload


# ═══════════════════════════════════════════════════════
# DASHBOARD — resumen ejecutivo de canales en cava
# ═══════════════════════════════════════════════════════
@app.get("/api/dashboard")
def get_dashboard(
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    refresh: Optional[str] = None,
):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    ck = ("dashboard", fecha_filtro, turno, "prog_v1")
    hit = cache_get(ck)
    if hit is not None:
        return hit
    sql_totales = f"""
        SELECT
            COUNT(*) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
            COUNT(*) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
            COUNT(*) AS total_partes,
            COUNT(DISTINCT SPLIT_PART(pp.id_producto, '-', 1)||'-'||SPLIT_PART(pp.id_producto, '-', 2)) AS animales
        FROM trazabilidad_proceso.parte_producto pp
        {SQL_PPCR_JOIN}
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          {SQL_EXISTS_PROGRAMADO}
    """
    sql_cavas = f"""
        SELECT c.nombre AS cava,
               COUNT(*) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
               COUNT(*) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
               COUNT(*) AS total
        FROM trazabilidad_proceso.parte_producto pp
        {SQL_PPCR_JOIN}
        JOIN trazabilidad_proceso.cava c ON c.id = ppcr.id_cava
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          {SQL_EXISTS_PROGRAMADO}
        GROUP BY c.id, c.nombre, c.orden ORDER BY c.orden NULLS LAST
    """
    sql_destinos = f"""
        SELECT COALESCE(NULLIF(TRIM(de.nombre), ''), NULLIF(TRIM(s.nombre), ''), 'Sin zona') AS destino,
               COUNT(*) AS total
        FROM trazabilidad_proceso.parte_producto pp
        {SQL_PPCR_JOIN}
        JOIN trazabilidad_proceso.parte_producto_empresa ppe
            ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        JOIN trazabilidad_proceso.parte_producto_empresa_local ppel
            ON ppel.id_parte_producto_empresa = ppe.id
           AND ppel.fecha_programacion_despacho::date = %s::date
        LEFT JOIN organizaciones.sucursal s ON s.id = ppel.id_local
        LEFT JOIN trazabilidad_proceso.destino de ON de.id = s.id_destino
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
        GROUP BY 1 ORDER BY total DESC LIMIT 10
    """

    results = safe_query_many([
        ("totales", sql_totales, (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro), "dashboard.totales"),
        ("cavas",   sql_cavas,   (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro), "dashboard.cavas"),
        ("destinos",sql_destinos,(fecha_filtro, IDS_CANAL),                 "dashboard.destinos"),
    ])
    t  = results.get("totales", [{}])
    cv = results.get("cavas", [])
    ds = results.get("destinos", [])

    tot = t[0] if t else {}
    mc1 = int(tot.get("mc1") or 0)
    mc2 = int(tot.get("mc2") or 0)
    # Un animal completo = MC1 + MC2 → cada media vale 0.5 canales
    canales_completas = (mc1 + mc2) / 2

    payload = {
        "fecha":             fecha_filtro,
        "turno":             turno or "Todos",
        "mc1":               mc1,
        "mc2":               mc2,
        "total_partes":      int(tot.get("total_partes") or 0),
        "animales_distintos":int(tot.get("animales") or 0),
        "canales_completas": canales_completas,
        "cavas":             serializable(cv),
        "top_destinos":      serializable(ds),
    }
    cache_set(ck, payload)
    return payload


# ═══════════════════════════════════════════════════════
# DESPACHOS — agrupado por puesto/zona (como Gestor Vísceras)
# Ruta: 09404/Floridablanca/.../JxV/  → puesto 9404, zona Floridablanca
# ═══════════════════════════════════════════════════════
@app.get("/api/despachos")
def get_despachos(
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    refresh: Optional[str] = None,
):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    ck = ("despachos", fecha_filtro, turno, "prog_v1")
    hit = cache_get(ck)
    if hit is not None:
        return hit

    piezas = obtener_piezas_programadas(fecha_filtro, turno)
    data, totales = agrupar_despachos_por_puesto(piezas)
    payload = {"fecha": fecha_filtro, "turno": turno, "totales": totales, "data": data}
    cache_set(ck, payload)
    return payload


# ═══════════════════════════════════════════════════════
# DETALLE DESPACHO — por puesto/zona (clave) o propietario
# ═══════════════════════════════════════════════════════
@app.get("/api/despachos/detalle")
def get_despacho_detalle(
    propietario: Optional[str] = None,
    destino: Optional[str] = None,
    puesto: Optional[str] = None,
    zona: Optional[str] = None,
    clave: Optional[str] = None,
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    refresh: Optional[str] = None,
):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    data = obtener_piezas_programadas(fecha_filtro, turno)

    clave_q = (clave or "").strip()
    puesto_q = formatear_codigo_sucursal(puesto or "")
    zona_q = (zona or destino or "").strip().upper()
    prop_q = (propietario or "").strip()

    filtradas = []
    for r in data:
        if clave_q and r.get("clave") != clave_q:
            continue
        if puesto_q and formatear_codigo_sucursal(r.get("puesto")) != puesto_q:
            continue
        if zona_q and str(r.get("zona") or "").strip().upper() != zona_q:
            continue
        if prop_q and not clave_q and not puesto_q and str(r.get("propietario") or "").strip() != prop_q:
            continue
        item = dict(r)
        enriquecer_codigo(item)
        item["destino"] = item.get("zona") or item.get("ruta") or ""
        item["total_partes"] = 0.5
        filtradas.append(item)

    etiqueta = filtradas[0]["etiqueta"] if filtradas else (clave_q or prop_q or zona_q or "—")
    return {
        "fecha": fecha_filtro,
        "turno": turno,
        "propietario": prop_q or (filtradas[0].get("propietario") if filtradas else ""),
        "puesto": puesto_q or (filtradas[0].get("puesto") if filtradas else ""),
        "zona": zona_q or (filtradas[0].get("zona") if filtradas else ""),
        "destino": etiqueta,
        "total": len(filtradas),
        "data": filtradas,
    }


# ═══════════════════════════════════════════════════════
# OPL — canales por operador logístico
# Cada canal vale 0.5 (MC1 o MC2 = 0.5; par completo = 1.0)
# ═══════════════════════════════════════════════════════
@app.get("/api/opl")
def get_opl(fecha: Optional[str] = None, turno: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    sql = f"""
        SELECT
            e3.nombre                           AS propietario,
            COALESCE(NULLIF(TRIM(de.nombre), ''), NULLIF(TRIM(s.nombre), ''), 'Sin zona') AS destino,
            COUNT(*) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
            COUNT(*) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
            COUNT(*) AS total_partes,
            COUNT(*) * 0.5                  AS total_canales
        FROM trazabilidad_proceso.parte_producto pp
        {SQL_PPCR_JOIN}
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = pp.id_producto::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        JOIN trazabilidad_proceso.parte_producto_empresa ppe
            ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        JOIN trazabilidad_proceso.parte_producto_empresa_local ppel
            ON ppel.id_parte_producto_empresa = ppe.id
           AND ppel.fecha_programacion_despacho::date = %s::date
        LEFT JOIN organizaciones.sucursal s ON s.id = ppel.id_local
        LEFT JOIN trazabilidad_proceso.destino de ON de.id = s.id_destino
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
        GROUP BY e3.nombre, 2
        ORDER BY e3.nombre NULLS LAST, 2
    """
    rows = safe_query(sql, (ID_MC1, ID_MC2, fecha_filtro, IDS_CANAL), "opl")
    data = serializable(rows)
    # Agrupar por propietario
    opls = {}
    for r in data:
        prop = r.get("propietario") or "SIN PROPIETARIO"
        if prop not in opls:
            opls[prop] = {"propietario": prop, "mc1": 0, "mc2": 0, "total_partes": 0, "total_canales": 0.0, "destinos": []}
        opls[prop]["mc1"]          += int(r.get("mc1") or 0)
        opls[prop]["mc2"]          += int(r.get("mc2") or 0)
        opls[prop]["total_partes"] += int(r.get("total_partes") or 0)
        opls[prop]["total_canales"] = opls[prop]["mc1"] * 0.5 + opls[prop]["mc2"] * 0.5
        if r.get("destino"):
            opls[prop]["destinos"].append(r["destino"])
    lista = sorted(opls.values(), key=lambda x: x["total_canales"], reverse=True)
    return {"fecha": fecha_filtro, "turno": turno, "total_opls": len(lista), "data": lista}


# ═══════════════════════════════════════════════════════
# OPL DETALLE — lista completa de canales para un propietario
# Columnas: código con sufijo, cliente/propietario, cava, riel, fecha salida
# ═══════════════════════════════════════════════════════
@app.get("/api/opl/detalle")
def get_opl_detalle(propietario: str, fecha: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
    sql = f"""
        SELECT DISTINCT ON (pp.id_producto, pp.id)
            pp.id_producto                          AS codigo,
            tpp.id                                  AS id_tipo,
            tpp.nombre                              AS descripcion,
            pp.con_destino                          AS destino,
            pp.observaciones,
            e3.nombre                               AS propietario,
            c.nombre                                AS cava,
            r.nombre                                AS riel,
            ppcr.fecha_ingreso                      AS fecha_ingreso,
            ppcr.fecha_salida                       AS fecha_salida,
            EXTRACT(EPOCH FROM (NOW() - ppcr.fecha_ingreso))/3600 AS horas_en_cava
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.tipo_parte_producto tpp ON tpp.id = pp.id_tipo_parte_producto
        {SQL_PPCR_JOIN}
        LEFT JOIN trazabilidad_proceso.cava c ON c.id = ppcr.id_cava
        LEFT JOIN trazabilidad_proceso.riel r ON r.id = ppcr.id_riel
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = pp.id_producto::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND e3.nombre = %s
          {SQL_EXISTS_PROGRAMADO}
        ORDER BY pp.id_producto, pp.id, c.orden NULLS LAST, r.nombre
    """
    rows = safe_query(sql, (IDS_CANAL, propietario, fecha_filtro), "opl_detalle")
    data = serializable(rows)
    for r in data:
        enriquecer_codigo(r)
        r["horas_en_cava"] = round(float(r.get("horas_en_cava") or 0), 1)
    mc1 = sum(1 for r in data if r.get("id_tipo") == ID_MC1)
    mc2 = sum(1 for r in data if r.get("id_tipo") == ID_MC2)
    return {
        "fecha": fecha_filtro,
        "propietario": propietario,
        "mc1": mc1, "mc2": mc2,
        "total_partes": len(data),
        "total_canales": (mc1 + mc2) * 0.5,
        "data": data
    }


# ═══════════════════════════════════════════════════════
# SALIDAS — canales despachadas (pistoleadas) en el rango
# ═══════════════════════════════════════════════════════
@app.get("/api/salidas")
def get_salidas(fecha: Optional[str] = None, dias: int = 1, turno: Optional[str] = None):
    fecha_fin = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_fin, turno)
    turno_filtro = None  # turno se aplica en logística (despachos/planilla); cavas = stock en cava
    sql = """
        SELECT
            pp.id_producto          AS codigo,
            tpp.id                  AS id_tipo,
            tpp.nombre              AS descripcion,
            pp.con_destino          AS destino,
            pp.observaciones,
            e3.nombre               AS propietario,
            c.nombre                AS cava,
            r.nombre                AS riel,
            ppcr.fecha_ingreso      AS ingreso_cava,
            ppcr.fecha_salida       AS fecha_salida,
            EXTRACT(EPOCH FROM (ppcr.fecha_salida - ppcr.fecha_ingreso))/3600 AS horas_en_cava
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.tipo_parte_producto tpp ON tpp.id = pp.id_tipo_parte_producto
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.cava c ON c.id = ppcr.id_cava
        LEFT JOIN trazabilidad_proceso.riel r ON r.id = ppcr.id_riel
        LEFT JOIN trazabilidad_proceso.producto p ON p.id::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = p.id::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel ON ppel.id_parte_producto_empresa = ppe.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NOT NULL
          AND ppcr.fecha_salida >= (%s::date - (%s * INTERVAL '1 day'))
          AND ppcr.fecha_salida < (%s::date + INTERVAL '1 day')
          AND """ + sql_filtro_turno() + """
        ORDER BY ppcr.fecha_salida DESC, pp.id_producto
    """
    rows = safe_query(sql, (IDS_CANAL, fecha_fin, dias, fecha_fin, turno_filtro, turno_filtro), "salidas")
    data = serializable(rows)
    for r in data:
        enriquecer_codigo(r)
        r["horas_en_cava"] = round(float(r.get("horas_en_cava") or 0), 1)
    mc1 = sum(1 for r in data if r.get("id_tipo") == ID_MC1)
    mc2 = sum(1 for r in data if r.get("id_tipo") == ID_MC2)
    return {
        "fecha": fecha_fin, "dias_rango": dias, "turno": turno,
        "mc1": mc1, "mc2": mc2,
        "total_partes": len(data),
        "total_canales": (mc1 + mc2) * 0.5,
        "data": data,
    }


# ═══════════════════════════════════════════════════════
# PLANILLA OPL — progreso de despacho por propietario
# ═══════════════════════════════════════════════════════
@app.get("/api/planilla_opl")
def get_planilla_opl(
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    refresh: Optional[str] = None,
):
    """
    Progreso OPL del día programado:
    - Pendientes: en cava con fecha_programacion_despacho = fecha
    - Despachadas: salida del día con esa misma programación
    """
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    ck = ("planilla_opl", fecha_filtro, turno, "prog_v4_adi_split")
    hit = cache_get(ck)
    if hit is not None:
        return hit

    sql_estado = f"""
        WITH programadas AS (
            SELECT DISTINCT
                pp.id_producto::text AS id_producto,
                pp.id AS id_parte_producto,
                pp.id_tipo_parte_producto
            FROM trazabilidad_proceso.parte_producto pp
            WHERE pp.id_tipo_parte_producto IN %s
              {SQL_EXISTS_PROGRAMADO}
        ),
        ultimo_movimiento AS (
            SELECT DISTINCT ON (p.id_producto, p.id_parte_producto)
                p.id_producto,
                p.id_parte_producto,
                p.id_tipo_parte_producto,
                mov.fecha_salida
            FROM programadas p
            JOIN trazabilidad_proceso.parte_producto_cava_riel mov
              ON mov.id_parte_producto = p.id_parte_producto
             AND mov.id_producto::text = p.id_producto
            ORDER BY
                p.id_producto,
                p.id_parte_producto,
                mov.fecha_ingreso DESC NULLS LAST,
                mov.id DESC
        )
        SELECT
            e3.nombre                           AS propietario,
            COUNT(*) FILTER (WHERE u.id_tipo_parte_producto = {ID_MC1} AND u.fecha_salida IS NULL) AS mc1_pend,
            COUNT(*) FILTER (WHERE u.id_tipo_parte_producto = {ID_MC2} AND u.fecha_salida IS NULL) AS mc2_pend,
            COUNT(*) FILTER (WHERE u.fecha_salida IS NULL) AS total_pend,
            COUNT(*) FILTER (WHERE u.id_tipo_parte_producto = {ID_MC1} AND u.fecha_salida::date = %s::date) AS mc1_sal,
            COUNT(*) FILTER (WHERE u.id_tipo_parte_producto = {ID_MC2} AND u.fecha_salida::date = %s::date) AS mc2_sal,
            COUNT(*) FILTER (WHERE u.fecha_salida::date = %s::date) AS total_sal
        FROM ultimo_movimiento u
        LEFT JOIN trazabilidad_proceso.producto_empresa pe
          ON pe.id_producto::text = u.id_producto AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        WHERE u.fecha_salida IS NULL OR u.fecha_salida::date = %s::date
        GROUP BY e3.nombre
        ORDER BY total_pend DESC
    """

    rows_estado = serializable(safe_query(
        sql_estado,
        (
            IDS_CANAL,
            fecha_filtro,
            fecha_filtro,
            fecha_filtro,
            fecha_filtro,
            fecha_filtro,
        ),
        "planilla.estado",
    ))

    idx_cava = {
        (r.get("propietario") or "SIN PROPIETARIO"): r
        for r in rows_estado
    }
    idx_salidas = {
        (r.get("propietario") or "SIN PROPIETARIO"): r
        for r in rows_estado
    }

    lista = []
    for prop in sorted(set(idx_cava) | set(idx_salidas)):
        cava = idx_cava.get(prop, {})
        sal  = idx_salidas.get(prop, {})
        mc1_pend = int(cava.get("mc1_pend") or 0)
        mc2_pend = int(cava.get("mc2_pend") or 0)
        mc1_sal  = int(sal.get("mc1_sal") or 0)
        mc2_sal  = int(sal.get("mc2_sal") or 0)
        total_pend = mc1_pend + mc2_pend
        total_sal  = mc1_sal  + mc2_sal
        total_ini  = total_pend + total_sal
        # Despachado = pistoleo real (fecha_salida del día)
        pct = round((total_sal / total_ini) * 100) if total_ini else 0
        if total_pend > 0:
            pct = min(99, pct)
        elif total_ini > 0:
            pct = 100
        lista.append({
            "propietario": prop,
            "mc1_pendiente":  mc1_pend,
            "mc2_pendiente":  mc2_pend,
            "mc1_despachado": mc1_sal,
            "mc2_despachado": mc2_sal,
            "total_pendiente": total_pend,
            "total_despachado": total_sal,
            "canales_pendiente":  total_pend * 0.5,
            "canales_despachado": total_sal  * 0.5,
            "total_inicial":     total_ini,
            "progreso_pct":      pct,
            "opl":               resolver_opl_de_propietario(prop),
        })

    lista.sort(key=lambda x: x["total_pendiente"], reverse=True)
    lista = _aplicar_adicionales_a_planilla_opl(lista, fecha_filtro)

    by_opl = {}
    for r in lista:
        opl = r.get("opl") or apps_script_local.OPL_DEFAULT
        b = by_opl.setdefault(opl, {"opl": opl, "pend": 0, "sal": 0, "mc1": 0, "mc2": 0})
        b["pend"] += int(r["total_pendiente"] or 0)
        b["sal"] += int(r["total_despachado"] or 0)
        b["mc1"] += int(r["mc1_pendiente"] or 0) + int(r["mc1_despachado"] or 0)
        b["mc2"] += int(r["mc2_pendiente"] or 0) + int(r["mc2_despachado"] or 0)

    totales_vivos = {
        "mc1_pend":  sum(r["mc1_pendiente"]   for r in lista),
        "mc2_pend":  sum(r["mc2_pendiente"]   for r in lista),
        "mc1_sal":   sum(r["mc1_despachado"]  for r in lista),
        "mc2_sal":   sum(r["mc2_despachado"]  for r in lista),
        "pend_total":sum(r["total_pendiente"] for r in lista),
        "sal_total": sum(r["total_despachado"]for r in lista),
    }
    # Snapshot vivo = universo asignado del día (pendientes + pistoleadas).
    # El pistoleo no lo mueve: solo sube con más asignaciones y baja si
    # cancelan / una salida deja de contar.
    snapshot = {
        "medias": totales_vivos["pend_total"] + totales_vivos["sal_total"],
        "mc1": totales_vivos["mc1_pend"] + totales_vivos["mc1_sal"],
        "mc2": totales_vivos["mc2_pend"] + totales_vivos["mc2_sal"],
        "por_opl": {
            opl: {"medias": b["pend"] + b["sal"], "mc1": b["mc1"], "mc2": b["mc2"]}
            for opl, b in by_opl.items()
        },
    }
    congelado = apps_script_local.actualizar_asignado_congelado(
        fecha_filtro, turno or turno_de_fecha(fecha_filtro), snapshot
    )
    por_opl_cong = congelado.get("por_opl") or {}

    todos_opl = []
    for opl, b in by_opl.items():
        pend = b["pend"]
        cong = por_opl_cong.get(opl) or {}
        total = int(cong.get("medias") or (b["pend"] + b["sal"]))
        # Estilo Vísceras: despachados = asignado congelado − pendientes
        pend = min(total, pend)
        sal = max(0, total - pend)
        pct = round((sal / total) * 100) if total else 0
        if pend > 0:
            pct = min(99, pct)
        elif total > 0:
            pct = 100
        todos_opl.append({
            "opl": opl,
            "total": total * 0.5,
            "despachados": sal * 0.5,
            "pendientes": pend * 0.5,
            "progreso": pct,
            "total_medias": total,
            "pendientes_medias": pend,
            "despachados_medias": sal,
            "asignado_medias": total,
        })
    todos_opl.sort(key=lambda x: (-x["pendientes"], -x["total"], x["opl"]))
    progreso_activos = [x for x in todos_opl if x["pendientes"] > 0]

    asignado_medias = int(congelado.get("medias") or 0)
    pend_total = totales_vivos["pend_total"]
    pend_total = min(asignado_medias, pend_total) if asignado_medias else pend_total
    sal_total = max(0, asignado_medias - pend_total)
    totales = {
        "mc1_pend":  totales_vivos["mc1_pend"],
        "mc2_pend":  totales_vivos["mc2_pend"],
        "mc1_sal":   totales_vivos["mc1_sal"],
        "mc2_sal":   totales_vivos["mc2_sal"],
        "pend_total": pend_total,
        "sal_total": sal_total,
        "canales_pend": pend_total * 0.5,
        "canales_sal":  sal_total * 0.5,
        # Meta congelada del día (no baja con pistoleo)
        "asignado_medias": asignado_medias,
        "asignado_canales": asignado_medias * 0.5,
        "mc1_asignado": int(congelado.get("mc1") or 0),
        "mc2_asignado": int(congelado.get("mc2") or 0),
        "asignado_congelado": True,
    }
    t_ini = asignado_medias
    totales["progreso_global"] = round((sal_total / t_ini) * 100) if t_ini else 0
    if pend_total > 0:
        totales["progreso_global"] = min(99, totales["progreso_global"])
    elif t_ini > 0:
        totales["progreso_global"] = 100

    adi = contar_adicionales_dashboard(fecha_filtro)
    payload = {
        "fecha": fecha_filtro,
        "turno": turno or turno_de_fecha(fecha_filtro),
        "totales": totales,
        "data": lista,
        "todosOPL": todos_opl,
        "progreso": progreso_activos,
        "operacionFinalizada": bool(todos_opl) and not progreso_activos,
        "unidad": "canales",
        "success": True,
        # Desglose horario: progreso / pendientes cuentan AMBAS
        "totalCanalesNormales": adi.get("totalCanalesNormales") or 0,
        "totalMediasNormales": adi.get("totalMediasNormales") or 0,
        "totalCanalesAdicionales": adi.get("totalCanalesAdicionales") or 0,
        "totalMediasAdicionales": adi.get("totalMediasAdicionales") or 0,
        "totalCanalesSalidaDia": adi.get("totalCanalesSalida") or 0,
        "corteAdicional": adi.get("corteAdicional") or get_salida_adicional_corte_label(),
    }
    cache_set(ck, payload)
    return payload


# ═══════════════════════════════════════════════════════
# PLANILLA DE PUNTOS — lista por OPL para logística
# ═══════════════════════════════════════════════════════
def armar_planilla_estilo_visceras(items: List[dict], opl_sel: Optional[str], fecha: str, turno: Optional[str]) -> dict:
    """
    Misma estructura que Gestor Vísceras generarPlanillaPuntos:
    zonas[{nombre, total, puestos[{puesto, cantidad}]}] + lista plana puestos.
    Cantidad = medias × 0.5 (equivalente canal).
    """
    opl_sel = (opl_sel or "").strip()
    total_global = round(len(items) * 0.5, 2)
    zonas_map = {}
    puestos_flat = []
    total_opl = 0.0

    for r in items:
        opl_reg = r.get("opl") or apps_script_local.OPL_DEFAULT
        if opl_sel and opl_sel.upper() != "TODOS" and opl_reg != opl_sel:
            continue
        cantidad = 0.5
        total_opl += cantidad
        zona = (r.get("zona") or "SIN ZONA").strip() or "SIN ZONA"
        puesto = formatear_codigo_sucursal(r.get("puesto") or "") or "—"
        clave = r.get("clave") or f"{puesto}|{zona.upper()}"
        if zona not in zonas_map:
            zonas_map[zona] = {"total": 0.0, "puestos_map": {}}
        zonas_map[zona]["total"] += cantidad
        pm = zonas_map[zona]["puestos_map"]
        if clave not in pm:
            pm[clave] = {"puesto": puesto, "cantidad": 0.0}
        pm[clave]["cantidad"] += cantidad
        puestos_flat.append({
            "puesto": r.get("ruta") or construir_ruta(puesto, zona, r.get("direccion") or "", turno or ""),
            "etiqueta": r.get("etiqueta") or (f"{puesto} · {zona}" if puesto and zona else puesto or zona),
            "sucursal": puesto,
            "zona": zona,
            "cantidad": cantidad,
            "opl": opl_reg,
            "codigo": r.get("codigo"),
            "propietario": r.get("propietario"),
        })

    # Consolidar flat por puesto+zona
    flat_agg = {}
    for p in puestos_flat:
        k = f"{p['sucursal']}|{str(p['zona']).upper()}"
        if k not in flat_agg:
            flat_agg[k] = {
                "puesto": p["puesto"],
                "etiqueta": p["etiqueta"],
                "sucursal": p["sucursal"],
                "zona": p["zona"],
                "cantidad": 0.0,
                "opl": p["opl"],
            }
        flat_agg[k]["cantidad"] += p["cantidad"]
    puestos_lista = sorted(
        ({**v, "cantidad": round(v["cantidad"], 2)} for v in flat_agg.values()),
        key=lambda x: (str(x.get("zona") or ""), str(x.get("sucursal") or "")),
    )

    zonas_array = []
    for zona, bucket in zonas_map.items():
        puestos_arr = sorted(
            [
                {"puesto": v["puesto"], "cantidad": round(v["cantidad"], 2)}
                for v in bucket["puestos_map"].values()
            ],
            key=lambda x: str(x["puesto"]),
        )
        zonas_array.append({
            "nombre": zona,
            "total": round(bucket["total"], 2),
            "puestos": puestos_arr,
        })
    zonas_array.sort(key=lambda z: (-z["total"], z["nombre"]))

    pct = f"{(total_opl / total_global * 100):.1f}" if total_global > 0 else "0.0"
    return {
        "success": True,
        "opl": opl_sel or "TODOS",
        "zonas": zonas_array,
        "puestos": puestos_lista,
        "totalOPL": round(total_opl, 2),
        "totalGlobal": total_global,
        "porcentaje": pct,
        "turno": turno or "Todos",
        "fecha": fecha,
    }


@app.get("/api/planilla_puntos")
def get_planilla_puntos(
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    opl: Optional[str] = None,
    refresh: Optional[str] = None,
):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    opl_key = (opl or "").strip() or "TODOS"
    ck = ("planilla_puntos", fecha_filtro, turno, opl_key, "v1")
    hit = cache_get(ck)
    if hit is not None:
        return hit
    items = obtener_piezas_programadas(fecha_filtro, turno)
    resumen, total_general = resumen_planilla_puntos(items)
    cfg = apps_script_local.getOplConfig()
    opls_cfg = cfg.get("opls") or []
    opls_data = [r["opl"] for r in resumen]
    opls = sorted(set(opls_cfg) | set(opls_data))
    pack = armar_planilla_estilo_visceras(items, opl, fecha_filtro, turno)
    pack["opls"] = opls
    pack["resumen"] = resumen
    pack["totalGeneral"] = total_general
    pack["success"] = True
    # Detalle pieza a pieza (Excel / apoyo)
    opl_sel = (opl or "").strip()
    detalle = items if not opl_sel or opl_sel.upper() == "TODOS" else [
        r for r in items if (r.get("opl") or "") == opl_sel
    ]
    pack["data"] = detalle
    pack["total"] = len(detalle)
    pack["total_partes"] = round(len(detalle) * 0.5, 2)
    cache_set(ck, pack)
    return pack


@app.get("/api/planilla_puntos/excel")
def excel_planilla_puntos(
    opl: str,
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
):
    opl = (opl or "").strip()
    if not opl:
        raise HTTPException(status_code=400, detail="Indica el OPL")
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    items = [
        r for r in obtener_piezas_programadas(fecha_filtro, turno)
        if (r.get("opl") or "") == opl
    ]
    buf = construir_excel_opl(opl, fecha_filtro, turno, items)
    fname = f"OPL_{opl.replace(' ', '_')}_{fecha_filtro}.xlsx"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"
    }
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


class ParticularesIn(BaseModel):
    opls: List[str] = []


@app.get("/api/planilla_puntos/particulares")
def get_particulares():
    return apps_script_local.getOplsParticulares()


@app.post("/api/planilla_puntos/particulares")
def set_particulares(payload: ParticularesIn):
    return apps_script_local.setOplsParticulares(payload.opls or [])


@app.get("/api/planilla_puntos/excel_particulares")
def excel_planilla_particulares(
    fecha: Optional[str] = None,
    turno: Optional[str] = None,
    refresh: Optional[str] = None,
):
    """
    Excel multi-hoja de OPLs particulares.
    Solo pendientes (sin fecha_salida): a medida que pistolean, se van quitando.
    """
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)

    cfg = apps_script_local.getOplsParticulares()
    seleccion = [str(x).strip() for x in (cfg.get("opls") or []) if str(x).strip()]
    if not seleccion:
        raise HTTPException(
            status_code=400,
            detail="No hay OPLs particulares configurados. Selecciónalos primero.",
        )

    seleccion_up = {x.upper() for x in seleccion}
    piezas = obtener_piezas_programadas(fecha_filtro, turno)
    por_opl = {opl: [] for opl in seleccion}
    mapa_nombre = {opl.upper(): opl for opl in seleccion}
    for r in piezas:
        opl = (r.get("opl") or "").strip()
        if opl.upper() in seleccion_up:
            por_opl[mapa_nombre[opl.upper()]].append(r)

    # Mantener orden de selección; incluir hojas aunque queden en 0 (ya despachados)
    ordenado = {opl: por_opl.get(opl, []) for opl in seleccion}
    buf = construir_excel_particulares(fecha_filtro, turno, ordenado)
    fname = f"Particulares_pendientes_{fecha_filtro}.xlsx"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"
    }
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ═══════════════════════════════════════════════════════
# SALIDAS FÍSICAS — normales vs adicionales por hora
# ═══════════════════════════════════════════════════════
@app.get("/api/salidas_fisicas")
def api_salidas_fisicas(fecha: Optional[str] = None, refresh: Optional[str] = None):
    """
    Pistoleo del día (fecha_salida). Clasifica normal / adicional por hora
    (≥ CANALES_SALIDA_ADICIONAL_HORA:MINUTO, defecto 15:20).
    No escribe tipo en SIRT ni cambia pendientes.
    """
    fecha_filtro = fecha or date.today().isoformat()
    if es_refresh(refresh):
        cache_invalidate_fecha(fecha_filtro)
    ck = ("salidas_fisicas", fecha_filtro, "v2_prog")
    hit = cache_get(ck)
    if hit is not None:
        return hit
    payload = consultar_salidas_fisicas(fecha_filtro)
    cache_set(ck, payload)
    return payload


# ═══════════════════════════════════════════════════════
# ADICIONALES — Excel manual (flujo aparte; no es el corte horario)
# ═══════════════════════════════════════════════════════
@app.get("/api/adicionales")
def api_get_adicionales(fecha: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
    return apps_script_local.getAdicionales(fecha_filtro)


@app.post("/api/adicionales/procesar")
async def api_procesar_adicionales(
    file: UploadFile = File(...),
    fecha: Optional[str] = Form(None),
):
    fecha_filtro = fecha or date.today().isoformat()
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    nombre = file.filename or "adicionales.xlsx"
    try:
        return procesar_excel_adicionales(raw, nombre, fecha_filtro)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/adicionales")
def api_limpiar_adicionales(fecha: Optional[str] = None):
    return apps_script_local.limpiarAdicionales(fecha)


@app.post("/api/cache/invalidate")
def api_cache_invalidate(fecha: Optional[str] = None):
    """Invalida caché de la fecha (o toda). Usado por el botón Refrescar."""
    n = cache_invalidate_fecha(fecha)
    return {"ok": True, "fecha": fecha, "cleared": n, "ttl_seg": CACHE_TTL_SEG}


# ═══════════════════════════════════════════════════════
# SERVIDOR
# ═══════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")


class AppsScriptRequest(BaseModel):
    args: list = []


class UsageEventIn(BaseModel):
    usuario: str = "anonimo"
    action: str = "event"
    module: str = ""
    detail: str = ""
    sessionId: str = ""
    page: str = ""
    meta: dict = {}


class UsageLoginIn(BaseModel):
    password: str = ""


@app.post("/api/usability/event")
def usability_event(payload: UsageEventIn, request: Request):
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    return usability.record_event(payload.dict(), ip=ip, user_agent=ua)


@app.post("/api/usability/login")
def usability_login(payload: UsageLoginIn):
    token = usability.login_admin(payload.password)
    if not token:
        return {"success": False, "message": "Contraseña incorrecta"}
    return {"success": True, "token": token}


@app.get("/api/usability/stats")
def usability_stats(
    days: int = 30,
    x_usability_admin: str = Header(default="", alias="X-Usability-Admin"),
    authorization: str = Header(default=""),
):
    token = (x_usability_admin or "").strip()
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not usability.verify_admin(token):
        raise HTTPException(status_code=401, detail="No autorizado")
    return usability.get_stats(days)


@app.get("/usabilidad.html")
def usabilidad_page():
    return FileResponse("static/usabilidad.html")


@app.get("/api/opl/propietarios")
def get_opl_propietarios(fecha: Optional[str] = None, turno: Optional[str] = None):
    """Propietarios del día con canales y OPL actual (para Configuración)."""
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    planilla = get_planilla_opl(fecha=fecha_filtro, turno=turno or "Todos")
    cfg = apps_script_local.getOplConfig()
    opls = cfg.get("opls") or [apps_script_local.OPL_DEFAULT]
    por_prop = {}
    for r in planilla.get("data") or []:
        prop = (r.get("propietario") or "SIN PROPIETARIO").strip()
        b = por_prop.setdefault(prop, {
            "propietario": prop,
            "canales": 0.0,
            "medias": 0,
            "opl": r.get("opl") or apps_script_local.OPL_DEFAULT,
        })
        b["medias"] += int(r.get("total_inicial") or 0)
        b["canales"] = b["medias"] * 0.5
        b["opl"] = r.get("opl") or b["opl"]
    resultado = sorted(por_prop.values(), key=lambda x: (-x["canales"], x["propietario"]))
    return {
        "success": True,
        "fecha": fecha_filtro,
        "turno": planilla.get("turno"),
        "opls": opls,
        "resultado": resultado,
    }


@app.post("/api/opl/asignar")
def post_opl_asignar(propietario: str, opl: str):
    """Asigna propietario → OPL e invalida caché de progreso."""
    res = apps_script_local.upsertOpl(propietario, opl)
    cache_invalidate_fecha(None)
    return res


@app.post("/api/apps-script/{function_name}")
def run_apps_script_function(function_name: str, payload: AppsScriptRequest):
    try:
        out = apps_script_local.dispatch(function_name, payload.args)
        if function_name in ("upsertOpl", "eliminarOpl", "guardarProgresoOpl"):
            cache_invalidate_fecha(None)
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/favicon.ico")
def favicon():
    return FileResponse(
        "static/favicon.png",
        media_type="image/png",
        headers={"Cache-Control": "no-cache, max-age=0, must-revalidate"},
    )


@app.get("/")
def root():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
