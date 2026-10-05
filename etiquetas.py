"""
Reetiquetado de media canal — Zebra ZT231 203 dpi (ZPL).

Misma tira que la etiqueta original de SIRT: 2.5 × 26.35 cm = 197 × 2106 dots,
sale a lo largo (el texto va rotado 90° para leerse a lo largo de la tira).
Bloques, de izquierda a derecha como en la original:
  · Bloque 1: puesto + turno, código de barras, código del animal y tipo de media.
  · (espacio del QR: vacío, el reetiquetado no lleva QR)
  · Bloque 3: código de barras, cuarto + CAM1/CAM2, ubicación, zona y fecha de despacho.
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


def config_impresora() -> dict:
    host = (os.getenv("ZEBRA_HOST") or "").strip()
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
        "ancho_total": int(os.getenv("ZEBRA_ETQ_ANCHO_TOTAL", "832") or 832),
        "columnas": max(1, int(os.getenv("ZEBRA_ETQ_COLUMNAS", "4") or 4)),
        # Medido con la regla de calibración (tiras de ~185 dots útiles cada 188)
        "margen": int(os.getenv("ZEBRA_ETQ_MARGEN", "53") or 53),
        "paso": int(os.getenv("ZEBRA_ETQ_PASO", "188") or 188),
        # Inicio exacto de cada tira (dots, separados por coma); manda sobre margen/paso.
        # La tira de arriba quedó 15 dots más adentro que el paso parejo.
        "tiras": [int(v) for v in re.findall(r"-?\d+", os.getenv("ZEBRA_ETQ_TIRAS", "53,241,429,632"))],
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


def layout_etiqueta(et: dict) -> list:
    """Elementos de la tira en coordenadas a lo largo: x (largo), y (ancho), h (alto)."""
    codigo = _zpl_txt(et.get("codigo_barras"))
    # El logo preimpreso empieza hacia el dot 1670 del largo y cada tira útil
    # mide ~185 dots de ancho: todo va entre y=6 y y=172 y termina antes de x≈1600.
    b1, b3, b4 = 220, 800, 1200
    c4 = 42
    elementos = [
        # Bloque 1
        {"tipo": "texto", "x": b1, "y": 10, "h": 32, "texto": _corto(et.get("puesto_turno") or "SIN PUESTO", 22)},
        {"tipo": "barras", "x": b1, "y": 46, "h": 54, "modulo": 2, "texto": codigo},
        {"tipo": "texto", "x": b1, "y": 104, "h": 40, "texto": _zpl_txt(et.get("codigo_animal"))},
        {"tipo": "texto", "x": b1, "y": 150, "h": 22, "texto": _zpl_txt(et.get("tipo"))},
        # Bloque 3
        {"tipo": "barras", "x": b3, "y": 8, "h": 50, "modulo": 2, "texto": codigo},
        {"tipo": "texto", "x": b3, "y": 64, "h": 20, "texto": _zpl_txt(et.get("cuarto"))},
        {"tipo": "texto", "x": b3 + 270, "y": 60, "h": 34, "texto": _zpl_txt(et.get("cam"))},
        {"tipo": "texto", "x": b3, "y": 90, "h": 24, "texto": f"Ubicacion: {_zpl_txt(et.get('ubicacion')) or '-'}"},
        {"tipo": "texto", "x": b3, "y": 120, "h": 20, "texto": f"Zona: {_corto(et.get('zona') or '-', 26)}"},
        {"tipo": "texto", "x": b3, "y": 146, "h": 20, "texto": f"Despacho: {_fecha_dmy(et.get('fecha_programacion')) or '-'}"},
        # Bloque 4
        {"tipo": "texto", "x": b4, "y": 6, "h": 17, "texto": _corto(f"Cliente {et.get('cliente') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 29, "h": 17, "texto": _corto(f"Puesto {et.get('puesto') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 52, "h": 17, "texto": _corto(f"Turno {et.get('turno') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 75, "h": 17, "texto": _corto(f"OPL {et.get('opl') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 98, "h": 17, "texto": _corto(f"Destino {et.get('destino') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 121, "h": 17, "texto": _corto(et.get("direccion") or "", c4)},
        {"tipo": "texto", "x": b4, "y": 144, "h": 17, "texto": "Especie Bovino"},
    ]
    return [e for e in elementos if e["texto"]]


def layout_separador(sep: dict) -> list:
    """Etiqueta que encabeza cada cliente en la impresión por OPL."""
    x0 = 220
    total = int(sep.get("total") or 0)
    detalle = f"{total} etiqueta{'s' if total != 1 else ''}"
    if sep.get("puestos"):
        detalle += f" · Puestos: {_corto(sep.get('puestos'), 40)}"
    elementos = [
        {"tipo": "texto", "x": x0, "y": 10, "h": 28, "texto": f"OPL {_corto(sep.get('opl') or '-', 30)}  ·  INICIO CLIENTE"},
        {"tipo": "linea", "x": x0, "y": 44, "h": 5, "largo": 1180, "texto": "-"},
        {"tipo": "texto", "x": x0, "y": 60, "h": 50, "texto": _corto(sep.get("cliente") or "SIN CLIENTE", 40)},
        {"tipo": "texto", "x": x0, "y": 124, "h": 30, "texto": detalle},
    ]
    return [e for e in elementos if e["texto"]]


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
            out.append(f"^FO{fo_x},{fo_y}^A0R,{h},{h}^FD{e['texto']}^FS")
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
    formatos = []
    for i in range(0, len(layouts), n):
        lineas = _encabezado(cfg)
        # Con la impresión girada la primera de la pasada queda en la tira de arriba
        for col, layout in enumerate(layouts[i:i + n]):
            pos = (n - 1 - col) if cfg["invertir"] else col
            lineas += _campos(layout, _inicio_tira(cfg, pos), cfg)
        lineas += ["^PQ1,0,1,Y", "^XZ"]
        formatos.append("\n".join(lineas))
    return "\n".join(formatos)


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
    h = win32print.OpenPrinter(nombre)
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
