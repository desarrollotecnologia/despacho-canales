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
        # Asignado congelado estilo Vísceras: meta del día por fecha/turno.
        # Solo sube si asignan más; solo baja si el total vivo (pend+sal) baja
        # (cancelación / se quita la asignación o una salida deja de contar).
        "asignado_congelado": {},
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
# ASIGNADO CONGELADO — meta del día (estilo Vísceras)
# ═══════════════════════════════════════════════════════
def _clave_asignado(fecha, turno=None):
    t = _as_str(turno) or "Todos"
    return f"{_as_str(fecha)}|{t}"


def actualizar_asignado_congelado(fecha, turno, snapshot):
    """
    Actualiza la meta congelada del día.

    snapshot vivo = pendientes + salidas actuales:
      {
        "medias": int, "mc1": int, "mc2": int,
        "por_opl": { "OPL": {"medias": n, "mc1": n, "mc2": n} }
      }

    Reglas estilo Vísceras:
    - Si asignan más → el congelado sube.
    - Si pistolean → no cambia (pend baja, sal sube, suma igual).
    - Si baja el total vivo → el congelado baja (cancelación /
      una salida marcada deja de contar en el día).
    """
    state = _load_state()
    key = _clave_asignado(fecha, turno)
    bag = state.setdefault("asignado_congelado", {})
    prev = bag.get(key) or {}

    vivo_medias = int(_num(snapshot.get("medias")))
    vivo_mc1 = int(_num(snapshot.get("mc1")))
    vivo_mc2 = int(_num(snapshot.get("mc2")))
    vivo_opl = snapshot.get("por_opl") or {}

    prev_medias = int(_num(prev.get("medias")))
    prev_opl = prev.get("por_opl") or {}

    # Universo asignado = pend + sal. El pistoleo lo deja igual.
    if vivo_medias > prev_medias:
        medias, mc1, mc2 = vivo_medias, vivo_mc1, vivo_mc2
    elif vivo_medias < prev_medias:
        medias, mc1, mc2 = vivo_medias, vivo_mc1, vivo_mc2
    elif prev_medias:
        medias = prev_medias
        mc1 = int(_num(prev.get("mc1"))) or vivo_mc1
        mc2 = int(_num(prev.get("mc2"))) or vivo_mc2
    else:
        medias, mc1, mc2 = vivo_medias, vivo_mc1, vivo_mc2

    por_opl = {}
    opl_keys = set(prev_opl.keys()) | set(vivo_opl.keys())
    for opl in opl_keys:
        v = vivo_opl.get(opl) or {}
        p = prev_opl.get(opl) or {}
        v_m = int(_num(v.get("medias")))
        p_m = int(_num(p.get("medias")))
        if v_m > p_m:
            m, a, b = v_m, int(_num(v.get("mc1"))), int(_num(v.get("mc2")))
        elif v_m < p_m:
            m, a, b = v_m, int(_num(v.get("mc1"))), int(_num(v.get("mc2")))
        elif p_m:
            m = p_m
            a = int(_num(p.get("mc1"))) or int(_num(v.get("mc1")))
            b = int(_num(p.get("mc2"))) or int(_num(v.get("mc2")))
        else:
            m, a, b = v_m, int(_num(v.get("mc1"))), int(_num(v.get("mc2")))
        if m > 0 or v_m > 0 or p_m > 0:
            por_opl[opl] = {"medias": m, "mc1": a, "mc2": b}

    nuevo = {
        "fecha": _as_str(fecha),
        "turno": _as_str(turno) or "Todos",
        "medias": medias,
        "mc1": mc1,
        "mc2": mc2,
        "canales": medias * 0.5,
        "por_opl": por_opl,
        "actualizado": _now(),
        "vivo_medias": vivo_medias,
        "prev_medias": prev_medias,
    }
    bag[key] = nuevo
    state["asignado_congelado"] = bag
    _save_state(state)
    return nuevo


def get_asignado_congelado(fecha, turno=None):
    state = _load_state()
    return (state.get("asignado_congelado") or {}).get(_clave_asignado(fecha, turno))


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
