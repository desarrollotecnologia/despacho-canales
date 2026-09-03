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
    options="-c statement_timeout=15000",
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== max id ===")
cur.execute("SELECT MAX(id) AS m FROM trazabilidad_proceso.parte_producto")
m = cur.fetchone()["m"]
print(m)
lo = m - 200000

print("\n=== recent con_destino with slash + turno-like ===")
cur.execute(
    """
    SELECT id_tipo_parte_producto AS tipo, con_destino, LEFT(COALESCE(observaciones,''),40) AS obs
    FROM trazabilidad_proceso.parte_producto
    WHERE id > %s
      AND con_destino LIKE '%%/%%'
      AND (
        con_destino ILIKE '%%JxV%%' OR con_destino ILIKE '%%LxM%%'
        OR con_destino ILIKE '%%MxJ%%' OR con_destino ILIKE '%%VxS%%'
      )
    ORDER BY id DESC
    LIMIT 15
    """,
    (lo,),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== recent tipos 4/5 con_destino values ===")
cur.execute(
    """
    SELECT con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto
    WHERE id > %s AND id_tipo_parte_producto IN (4,5)
    GROUP BY con_destino
    ORDER BY n DESC
    LIMIT 20
    """,
    (lo,),
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== recent tipos 13/14 sample paths ===")
cur.execute(
    """
    SELECT con_destino, COUNT(*) n
    FROM trazabilidad_proceso.parte_producto
    WHERE id > %s AND id_tipo_parte_producto IN (13,14)
      AND con_destino LIKE '%%/%%'
    GROUP BY con_destino
    ORDER BY n DESC
    LIMIT 10
    """,
    (lo,),
)
for r in cur.fetchall():
    print(dict(r))

conn.close()
print("OK")
