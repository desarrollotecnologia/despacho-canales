"""
Reetiquetado de media canal — Zebra ZT231 203 dpi (ZPL).

Misma tira que la etiqueta original de SIRT: 2.5 × 26.35 cm = 197 × 2106 dots,
sale a lo largo (el texto va rotado 90° para leerse a lo largo de la tira).
Bloques, de izquierda a derecha, centrados en la tira (margen en los cantos
y corte antes del logo preimpreso, para que no se recorten al imprimir):
  · Bloque 1: puesto + turno, código de barras, código del animal y tipo de media.
  · (espacio del QR: vacío, el reetiquetado no lleva QR)
  · Bloque 3: cuarto + CAM1/CAM2, ubicación, zona y fecha de despacho.
  · Bloque 4: cliente, puesto, turno, OPL, destino, dirección, especie.
  · (espacio del logo: vacío, el logo viene preimpreso en el rollo)

El diseño se define en coordenadas "a lo largo" (X sobre el largo, Y sobre el ancho)
y se convierte a coordenadas de la impresora al generar el ZPL; la vista previa
de la pantalla usa el mismo diseño.

Envío: si ZEBRA_HOST está definido se manda por red (puerto 9100);
si no, en crudo (RAW) a la cola de Windows ZEBRA_PRINTER.
"""
import os
import re
import socket

LARGO_DOTS = 2106
ANCHO_DOTS = 197
IMPRESORA_DEFAULT = "ZDesigner ZT231-203dpi ZPL"

# Presets de rollo: solo cambian columnas/tiras/margen/paso.
# El resto (oscuridad, velocidad, invertir, ajustes, ancho cabezal) es común.
# 4 = rollo definitivo calibrado (cabezal completo ~832 dots).
# 2 = solo las 2 manillas físicas del rollo viejo (2ª y 3ª tira del de 4).
#     Ancho de impresión se recorta a esas tiras para no “disparar” fuera del papel.
# invertir_orden: con ^POI, la 1ª del lote debe ir a la tira que el operario ve primero
# (separadora cava/riel antes que la etiqueta). Igual en 2 y en 4.
ROLLO_PRESETS = {
    4: {
        "columnas": 4, "margen": 53, "paso": 188,
        "tiras": "53,241,429,632", "env_tiras": "ZEBRA_ETQ_TIRAS",
        "invertir_orden": True,
        "ancho_total": 832,
    },
    2: {
        "columnas": 2, "margen": 241, "paso": 188,
        "tiras": "241,429", "env_tiras": "ZEBRA_ETQ_TIRAS_2",
        "invertir_orden": True,
        # Desde el inicio de la 1ª tira hasta el final de la 2ª (no usar las 4 del cabezal).
        "ancho_total": 429 + 197,  # 626
    },
}

# Provider opcional (p. ej. estado local) → 2 u 4. Si no hay, env o default 4.
_columnas_provider = None


def set_columnas_provider(fn) -> None:
    global _columnas_provider
    _columnas_provider = fn


def columnas_rollo_activas() -> int:
    if _columnas_provider:
        try:
            n = int(_columnas_provider() or 4)
            if n in ROLLO_PRESETS:
                return n
        except Exception:
            pass
    n = int(os.getenv("ZEBRA_ETQ_COLUMNAS", "4") or 4)
    return n if n in ROLLO_PRESETS else 4


def config_impresora() -> dict:
    host = (os.getenv("ZEBRA_HOST") or "").strip()
    cols = columnas_rollo_activas()
    preset = ROLLO_PRESETS[cols]
    tiras_txt = (os.getenv(preset["env_tiras"]) or "").strip() or preset["tiras"]
    # Fallback: si el de 2 no tiene env propio, no heredar el de 4 (rompería el preset)
    return {
        "impresora": (os.getenv("ZEBRA_PRINTER") or IMPRESORA_DEFAULT).strip(),
        "host": host,
        "puerto": int(os.getenv("ZEBRA_PORT", "9100") or 9100),
        "modo": "red" if host else "windows",
        # Con oscuridad 15 a 6 ips el rollo de manillas casi no marcaba
        "velocidad": int(os.getenv("ZEBRA_VELOCIDAD", "4") or 4),
        "oscuridad": int(os.getenv("ZEBRA_OSCURIDAD", "25") or 25),
        # Con el rollo actual sale girada 180°; ZEBRA_ETQ_INVERTIR=0 la deja sin girar
        "invertir": (os.getenv("ZEBRA_ETQ_INVERTIR") or "1").strip().lower() in ("1", "true", "si", "sí"),
        # Calibración fina en dots (+ mueve hacia el logo / hacia abajo del texto)
        "ajuste_largo": int(os.getenv("ZEBRA_ETQ_AJUSTE_LARGO", "0") or 0),
        "ajuste_ancho": int(os.getenv("ZEBRA_ETQ_AJUSTE_ANCHO", "0") or 0),
        # Rollo de varias tiras por pasada: ancho del cabezal, nº de tiras,
        # dónde empieza la primera y distancia entre el inicio de una tira y la siguiente
        "ancho_total": (
            int(os.getenv("ZEBRA_ETQ_ANCHO_TOTAL", "832") or 832)
            if cols == 4
            else int(preset.get("ancho_total") or (max(
                [int(v) for v in re.findall(r"-?\d+", tiras_txt)] or [241]
            ) + ANCHO_DOTS))
        ),
        "columnas": preset["columnas"],
        # En rollo de 4 se respetan env de margen/paso; en 2 solo el preset (no mezclar).
        "margen": (
            int(os.getenv("ZEBRA_ETQ_MARGEN", "53") or 53)
            if cols == 4
            else preset["margen"]
        ),
        "paso": (
            int(os.getenv("ZEBRA_ETQ_PASO", "188") or 188)
            if cols == 4
            else preset["paso"]
        ),
        # Inicio exacto de cada tira (dots). 4 = calibración actual; 2 = preset temporal.
        "tiras": [int(v) for v in re.findall(r"-?\d+", tiras_txt)],
        "invertir_orden": bool(preset.get("invertir_orden", True)),
    }


def abreviar_ubicacion(cava: str, riel: str) -> str:
    """'Cava 2 - Riel 9' → C2R9 · 'Cava 6A' → C6A."""
    txt_riel = str(riel or "")
    m = re.search(r"cava\s*([0-9]+[a-z]?)\D*?riel\s*([0-9]+[a-z]?)", txt_riel, re.I)
    if m:
        return f"C{m.group(1).upper()}R{m.group(2).upper()}"
    m_c = re.search(r"cava\s*([0-9]+[a-z]?)\b", str(cava or ""), re.I)
    m_r = re.search(r"riel\s*([0-9]+[a-z]?)\b", txt_riel, re.I)
    out = ""
    if m_c:
        out += f"C{m_c.group(1).upper()}"
    if m_r:
        out += f"R{m_r.group(1).upper()}"
    return out


def _zpl_txt(valor) -> str:
    """^ y ~ son comandos ZPL: no pueden ir dentro de un campo."""
    return str(valor or "").replace("^", " ").replace("~", " ").strip()


def _corto(valor, max_chars: int) -> str:
    txt = _zpl_txt(valor)
    return txt if len(txt) <= max_chars else txt[:max_chars].rstrip()


def _fecha_dmy(iso: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


# Área útil de la tira: el logo preimpreso empieza ~1670 y los bordes
# largos de la manilla recortan lo que queda a menos de ~2.5 mm del canto.
LOGO_X = 1600
MARGEN_LARGO = 80
MARGEN_ANCHO = 22
GAP_BLOQUES = 36


def _dots_texto(texto: str, w: int) -> int:
    return len(texto) * max(1, int(w))


def _dots_barras(texto: str, modulo: int) -> int:
    # Code 128: inicio + datos + checksum + stop ≈ 11*n + 35 módulos.
    return (11 * max(len(texto), 1) + 35) * max(1, int(modulo))


def _ancho_elemento(e: dict) -> int:
    x = int(e.get("x_rel") or 0)
    if e["tipo"] == "barras":
        return x + _dots_barras(e["texto"], e.get("modulo") or 2)
    if e["tipo"] == "linea":
        return x + int(e.get("largo") or 0)
    return x + _dots_texto(e["texto"], int(e.get("w") or e["h"]))


def _centrar_en_ancho(elementos: list, margen: int = MARGEN_ANCHO) -> None:
    """Deja el mismo margen arriba y abajo de la tira para que no se corte el canto."""
    if not elementos:
        return
    top = min(e["y"] for e in elementos)
    bot = max(e["y"] + int(e["h"]) for e in elementos)
    alto = bot - top
    libre = ANCHO_DOTS - 2 * margen
    y0 = margen + max(0, (libre - alto) // 2)
    dy = y0 - top
    for e in elementos:
        e["y"] += dy


def _centrar_en_largo(grupos: list) -> list:
    """Centra los bloques entre el inicio de la tira y el logo preimpreso."""
    presentes = [g for g in grupos if g]
    if not presentes:
        return []
    anchos = [max(_ancho_elemento(e) for e in g) for g in presentes]
    total = sum(anchos) + GAP_BLOQUES * (len(presentes) - 1)
    zona = LOGO_X - MARGEN_LARGO
    x = MARGEN_LARGO + max(0, (zona - total) // 2)
    out = []
    for grupo, ancho in zip(presentes, anchos):
        for e in grupo:
            e["x"] = x + int(e.pop("x_rel", 0))
            out.append(e)
        x += ancho + GAP_BLOQUES
    return out


def layout_etiqueta(et: dict) -> list:
    """Elementos de la tira en coordenadas a lo largo: x (largo), y (ancho), h (alto).

    El bloque queda centrado en la manilla (margen arriba/abajo) y termina
    antes del logo, para que la Zebra no recorte los cantos ni lo imprima encima.
    """
    codigo = _zpl_txt(et.get("codigo_barras"))
    puesto = _corto(et.get("puesto_turno") or "SIN PUESTO", 16)
    animal = _zpl_txt(et.get("codigo_animal"))
    tipo = _zpl_txt(et.get("tipo"))
    cuarto = _zpl_txt(et.get("cuarto"))
    cam = _zpl_txt(et.get("cam"))
    ubi = f"Ubicacion: {_zpl_txt(et.get('ubicacion')) or '-'}"
    zona = f"Zona: {_corto(et.get('zona') or '-', 18)}"
    desp = f"Despacho: {_fecha_dmy(et.get('fecha_programacion')) or '-'}"
    c4 = 36
    f_w = 14

    b1 = []
    y = 0
    if puesto:
        b1.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 36, "w": 26, "texto": puesto})
        y += 40
    if codigo:
        b1.append({"tipo": "barras", "x_rel": 0, "y": y, "h": 40, "modulo": 2, "texto": codigo})
        y += 44
    if animal:
        b1.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 32, "w": 26, "texto": animal})
        y += 34
    if tipo:
        b1.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 16, "w": 14, "texto": tipo})

    b3 = []
    y = 0
    if cuarto or cam:
        if cuarto:
            b3.append({"tipo": "texto", "x_rel": 0, "y": 0, "h": 20, "w": 15, "texto": cuarto})
        if cam:
            x_cam = (_dots_texto(cuarto, 15) + 16) if cuarto else 0
            b3.append({"tipo": "texto", "x_rel": x_cam, "y": 0, "h": 22, "w": 18, "texto": cam})
        y = 26
    if ubi:
        b3.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 34, "w": 20, "texto": ubi})
        y += 38
    if zona:
        b3.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 18, "w": 15, "texto": zona})
        y += 22
    if desp:
        b3.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 18, "w": 15, "texto": desp})

    lineas = [
        _corto(f"Cliente {et.get('cliente') or '-'}", c4),
        _corto(f"Puesto {et.get('puesto') or '-'}", c4),
        _corto(f"Turno {et.get('turno') or '-'}", c4),
        _corto(f"OPL {et.get('opl') or '-'}", c4),
        _corto(f"Destino {et.get('destino') or '-'}", c4),
        _corto(et.get("direccion") or "", c4),
        "Especie Bovino",
    ]
    b4 = []
    y = 0
    for txt in lineas:
        if not txt:
            continue
        b4.append({"tipo": "texto", "x_rel": 0, "y": y, "h": 16, "w": f_w, "texto": txt})
        y += 20

    for grupo in (b1, b3, b4):
        _centrar_en_ancho(grupo)
    return _centrar_en_largo([b1, b3, b4])


def layout_separador(sep: dict) -> list:
    """Etiqueta que encabeza cada cliente (o cada riel), centrada en la tira."""
    total = int(sep.get("total") or 0)
    detalle = f"{total} etiqueta{'s' if total != 1 else ''}"
    if sep.get("puestos"):
        detalle += f" · Puestos: {_corto(sep.get('puestos'), 28)}"
    if sep.get("opls"):
        detalle += f" · OPL: {_corto(sep.get('opls'), 24)}"
    titulo = sep.get("titulo") or f"OPL {_corto(sep.get('opl') or '-', 24)}  ·  INICIO CLIENTE"
    titulo = _corto(titulo, 42)
    detalle = _corto(detalle, 52)
    cliente = _corto(sep.get("cliente") or "SIN CLIENTE", 28)
    # El ancho de la raya se ajusta al texto más largo, sin llegar al logo.
    ancho_txt = max(
        _dots_texto(titulo, 18),
        _dots_texto(cliente, 22),
        _dots_texto(detalle, 16),
        480,
    )
    largo = min(ancho_txt, LOGO_X - MARGEN_LARGO - 40)
    elementos = [
        {"tipo": "texto", "x_rel": 0, "y": 0, "h": 26, "w": 18, "texto": titulo},
        {"tipo": "linea", "x_rel": 0, "y": 32, "h": 4, "largo": largo, "texto": "-"},
        {"tipo": "texto", "x_rel": 0, "y": 44, "h": 40, "w": 22, "texto": cliente},
        {"tipo": "texto", "x_rel": 0, "y": 90, "h": 24, "w": 16, "texto": detalle},
    ]
    elementos = [e for e in elementos if e["texto"]]
    _centrar_en_ancho(elementos)
    return _centrar_en_largo([elementos])


def zpl_etiqueta(et: dict, copias: int = 1) -> str:
    copias = max(1, min(int(copias or 1), 20))
    return zpl_lote([layout_etiqueta(et)] * copias)


def _encabezado(cfg: dict) -> list:
    return [
        "^XA",
        "^CI28",
        f"^PW{cfg['ancho_total']}",
        f"^LL{LARGO_DOTS}",
        "^LH0,0",
        "^POI" if cfg["invertir"] else "^PON",
        f"^PR{cfg['velocidad']}",
        f"~SD{cfg['oscuridad']:02d}",
    ]


def _campos(layout: list, x_tira: int, cfg: dict) -> list:
    out = []
    for e in layout:
        h = e["h"]
        # Rotación R (90° horario): el largo de la tira es el eje y de la impresora
        # y la parte superior del texto queda hacia el borde derecho de la tira.
        fo_x = max(0, x_tira + ANCHO_DOTS - (e["y"] + cfg["ajuste_ancho"]) - h)
        fo_y = max(0, e["x"] + cfg["ajuste_largo"])
        if e["tipo"] == "linea":
            out.append(f"^FO{fo_x},{fo_y}^GB{h},{e['largo']},{h}^FS")
        elif e["tipo"] == "barras":
            out.append(f"^FO{fo_x},{fo_y}^BY{e['modulo']},3,{h}^BCR,{h},N,N,N,A^FD{e['texto']}^FS")
        else:
            # w opcional: alarga el carácter a lo largo de la tira (más visible)
            w = int(e.get("w") or h)
            out.append(f"^FO{fo_x},{fo_y}^A0R,{h},{w}^FD{e['texto']}^FS")
    return out


def _inicio_tira(cfg: dict, pos: int) -> int:
    tiras = cfg["tiras"]
    if len(tiras) >= cfg["columnas"]:
        return tiras[pos]
    return cfg["margen"] + pos * cfg["paso"]


def zpl_lote(layouts: list) -> str:
    """Reparte las etiquetas en las tiras del rollo: cada pasada imprime `columnas` diferentes."""
    cfg = config_impresora()
    n = cfg["columnas"]
    # invertir (^POI) + invertir_orden: en rollo de 4 la 1ª va arriba; en 2 la 1ª va a tira 0.
    rev = bool(cfg.get("invertir")) and bool(cfg.get("invertir_orden", True))
    formatos = []
    for i in range(0, len(layouts), n):
        lineas = _encabezado(cfg)
        for col, layout in enumerate(layouts[i:i + n]):
            pos = (n - 1 - col) if rev else col
            lineas += _campos(layout, _inicio_tira(cfg, pos), cfg)
        lineas += ["^PQ1,0,1,Y", "^XZ"]
        formatos.append("\n".join(lineas))
    return "\n".join(formatos)


def meta_pasadas(slots_codigo: list, fecha: str = "") -> list:
    """
    Alineado con zpl_lote: un ítem por layout (None = separadora, str = código).
    Devuelve [{codigos, marcas}] por cada ^XA…^XZ para marcar solo lo ya enviado.
    """
    n = max(1, int(config_impresora().get("columnas") or 4))
    out = []
    for i in range(0, len(slots_codigo), n):
        codigos = [c for c in slots_codigo[i:i + n] if c]
        marcas = [{"codigo": c, "fecha": fecha} for c in codigos if fecha]
        out.append({"codigos": codigos, "marcas": marcas})
    return out


def zpl_calibracion() -> str:
    """Regla para ubicar las tiras: números a lo ancho (dots) y a lo largo cada 200 dots."""
    cfg = config_impresora()
    lineas = _encabezado(cfg)
    for y0 in (300, 1000, 1700):
        for x in range(0, cfg["ancho_total"] + 1, 10):
            largo = 50 if x % 50 == 0 else 20
            lineas.append(f"^FO{x},{y0}^GB2,{largo},2^FS")
            if x % 50 == 0:
                lineas.append(f"^FO{x + 3},{y0 + 55}^A0R,18,16^FD{x}^FS")
    for x in range(20, cfg["ancho_total"], 100):
        for y in range(0, LARGO_DOTS, 200):
            if y in (300, 1000, 1700):
                continue
            lineas.append(f"^FO{x},{y}^A0R,22,20^FDL{y}^FS")
    lineas += ["^PQ1,0,1,Y", "^XZ"]
    return "\n".join(lineas)


def enviar_zpl(zpl: str) -> dict:
    cfg = config_impresora()
    data = zpl.encode("utf-8")
    if cfg["host"]:
        with socket.create_connection((cfg["host"], cfg["puerto"]), timeout=8) as s:
            s.sendall(data)
        return {"modo": "red", "destino": f"{cfg['host']}:{cfg['puerto']}"}

    try:
        import win32print
    except ImportError as e:
        raise RuntimeError("Falta pywin32 (pip install pywin32) o definir ZEBRA_HOST en .env") from e

    nombre = cfg["impresora"]
    try:
        h = win32print.OpenPrinter(nombre)
    except Exception as e:
        if getattr(e, "winerror", None) == 1801 or (getattr(e, "args", None) or [None])[0] == 1801:
            raise RuntimeError(
                f"el servidor no tiene instalada la impresora «{nombre}». "
                "Elige «Imprimir en la Zebra de este equipo» (requiere Zebra Browser Print)"
            ) from e
        raise
    try:
        win32print.StartDocPrinter(h, 1, ("Reetiquetado media canal", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    return {"modo": "windows", "destino": nombre}
