"""
Migra local_data/canales_state.json a la BD propia del gestor (SQLite).

Uso (con el venv del proyecto):
    venv\\Scripts\\python.exe migrate_json_to_db.py

Idempotente: si la BD ya tiene datos, no vuelve a importar.
Tras un import exitoso el JSON se renombra a canales_state.json.bak.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import apps_script_local  # noqa: E402  (ensure_ready al importar)
import db_gestor  # noqa: E402


def main() -> int:
    info = db_gestor.ensure_ready(opl_defaults=apps_script_local.OPL_EXCEPCIONES_DEFAULT)
    st = db_gestor.stats()
    print("BD:", st["db"])
    print("Migración:", json.dumps(info, ensure_ascii=False, indent=2))
    print("Conteos:", json.dumps(st["counts"], ensure_ascii=False, indent=2))
    print("json_migrated_at:", st["json_migrated_at"])

    bak = info.get("backup")
    json_path = Path(info.get("json") or (db_gestor.DATA_DIR / "canales_state.json"))
    if info.get("imported"):
        print("JSON importado y respaldado en:", bak)
    elif json_path.exists():
        print("JSON aún presente (no se importó):", json_path, "motivo:", info.get("reason"))
    else:
        print("JSON ya no está en", json_path, "(ok si ya se migró).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
