from pathlib import Path
from dotenv import load_dotenv
import os, psycopg2, psycopg2.extras

load_dotenv(Path(r"C:\Users\CAMPUSLANDS\G-Visceras\.env"))
conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "10.64.1.47"),
    dbname=os.getenv("POSTGRES_DB", "sirt"),
    user=os.getenv("POSTGRES_USER", "acceso"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
    connect_timeout=10,
    options="-c statement_timeout=20000",
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== cualquier con_destino Floridablanca /09404 (limit) ===")
cur.execute(
    """
    SELECT pp.id_tipo_parte_producto AS tipo, pp.con_destino,
           LEFT(COALESCE(pp.observaciones,''),60) AS obs
    FROM trazabilidad_proceso.parte_producto pp
    WHERE pp.con_destino ILIKE '%%Floridablanca%%'
       OR pp.con_destino ILIKE '%%09404%%'
       OR pp.observaciones ILIKE '%%09404/Floridablanca%%'
    ORDER BY pp.id DESC
    LIMIT 12
    """
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== sample visceras con_destino con slash (limit) ===")
cur.execute(
    """
    SELECT pp.con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    WHERE pp.id_tipo_parte_producto IN (13,14,10,11)
      AND pp.con_destino LIKE '%%/%%JxV%%'
    GROUP BY pp.con_destino
    ORDER BY n DESC
    LIMIT 8
    """
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== canales: valores con_destino distintos recientes (sin join cava) ===")
cur.execute(
    """
    SELECT pp.con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto pp
    WHERE pp.id_tipo_parte_producto IN (4,5)
      AND pp.id > (SELECT MAX(id)-50000 FROM trazabilidad_proceso.parte_producto)
    GROUP BY pp.con_destino
    ORDER BY n DESC
    LIMIT 25
    """
)
for r in cur.fetchall():
    print(dict(r))

conn.close()
print("OK")
