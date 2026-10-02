"""
Reetiquetado de media canal — Zebra ZT231 203 dpi (ZPL).

Etiqueta 7.62 × 5.08 cm (3" × 2") = 609 × 406 dots.
Solo lleva los bloques del reetiquetado:
  · Bloque superior: código de barras, ubicación (C2R9) y CAM1/CAM2.
  · Bloque inferior: puesto + turno, código de barras, código del animal y tipo de media.

Envío: si ZEBRA_HOST está definido se manda por red (puerto 9100);
si no, en crudo (RAW) a la cola de Windows ZEBRA_PRINTER.
"""
import os
import re
import socket

ANCHO_DOTS = 609
ALTO_DOTS = 406
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


def zpl_etiqueta(et: dict, copias: int = 1) -> str:
    cfg = config_impresora()
    codigo = _zpl_txt(et.get("codigo_barras"))
    cam = _zpl_txt(et.get("cam"))
    ubic = _zpl_txt(et.get("ubicacion"))
    cuarto = _zpl_txt(et.get("cuarto"))
    puesto_turno = _zpl_txt(et.get("puesto_turno"))
    animal = _zpl_txt(et.get("codigo_animal"))
    tipo = _zpl_txt(et.get("tipo"))
    copias = max(1, min(int(copias or 1), 20))
    return "\n".join([
        "^XA",
        "^CI28",
        f"^PW{ANCHO_DOTS}",
        f"^LL{ALTO_DOTS}",
        "^LH0,0",
        f"^PR{cfg['velocidad']}",
        f"~SD{cfg['oscuridad']:02d}",
        # Bloque superior
        f"^FO20,14^BY2,3,72^BCN,72,N,N,N,A^FD{codigo}^FS",
        f"^FO440,14^A0N,40,36^FD{ubic}^FS",
        f"^FO440,62^A0N,40,36^FD{cam}^FS",
        f"^FO20,94^A0N,26,24^FD{cuarto}^FS",
        "^FO10,128^GB589,3,3^FS",
        # Bloque inferior
        f"^FO20,142^A0N,40,38^FD{puesto_turno}^FS",
        f"^FO20,190^BY3,3,100^BCN,100,N,N,N,A^FD{codigo}^FS",
        f"^FO20,300^A0N,58,54^FD{animal}^FS",
        f"^FO20,364^A0N,32,30^FD{tipo}^FS",
        f"^PQ{copias},0,1,Y",
        "^XZ",
    ])


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
