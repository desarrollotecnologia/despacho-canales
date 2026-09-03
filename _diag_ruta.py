"""Diagnóstico: dónde vive la ruta puesto/zona/turno (solo lectura)."""
from pathlib import Path
from dotenv import load_dotenv
import os
import psycopg2
import psycopg2.extras

load_dotenv(Path(r"C:\Users\CAMPUSLANDS\G-Visceras\.env"))
conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "10.64.1.47"),
    dbname=os.getenv("POSTGRES_DB", "sirt"),
    user=os.getenv("POSTGRES_USER", "acceso"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
    connect_timeout=10,
    options="-c statement_timeout=25000",
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
fecha = "2026-09-03"

print("=== VISERAS con_destino con /JxV/ (muestra) ===")
cur.execute(
    """
    SELECT pp.con_destino, LEFT(COALESCE(pp.observaciones,''),80) AS obs, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
    WHERE pp.id_tipo_parte_producto IN (13,14,10,11)
      AND ppcr.fecha_salida IS NULL
      AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
      AND pp.con_destino ILIKE '%%JxV%%'
    GROUP BY pp.con_destino, LEFT(COALESCE(pp.observaciones,''),80)
    ORDER BY n DESC
    LIMIT 8
    """,
    (fecha,),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== CANALES con_destino que contienen '/' ===")
cur.execute(
    """
    SELECT pp.con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
    WHERE pp.id_tipo_parte_producto IN (4,5)
      AND ppcr.fecha_salida IS NULL
      AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
      AND pp.con_destino IS NOT NULL
      AND pp.con_destino LIKE '%%/%%'
    GROUP BY pp.con_destino
    ORDER BY n DESC
    LIMIT 15
    """,
    (fecha,),
)
rows = cur.fetchall()
print("filas", len(rows))
for r in rows:
    print(dict(r))

print("\n=== CANALES observaciones con '/' o turno ===")
cur.execute(
    """
    SELECT LEFT(pp.observaciones,120) AS obs, pp.con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
    WHERE pp.id_tipo_parte_producto IN (4,5)
      AND ppcr.fecha_salida IS NULL
      AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
      AND (
        pp.observaciones ILIKE '%%/%%'
        OR pp.observaciones ILIKE '%%JxV%%'
        OR pp.observaciones ILIKE '%%Floridablanca%%'
      )
    GROUP BY LEFT(pp.observaciones,120), pp.con_destino
    ORDER BY n DESC
    LIMIT 15
    """,
    (fecha,),
)
rows = cur.fetchall()
print("filas", len(rows))
for r in rows:
    print(dict(r))

print("\n=== CANALES top con_destino ===")
cur.execute(
    """
    SELECT pp.con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
    WHERE pp.id_tipo_parte_producto IN (4,5)
      AND ppcr.fecha_salida IS NULL
      AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
      AND pp.con_destino IS NOT NULL
    GROUP BY pp.con_destino
    ORDER BY n DESC
    LIMIT 20
    """,
    (fecha,),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== CANALES: ¿hay ruta en identificacion? ===")
cur.execute(
    """
    SELECT LEFT(pp.identificacion,100) AS ident, pp.con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    JOIN trazabilidad_proceso.parte_producto_cava_riel ppcr ON ppcr.id_parte_producto = pp.id
    WHERE pp.id_tipo_parte_producto IN (4,5)
      AND ppcr.fecha_salida IS NULL
      AND ppcr.fecha_ingreso < (%s::date + INTERVAL '1 day')
      AND pp.identificacion IS NOT NULL
      AND (pp.identificacion LIKE '%%/%%' OR pp.identificacion ILIKE '%%JxV%%')
    GROUP BY LEFT(pp.identificacion,100), pp.con_destino
    LIMIT 10
    """,
    (fecha,),
)
rows = cur.fetchall()
print("filas", len(rows))
for r in rows:
    print(dict(r))

conn.close()
print("\nOK")
