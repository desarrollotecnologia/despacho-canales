"""
apps_script_local.py — Despacho de Canales (Colbeef)
Capa operativa del gestor. Persistencia en BD propia (db_gestor / SQLite).
SIRT no se escribe. La firma pública hacia main.py se mantiene.
"""
from datetime import datetime

import db_gestor

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
ETIQUETAS_DIAS_GUARDADOS = 15

db_gestor.ensure_ready(opl_defaults=OPL_EXCEPCIONES_DEFAULT)


def _now():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _load_state():
    """Compatibilidad: snapshot con la forma del JSON anterior, leído de la BD."""
    db_gestor.ensure_ready(opl_defaults=OPL_EXCEPCIONES_DEFAULT)
    return db_gestor.export_state_dict()


def _save_state(_state):
    """No-op de compatibilidad. Las escrituras van por CRUD transaccional en db_gestor."""
    return None


def _as_str(value):
    return "" if value is None else str(value).strip()


def _num(value):
    try:
        return float(value) if value not in ("", None) else 0
    except Exception:
        return 0


def _opl_map(state=None):
    if state is None:
        return db_gestor.opl_map()
    config = state.get("opl_config") or []
    return {
        _as_str(row[0]).upper(): (_as_str(row[1]) or OPL_DEFAULT)
        for row in config
        if row and _as_str(row[0])
    }


def _resolver_opl(prop, mapa):
    return mapa.get(_as_str(prop).upper(), OPL_DEFAULT)


# ═══════════════════════════════════════════════════════════════════════════
# OPL CONFIG
# ═══════════════════════════════════════════════════════════════════════════
def getOplConfig():
    config = db_gestor.list_opl_config()
    if not config:
        for row in OPL_EXCEPCIONES_DEFAULT:
            db_gestor.upsert_opl(row[0], row[1], row[2] if len(row) > 2 else 0)
        config = db_gestor.list_opl_config()
    opls = sorted({_as_str(r[1]) for r in config if len(r) > 1 and _as_str(r[1])} | {OPL_DEFAULT})
    return {"success": True, "config": config, "opls": opls}


def upsertOpl(propietario, opl):
    prop_key = _as_str(propietario).upper()
    if not prop_key or not _as_str(opl):
        return {"success": False, "message": "Propietario y OPL son obligatorios"}
    db_gestor.upsert_opl(prop_key, _as_str(opl), 0)
    return {"success": True}


def eliminarOpl(rowIdx):
    idx = int(rowIdx) - 2
    db_gestor.delete_opl_by_index(idx)
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════
# ASIGNADO CONGELADO — meta del día (estilo Vísceras)
# ═══════════════════════════════════════════════════════════════════════════
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
    - Nunca baja solo: para bajarlo hay que usar recalcular_asignado_congelado.
    - Un snapshot vacío (BD caída / consulta fallida) no toca lo guardado.
    """
    key_turno = _as_str(turno) or "Todos"
    prev = db_gestor.get_asignado(_as_str(fecha), key_turno) or {}

    vivo_medias = int(_num(snapshot.get("medias")))
    vivo_mc1 = int(_num(snapshot.get("mc1")))
    vivo_mc2 = int(_num(snapshot.get("mc2")))
    vivo_opl = snapshot.get("por_opl") or {}

    prev_medias = int(_num(prev.get("medias")))
    prev_opl = prev.get("por_opl") or {}

    if vivo_medias <= 0 and prev_medias > 0:
        return prev

    if vivo_medias > prev_medias:
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
        "turno": key_turno,
        "medias": medias,
        "mc1": mc1,
        "mc2": mc2,
        "canales": medias * 0.5,
        "por_opl": por_opl,
        "actualizado": _now(),
        "vivo_medias": vivo_medias,
        "prev_medias": prev_medias,
    }
    db_gestor.upsert_asignado(_as_str(fecha), key_turno, nuevo)
    return nuevo


def recalcular_asignado_congelado(fecha):
    """Borra la meta congelada de la fecha (todos los turnos); la próxima consulta la fija con el vivo."""
    borrados = db_gestor.delete_asignado_fecha(_as_str(fecha))
    return {"success": True, "borrados": list(borrados)}


def get_asignado_congelado(fecha, turno=None):
    return db_gestor.get_asignado(_as_str(fecha), _as_str(turno) or "Todos")


# ═══════════════════════════════════════════════════════════════════════════
# OPLS PARTICULARES — selección para Excel multi-hoja
# ═══════════════════════════════════════════════════════════════════════════
def getOplsParticulares():
    lista = [_as_str(x) for x in db_gestor.list_opls_particulares() if _as_str(x)]
    vistos = set()
    out = []
    for opl in lista:
        key = opl.upper()
        if key in vistos:
            continue
        vistos.add(key)
        out.append(opl)
    return {"success": True, "opls": out}


def setOplsParticulares(opls):
    if not isinstance(opls, list):
        return {"success": False, "message": "opls debe ser una lista"}
    limpios = db_gestor.set_opls_particulares(opls)
    return {"success": True, "opls": limpios}


# ═══════════════════════════════════════════════════════════════════════════
# ETIQUETAS — rollo 2 o 4 manillas por pasada
# ═══════════════════════════════════════════════════════════════════════════
def get_etiquetas_rollo_columnas():
    try:
        n = int(db_gestor.get_setting("etiquetas_rollo_columnas") or 4)
    except (TypeError, ValueError):
        n = 4
    return 2 if n == 2 else 4


def set_etiquetas_rollo_columnas(columnas):
    try:
        n = int(columnas)
    except (TypeError, ValueError):
        return {"success": False, "message": "columnas debe ser 2 o 4"}
    if n not in (2, 4):
        return {"success": False, "message": "columnas debe ser 2 o 4"}
    db_gestor.set_setting("etiquetas_rollo_columnas", n)
    return {"success": True, "columnas": n}


# ═══════════════════════════════════════════════════════════════════════════
# ETIQUETAS DE DESPACHO — registro de impresas por fecha
# ═══════════════════════════════════════════════════════════════════════════
def get_etiquetas_impresas(fecha):
    return db_gestor.get_etiquetas_fecha(_as_str(fecha))


def marcar_etiquetas_impresas(fecha, codigos):
    key = _as_str(fecha)
    n = db_gestor.marcar_etiquetas(
        key, codigos or [], _now(), prune_keep=ETIQUETAS_DIAS_GUARDADOS
    )
    return {"success": True, "fecha": key, "total": n}


def limpiar_etiquetas_impresas(fecha, codigos=None):
    """Borra marcas de impresión del día (todas, o solo los códigos indicados)."""
    key = _as_str(fecha)
    res = db_gestor.limpiar_etiquetas_fecha(key, codigos)
    return {
        "success": True,
        "fecha": key,
        "limpiadas": res["limpiadas"],
        "quedan": res["quedan"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# ADICIONALES — salidas extra del día (estilo Vísceras)
# ═══════════════════════════════════════════════════════════════════════════
def getAdicionales(fecha):
    key = _as_str(fecha)
    filas = db_gestor.get_adicionales(key)
    return {
        "success": True,
        "fecha": key,
        "total": len(filas),
        "filas": filas,
    }


def reemplazarAdicionales(fecha, filas):
    db_gestor.set_adicionales(_as_str(fecha), filas or [])
    return getAdicionales(fecha)


def agregarAdicionales(fecha, nuevas):
    """Agrega adicionales evitando duplicados por código."""
    key = _as_str(fecha)
    actuales = list(db_gestor.get_adicionales(key))
    vistos = {_as_str(r.get("codigo")).upper() for r in actuales if _as_str(r.get("codigo"))}
    agregados = 0
    ignorados = 0
    for row in nuevas or []:
        codigo = _as_str(row.get("codigo"))
        if not codigo:
            continue
        cup = codigo.upper()
        if cup in vistos:
            ignorados += 1
            continue
        vistos.add(cup)
        actuales.append(row)
        agregados += 1
    db_gestor.set_adicionales(key, actuales)
    return {
        "success": True,
        "fecha": key,
        "agregados": agregados,
        "ignorados": ignorados,
        "total": len(actuales),
        "filas": actuales,
    }


def quitarAdicionalesPorCodigos(fecha, codigos):
    key = _as_str(fecha)
    actuales = list(db_gestor.get_adicionales(key))
    quitar = {_as_str(c).upper() for c in (codigos or []) if _as_str(c)}
    if not quitar:
        return {"success": True, "eliminados": 0, "total": len(actuales), "filas": actuales}
    queda = [r for r in actuales if _as_str(r.get("codigo")).upper() not in quitar]
    eliminados = len(actuales) - len(queda)
    db_gestor.set_adicionales(key, queda)
    return {"success": True, "eliminados": eliminados, "total": len(queda), "filas": queda}


def actualizarDestinoAdicionales(fecha, cambios):
    """cambios: [{codigo, zona|destino}]"""
    key = _as_str(fecha)
    actuales = list(db_gestor.get_adicionales(key))
    mapa = {
        _as_str(c.get("codigo")).upper(): _as_str(c.get("zona") or c.get("destino"))
        for c in (cambios or [])
        if _as_str(c.get("codigo"))
    }
    actualizados = 0
    for row in actuales:
        cup = _as_str(row.get("codigo")).upper()
        if cup in mapa and mapa[cup]:
            row["zona"] = mapa[cup]
            row["destino"] = mapa[cup]
            actualizados += 1
    db_gestor.set_adicionales(key, actuales)
    return {"success": True, "actualizados": actualizados, "total": len(actuales), "filas": actuales}


def limpiarAdicionales(fecha=None):
    db_gestor.clear_adicionales(_as_str(fecha) if fecha else None)
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════
# PROGRESO OPL — guarda snapshot del progreso actual
# ═══════════════════════════════════════════════════════════════════════════
def guardarProgresoOpl(progreso_list):
    """
    Recibe lista de dicts con progreso de cada propietario y los guarda.
    Llamado desde el frontend cuando se actualiza la planilla.
    """
    items = progreso_list or []
    db_gestor.save_opl_progreso(items)
    finalizada = all((r.get("total_pendiente", 1) == 0) for r in items) if items else False
    db_gestor.set_setting("operacion_finalizada", "1" if finalizada else "0")
    return {"success": True}


def getProgresoOpl():
    return {
        "success": True,
        "progreso": db_gestor.get_opl_progreso(),
        "operacionFinalizada": db_gestor.get_setting("operacion_finalizada", "0") == "1",
        "fecha": _now(),
    }


def cerrarOperacion():
    progreso = db_gestor.get_opl_progreso()
    stamp = _now()
    for row in progreso:
        item = dict(row)
        item["fecha"] = stamp
        db_gestor.append_historico(item, fecha=stamp)
    db_gestor.clear_opl_progreso()
    db_gestor.set_setting("operacion_finalizada", "0")
    return {"success": True, "insertados": len(progreso)}


def getHistorico():
    return {"success": True, "historico": db_gestor.list_historico()}


def limpiarHistorico():
    db_gestor.clear_historico()
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════
# DISPATCH
# ═══════════════════════════════════════════════════════════════════════════
def dispatch(function_name, args):
    allowed = globals().get(function_name)
    if not callable(allowed) or function_name.startswith("_"):
        return {"success": False, "message": f"Funcion no implementada: {function_name}"}
    return allowed(*(args or []))
