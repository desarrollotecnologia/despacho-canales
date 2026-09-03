from pathlib import Path
from dotenv import load_dotenv
import os, psycopg2

load_dotenv(Path(r"C:\Users\CAMPUSLANDS\G-Visceras\.env"))
conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST"),
    dbname=os.getenv("POSTGRES_DB"),
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
    connect_timeout=10,
    options="-c statement_timeout=8000",
)
cur = conn.cursor()
cur.execute(
    """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_schema='trazabilidad_proceso' AND table_name='parte_producto'
    ORDER BY ordinal_position
    """
)
for r in cur.fetchall():
    print(r[0], r[1])
conn.close()
