"""
apps_script_local.py — Despacho de Canales (Colbeef)
Estado local persistente para planilla OPL, historial y configuración.
"""
import json
import re
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "local_data"
STATE_PATH = DATA_DIR / "canales_state.json"

OPL_DEFAULT = "TRANSCARNES"
OPL_EXCEPCIONES_DEFAULT = [
    ["AVILA MONSALVE REINALDO", "DRA CAVA", 0],
    ["BENITEZ GARNICA CEFERINO", "EDGAR AM", 0],
    ["CALIXTO ARDILA JAIME", "DRA CAVA", 0],
    ["CARNES SANTACRUZ S.A.S", "CSZ B/GA", 0],
    ["CRUZ LEONIDAS", "CAVA WO", 0],
    ["DRISTRIBUDORA DE CARNES AJR S.A.S", "CAVA AJR", 0],
    ["DISTRIBUIDORA DE CARNES AJR S.A.S", "CAVA AJR", 0],
    ["INVERSIONES ZULUAGA RUEDA S.A.S.", "MLT. GUARIN", 0],
    ["JAIMES BERMUDEZ JOSE MARIA", "MLT. GUARIN", 0],
    ["SANCHEZ CALDERON MIREYA", "CAVA MIREYA", 0],
    ["SUPERMERCADOS MAS POR MENOS S.A.S.", "MLT. GUARIN", 0],
    ["TECNOLOGIAS AGROPECUARIAS DE COLOMBIA S.A.S.", "CAVA T.A", 0],
    ["ROMERO OSORIO JOHN IGNACIO", "SMOYA", 0],
    ["COLBEEF S.A.S", "MLT. GUARIN", 0],
]

TURNOS = ["SxD", "VxS", "JxV", "MxJ", "MxM", "LxM", "DxL"]


def _now():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _blank_state():
    return {
        "opl_config": OPL_EXCEPCIONES_DEFAULT.copy(),
        "opl_progreso": [],
        "historico": [],
        "operacion_finalizada": False,
    }


def _load_state():
    DATA_DIR.mkdir(exist_ok=True)
    if not STATE_PATH.exists():
        state = _blank_state()
        _save_state(state)
        return state
    with STATE_PATH.open("r", encoding="utf-8") as fh:
        state = json.load(fh)
    base = _blank_state()
    for key, value in base.items():
        state.setdefault(key, value)
    if not state.get("opl_config"):
        state["opl_config"] = OPL_EXCEPCIONES_DEFAULT.copy()
        _save_state(state)
    return state


def _save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, default=str)


def _as_str(value):
    return "" if value is None else str(value).strip()


def _num(value):
    try:
        return float(value) if value not in ("", None) else 0
    except Exception:
        return 0


def _opl_map(state):
    config = state.get("opl_config") or []
    return {_as_str(row[0]).upper(): (_as_str(row[1]) or OPL_DEFAULT) for row in config if row and _as_str(row[0])}


def _resolver_opl(prop, mapa):
    return mapa.get(_as_str(prop).upper(), OPL_DEFAULT)


# ═══════════════════════════════════════════════════════
# OPL CONFIG
# ═══════════════════════════════════════════════════════
def getOplConfig():
    state = _load_state()
    config = state.get("opl_config", [])
    opls = sorted({_as_str(r[1]) for r in config if len(r) > 1 and _as_str(r[1])} | {OPL_DEFAULT})
    return {"success": True, "config": config, "opls": opls}


def upsertOpl(propietario, opl):
    state = _load_state()
    prop_key = _as_str(propietario).upper()
    if not prop_key or not _as_str(opl):
        return {"success": False, "message": "Propietario y OPL son obligatorios"}
    for row in state.get("opl_config", []):
        if _as_str(row[0]).upper() == prop_key:
            row[1] = _as_str(opl)
            _save_state(state)
            return {"success": True}
    state.setdefault("opl_config", []).append([prop_key, _as_str(opl), 0])
    _save_state(state)
    return {"success": True}


def eliminarOpl(rowIdx):
    state = _load_state()
    idx = int(rowIdx) - 2
    if 0 <= idx < len(state.get("opl_config", [])):
        state["opl_config"].pop(idx)
        _save_state(state)
    return {"success": True}


# ═══════════════════════════════════════════════════════
# PROGRESO OPL — guarda snapshot del progreso actual
# ═══════════════════════════════════════════════════════
def guardarProgresoOpl(progreso_list):
    """
    Recibe lista de dicts con progreso de cada propietario y los guarda.
    Llamado desde el frontend cuando se actualiza la planilla.
    """
    state = _load_state()
    state["opl_progreso"] = progreso_list or []
    state["operacion_finalizada"] = all(
        (r.get("total_pendiente", 1) == 0) for r in progreso_list
    ) if progreso_list else False
    _save_state(state)
    return {"success": True}


def getProgresoOpl():
    state = _load_state()
    return {
        "success": True,
        "progreso": state.get("opl_progreso", []),
        "operacionFinalizada": state.get("operacion_finalizada", False),
        "fecha": _now(),
    }


def cerrarOperacion():
    state = _load_state()
    progreso = state.get("opl_progreso", [])
    for row in progreso:
        item = dict(row)
        item["fecha"] = _now()
        state.setdefault("historico", []).append(item)
    state["opl_progreso"] = []
    state["operacion_finalizada"] = False
    _save_state(state)
    return {"success": True, "insertados": len(progreso)}


def getHistorico():
    state = _load_state()
    return {"success": True, "historico": state.get("historico", [])}


def limpiarHistorico():
    state = _load_state()
    state["historico"] = []
    _save_state(state)
    return {"success": True}


# ═══════════════════════════════════════════════════════
# DISPATCH
# ═══════════════════════════════════════════════════════
def dispatch(function_name, args):
    allowed = globals().get(function_name)
    if not callable(allowed) or function_name.startswith("_"):
        return {"success": False, "message": f"Funcion no implementada: {function_name}"}
    return allowed(*(args or []))
