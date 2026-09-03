"""
Despacho de Canales — Colbeef
Backend FastAPI con conexión directa a PostgreSQL (solo lectura)
v1.0 — Medias canales: Media Canal 1 (sufijo -1001) y Media Canal 2 (sufijo -1002)
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg2
import psycopg2.extras
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
import apps_script_local

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
    """Si turno viene vacío, usa el de la fecha. 'Todos' o None explícito = sin filtro."""
    if turno is None:
        return turno_de_fecha(fecha_str)
    t = str(turno).strip()
    if t == "" or t.lower() == "todos":
        return None
    return t

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
    "options": f"-c statement_timeout={os.getenv('POSTGRES_STATEMENT_TIMEOUT_MS', '30000')}",
}


def get_conn():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"No se pudo conectar a la BD: {str(e)}")


def query(sql: str, params=None):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


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
def get_cavas(fecha: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
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
        ORDER BY
            c.orden NULLS LAST,
            r.nombre,
            pp.id_producto
    """
    rows = safe_query(sql, (IDS_CANAL, fecha_filtro), "cavas")
    data = serializable(rows)
    for r in data:
        enriquecer_codigo(r)
    return {"fecha": fecha_filtro, "total": len(data), "data": data}


# ═══════════════════════════════════════════════════════
# DASHBOARD — resumen ejecutivo de canales en cava
# ═══════════════════════════════════════════════════════
@app.get("/api/dashboard")
def get_dashboard(fecha: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()

    sql_totales = """
        SELECT
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
            COUNT(pp.id) AS total_partes,
            COUNT(DISTINCT SPLIT_PART(pp.id_producto, '-', 1)||'-'||SPLIT_PART(pp.id_producto, '-', 2)) AS animales
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
    """
    sql_cavas = """
        SELECT c.nombre AS cava,
               COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
               COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
               COUNT(pp.id) AS total
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        JOIN trazabilidad_proceso.cava c ON c.id = ppcr.id_cava
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
        GROUP BY c.id, c.nombre, c.orden ORDER BY c.orden NULLS LAST
    """
    sql_destinos = """
        SELECT pp.con_destino AS destino, COUNT(pp.id) AS total
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
          AND pp.con_destino IS NOT NULL
        GROUP BY pp.con_destino ORDER BY total DESC LIMIT 10
    """

    results = safe_query_many([
        ("totales", sql_totales, (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro), "dashboard.totales"),
        ("cavas",   sql_cavas,   (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro), "dashboard.cavas"),
        ("destinos",sql_destinos,(IDS_CANAL, fecha_filtro),                 "dashboard.destinos"),
    ])
    t  = results.get("totales", [{}])
    cv = results.get("cavas", [])
    ds = results.get("destinos", [])

    tot = t[0] if t else {}
    mc1 = int(tot.get("mc1") or 0)
    mc2 = int(tot.get("mc2") or 0)
    # Un animal completo = MC1 + MC2 → cada media vale 0.5 canales
    canales_completas = (mc1 + mc2) / 2

    return {
        "fecha":             fecha_filtro,
        "mc1":               mc1,
        "mc2":               mc2,
        "total_partes":      int(tot.get("total_partes") or 0),
        "animales_distintos":int(tot.get("animales") or 0),
        "canales_completas": canales_completas,
        "cavas":             serializable(cv),
        "top_destinos":      serializable(ds),
    }


# ═══════════════════════════════════════════════════════
# DESPACHOS — canales agrupadas por destino/turno
# Columnas: código con sufijo, propietario, MC1, MC2, Total
# ═══════════════════════════════════════════════════════
@app.get("/api/despachos")
def get_despachos(fecha: Optional[str] = None, turno: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    turno_filtro = f"%{turno}%" if turno else None

    sql = """
        SELECT
            pp.con_destino AS destino,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
            COUNT(pp.id) AS total_partes,
            (COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s)
             + COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s)) * 0.5 AS total_canales,
            COUNT(DISTINCT
                SPLIT_PART(pp.id_producto,'-',1)||'-'||SPLIT_PART(pp.id_producto,'-',2)
            ) AS animales
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr
            ON ppcr.id_parte_producto = pp.id
        WHERE
            pp.id_tipo_parte_producto IN %s
            AND ppcr.fecha_salida IS NULL
            AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
            AND pp.con_destino IS NOT NULL
            AND (%s::text IS NULL OR pp.con_destino ILIKE %s::text)
        GROUP BY pp.con_destino
        HAVING COUNT(pp.id) > 0
        ORDER BY pp.con_destino
    """
    rows = safe_query(sql, (ID_MC1, ID_MC2, ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro, turno_filtro, turno_filtro), "despachos")
    data = serializable(rows)
    totales = {
        "mc1":            sum(r.get("mc1", 0) or 0 for r in data),
        "mc2":            sum(r.get("mc2", 0) or 0 for r in data),
        "total_partes":   sum(r.get("total_partes", 0) or 0 for r in data),
        "total_canales":  sum(float(r.get("total_canales", 0) or 0) for r in data),
        "puestos":        len(data),
    }
    return {"fecha": fecha_filtro, "turno": turno, "totales": totales, "data": data}


# ═══════════════════════════════════════════════════════
# DETALLE DESPACHO — lista individual de canales para un destino
# Columnas de la imagen: Código, Cliente, Cava, Riel
# ═══════════════════════════════════════════════════════
@app.get("/api/despachos/detalle")
def get_despacho_detalle(destino: str, fecha: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
    sql = """
        SELECT
            pp.id_producto                          AS codigo,
            tpp.id                                  AS id_tipo,
            tpp.nombre                              AS descripcion,
            tpp.abreviatura                         AS abrev,
            pp.con_destino                          AS destino,
            pp.observaciones,
            e3.nombre                               AS propietario,
            c.nombre                                AS cava,
            r.nombre                                AS riel,
            ppcr.fecha_ingreso                      AS fecha_ingreso,
            ppcr.fecha_salida                       AS fecha_salida
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
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
          AND pp.con_destino = %s
        ORDER BY c.orden NULLS LAST, r.nombre, pp.id_producto
    """
    rows = safe_query(sql, (IDS_CANAL, fecha_filtro, destino), "despacho_detalle")
    data = serializable(rows)
    for r in data:
        enriquecer_codigo(r)
    return {"fecha": fecha_filtro, "destino": destino, "total": len(data), "data": data}


# ═══════════════════════════════════════════════════════
# OPL — canales por operador logístico
# Cada canal vale 0.5 (MC1 o MC2 = 0.5; par completo = 1.0)
# ═══════════════════════════════════════════════════════
@app.get("/api/opl")
def get_opl(fecha: Optional[str] = None, turno: Optional[str] = None):
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    turno_filtro = f"%{turno}%" if turno else None
    sql = """
        SELECT
            e3.nombre                           AS propietario,
            pp.con_destino                      AS destino,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2,
            COUNT(pp.id) AS total_partes,
            COUNT(pp.id) * 0.5                  AS total_canales
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.tipo_parte_producto tpp ON tpp.id = pp.id_tipo_parte_producto
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.cava c ON c.id = ppcr.id_cava
        LEFT JOIN trazabilidad_proceso.producto p ON p.id::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = p.id::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel ON ppel.id_parte_producto_empresa = ppe.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
          AND (%s::text IS NULL OR pp.con_destino ILIKE %s::text)
        GROUP BY e3.nombre, pp.con_destino
        ORDER BY e3.nombre NULLS LAST, pp.con_destino
    """
    rows = safe_query(sql, (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro, turno_filtro, turno_filtro), "opl")
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
    sql = """
        SELECT
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
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.cava c ON c.id = ppcr.id_cava
        LEFT JOIN trazabilidad_proceso.riel r ON r.id = ppcr.id_riel
        LEFT JOIN trazabilidad_proceso.producto p ON p.id::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = p.id::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel ON ppel.id_parte_producto_empresa = ppe.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
          AND e3.nombre = %s
        ORDER BY c.orden NULLS LAST, r.nombre, pp.id_producto
    """
    rows = safe_query(sql, (IDS_CANAL, fecha_filtro, propietario), "opl_detalle")
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
    turno_filtro = f"%{turno}%" if turno else None
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
          AND (%s::text IS NULL OR pp.con_destino ILIKE %s::text)
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
def get_planilla_opl(fecha: Optional[str] = None, turno: Optional[str] = None):
    """
    Progreso OPL del turno de la fecha:
    - Pendientes: canales aún en cava cuyo destino trae el turno (DxL, JxV, etc.)
    - Despachadas: canales con fecha_salida del día Y destino del mismo turno
    Así solo entra lo pistoleado de ese turno, no salidas de otros.
    """
    fecha_filtro = fecha or date.today().isoformat()
    turno = resolver_turno(fecha_filtro, turno)
    turno_filtro = f"%{turno}%" if turno else None

    sql_en_cava = """
        SELECT
            e3.nombre                           AS propietario,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1_pend,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2_pend,
            COUNT(pp.id)                        AS total_pend
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.producto p ON p.id::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = p.id::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel ON ppel.id_parte_producto_empresa = ppe.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NULL
          AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
          AND (%s::text IS NULL OR pp.con_destino ILIKE %s::text)
        GROUP BY e3.nombre ORDER BY total_pend DESC
    """
    sql_salidas = """
        SELECT
            e3.nombre                           AS propietario,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc1_sal,
            COUNT(pp.id) FILTER (WHERE pp.id_tipo_parte_producto = %s) AS mc2_sal,
            COUNT(pp.id)                        AS total_sal
        FROM trazabilidad_proceso.parte_producto pp
        JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.producto p ON p.id::text = pp.id_producto::text
        LEFT JOIN trazabilidad_proceso.producto_empresa pe ON pe.id_producto::text = p.id::text AND pe.activo = true
        LEFT JOIN organizaciones.empresa e3 ON e3.id = pe.id_empresa
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa ppe ON ppe.id_producto::text = pp.id_producto::text AND ppe.id_parte_producto = pp.id
        LEFT JOIN trazabilidad_proceso.parte_producto_empresa_local ppel ON ppel.id_parte_producto_empresa = ppe.id
        WHERE pp.id_tipo_parte_producto IN %s
          AND ppcr.fecha_salida IS NOT NULL
          AND DATE(ppcr.fecha_salida) = %s::date
          AND (%s::text IS NULL OR pp.con_destino ILIKE %s::text)
        GROUP BY e3.nombre
    """

    results = safe_query_many([
        ("en_cava", sql_en_cava, (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro, turno_filtro, turno_filtro), "planilla.cava"),
        ("salidas",  sql_salidas, (ID_MC1, ID_MC2, IDS_CANAL, fecha_filtro, turno_filtro, turno_filtro), "planilla.salidas"),
    ])

    idx_cava = {
        (r.get("propietario") or "SIN PROPIETARIO"): r
        for r in serializable(results.get("en_cava", []))
    }
    idx_salidas = {
        (r.get("propietario") or "SIN PROPIETARIO"): r
        for r in serializable(results.get("salidas", []))
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
        pct = round((total_sal / total_ini) * 100) if total_ini else 0
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
        })

    lista.sort(key=lambda x: x["total_pendiente"], reverse=True)

    totales = {
        "mc1_pend":  sum(r["mc1_pendiente"]   for r in lista),
        "mc2_pend":  sum(r["mc2_pendiente"]   for r in lista),
        "mc1_sal":   sum(r["mc1_despachado"]  for r in lista),
        "mc2_sal":   sum(r["mc2_despachado"]  for r in lista),
        "pend_total":sum(r["total_pendiente"] for r in lista),
        "sal_total": sum(r["total_despachado"]for r in lista),
        "canales_pend": sum(r["canales_pendiente"]  for r in lista),
        "canales_sal":  sum(r["canales_despachado"] for r in lista),
    }
    t_ini = totales["pend_total"] + totales["sal_total"]
    totales["progreso_global"] = round((totales["sal_total"] / t_ini) * 100) if t_ini else 0

    return {
        "fecha": fecha_filtro,
        "turno": turno or turno_de_fecha(fecha_filtro),
        "totales": totales,
        "data": lista,
    }


# ═══════════════════════════════════════════════════════
# SERVIDOR
# ═══════════════════════════════════════════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")


class AppsScriptRequest(BaseModel):
    args: list = []


@app.post("/api/apps-script/{function_name}")
def run_apps_script_function(function_name: str, payload: AppsScriptRequest):
    try:
        return apps_script_local.dispatch(function_name, payload.args)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
