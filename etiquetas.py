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
        # 6 ips ≈ 15.2 cm/s y oscuridad 15, como las preferencias del driver
        "velocidad": int(os.getenv("ZEBRA_VELOCIDAD", "6") or 6),
        "oscuridad": int(os.getenv("ZEBRA_OSCURIDAD", "15") or 15),
        # Si sale al revés respecto al logo preimpreso: ZEBRA_ETQ_INVERTIR=1 (gira 180°)
        "invertir": (os.getenv("ZEBRA_ETQ_INVERTIR") or "").strip() in ("1", "true", "si", "sí"),
        # Calibración fina en dots (+ mueve hacia el logo / hacia abajo del texto)
        "ajuste_largo": int(os.getenv("ZEBRA_ETQ_AJUSTE_LARGO", "0") or 0),
        "ajuste_ancho": int(os.getenv("ZEBRA_ETQ_AJUSTE_ANCHO", "0") or 0),
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
    b1, b3, b4 = 530, 1130, 1545
    # Bloque 4 tiene ~200 dots de ancho útil antes del logo
    c4 = 21
    elementos = [
        # Bloque 1
        {"tipo": "texto", "x": b1, "y": 12, "h": 36, "texto": _corto(et.get("puesto_turno") or "SIN PUESTO", 22)},
        {"tipo": "barras", "x": b1, "y": 52, "h": 60, "modulo": 2, "texto": codigo},
        {"tipo": "texto", "x": b1, "y": 118, "h": 42, "texto": _zpl_txt(et.get("codigo_animal"))},
        {"tipo": "texto", "x": b1, "y": 166, "h": 22, "texto": _zpl_txt(et.get("tipo"))},
        # Bloque 3
        {"tipo": "barras", "x": b3, "y": 8, "h": 56, "modulo": 2, "texto": codigo},
        {"tipo": "texto", "x": b3, "y": 76, "h": 22, "texto": _zpl_txt(et.get("cuarto"))},
        {"tipo": "texto", "x": b3 + 270, "y": 70, "h": 36, "texto": _zpl_txt(et.get("cam"))},
        {"tipo": "texto", "x": b3, "y": 104, "h": 26, "texto": f"Ubicacion: {_zpl_txt(et.get('ubicacion')) or '-'}"},
        {"tipo": "texto", "x": b3, "y": 136, "h": 20, "texto": f"Zona: {_corto(et.get('zona') or '-', 26)}"},
        {"tipo": "texto", "x": b3, "y": 162, "h": 20, "texto": f"Despacho: {_fecha_dmy(et.get('fecha_programacion')) or '-'}"},
        # Bloque 4
        {"tipo": "texto", "x": b4, "y": 8, "h": 18, "texto": _corto(f"Cliente {et.get('cliente') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 32, "h": 18, "texto": _corto(f"Puesto {et.get('puesto') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 56, "h": 18, "texto": _corto(f"Turno {et.get('turno') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 80, "h": 18, "texto": _corto(f"OPL {et.get('opl') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 104, "h": 18, "texto": _corto(f"Destino {et.get('destino') or '-'}", c4)},
        {"tipo": "texto", "x": b4, "y": 128, "h": 18, "texto": _corto(et.get("direccion") or "", c4)},
        {"tipo": "texto", "x": b4, "y": 152, "h": 18, "texto": "Especie Bovino"},
    ]
    return [e for e in elementos if e["texto"]]


def zpl_etiqueta(et: dict, copias: int = 1) -> str:
    cfg = config_impresora()
    copias = max(1, min(int(copias or 1), 20))
    lineas = [
        "^XA",
        "^CI28",
        f"^PW{ANCHO_DOTS}",
        f"^LL{LARGO_DOTS}",
        "^LH0,0",
        "^POI" if cfg["invertir"] else "^PON",
        f"^PR{cfg['velocidad']}",
        f"~SD{cfg['oscuridad']:02d}",
    ]
    for e in layout_etiqueta(et):
        h = e["h"]
        # Rotación R (90° horario): el largo de la tira es el eje y de la impresora
        # y la parte superior del texto queda hacia el borde x = ANCHO_DOTS.
        fo_x = max(0, ANCHO_DOTS - (e["y"] + cfg["ajuste_ancho"]) - h)
        fo_y = max(0, e["x"] + cfg["ajuste_largo"])
        if e["tipo"] == "barras":
            lineas.append(f"^FO{fo_x},{fo_y}^BY{e['modulo']},3,{h}^BCR,{h},N,N,N,A^FD{e['texto']}^FS")
        else:
            lineas.append(f"^FO{fo_x},{fo_y}^A0R,{h},{h}^FD{e['texto']}^FS")
    lineas += [f"^PQ{copias},0,1,Y", "^XZ"]
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
