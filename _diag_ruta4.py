from pathlib import Path
from dotenv import load_dotenv
import os, psycopg2, psycopg2.extras

load_dotenv(Path(r"C:\Users\CAMPUSLANDS\G-Visceras\.env"))
conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST"),
    dbname=os.getenv("POSTGRES_DB"),
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
    connect_timeout=10,
    options="-c statement_timeout=12000",
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== latest canales by fecha_registro ===")
cur.execute(
    """
    SELECT id, id_tipo_parte_producto AS tipo, con_destino,
           LEFT(COALESCE(observaciones,''),100) AS obs,
           fecha_registro
    FROM trazabilidad_proceso.parte_producto
    WHERE id_tipo_parte_producto IN (4,5)
    ORDER BY fecha_registro DESC NULLS LAST
    LIMIT 20
    """
)
for r in cur.fetchall():
    print(dict(r))

print("\n=== latest ANY with slash in con_destino ===")
cur.execute(
    """
    SELECT id, id_tipo_parte_producto AS tipo, con_destino, fecha_registro
    FROM trazabilidad_proceso.parte_producto
    WHERE con_destino LIKE '%/%'
    ORDER BY fecha_registro DESC NULLS LAST
    LIMIT 15
    """
)
for r in cur.fetchall():
    print(dict(r))

conn.close()
print("OK")
