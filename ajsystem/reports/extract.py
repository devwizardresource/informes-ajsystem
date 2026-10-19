"""Autocompletado del informe a partir de texto reconocido (foto) o de una matriz Excel/CSV.

El reconocimiento óptico (OCR) se ejecuta en el navegador del técnico; aquí solo llega el texto.
Este módulo traduce ese texto, o una fila de Excel, a valores sugeridos para los campos de la
plantilla. Las sugerencias nunca se guardan solas: el técnico las revisa y pulsa «Guardar».

Reconoce las etiquetas habituales en español e inglés de:
- Configuración de Windows («Acerca de»), msinfo32, dxdiag y el Administrador de tareas;
- macOS («Acerca de este Mac»), herramientas Linux (dmidecode, lshw, hostnamectl);
- pantallas de BIOS/UEFI, CrystalDiskInfo/SMART y las etiquetas físicas del equipo (S/N, P/N, Model).
"""
from __future__ import annotations

import re
import unicodedata

from . import schema

# Etiquetas por campo. Se comparan sin tildes y en minúsculas; el orden importa (la primera gana).
LABELS: dict[str, tuple[str, ...]] = {
    "marca": ("fabricante del sistema", "system manufacturer", "fabricante", "manufacturer", "marca", "brand",
              "hardware vendor", "vendor", "proveedor", "make"),
    "modelo": ("modelo del sistema", "system model", "system product name", "product name", "nombre del producto",
               "hardware model", "model number", "model no", "modelo", "model", "mod", "producto"),
    "numero_serie": ("numero de serie del sistema", "system serial number", "numero de serie", "num de serie",
                     "n de serie", "serial number", "serial no", "serial", "service tag", "etiqueta de servicio",
                     "s/n", "sn", "no de serie", "nro de serie", "serie"),
    "nombre": ("nombre del dispositivo", "device name", "nombre del equipo", "nombre de equipo", "computer name",
               "nombre del sistema", "system name", "host name", "hostname", "static hostname", "equipo"),
    "procesadores": ("procesador", "processor", "cpu", "model name"),
    "ram": ("memoria ram instalada", "ram instalada", "installed ram", "memoria fisica instalada",
            "installed physical memory", "memoria fisica total", "total physical memory", "memoria ram", "memoria",
            "memory", "ram"),
    "disco": ("almacenamiento", "storage", "disco duro", "unidad de disco", "hard drive", "disk drive", "disco",
              "disk", "ssd", "hdd", "estado de salud", "health status", "vida util", "horas de encendido",
              "power on hours"),
    "temperatura": ("temperatura de la cpu", "cpu temperature", "temperatura", "temperature", "temp"),
    "ventiladores": ("velocidad del ventilador", "fan speed", "ventilador", "cpu fan", "fan"),
    "fuente_poder": ("fuente de poder", "fuente de alimentacion", "power supply", "psu", "adaptador de ca",
                     "ac adapter", "adaptador"),
    "parches_firmware": ("version del bios", "bios version", "version de bios", "bios/fecha", "bios", "firmware",
                         "uefi"),
    "dispositivo": ("tipo de dispositivo", "tipo de equipo", "chassis", "chasis", "tipo de sistema", "system type",
                    "tipo"),
    "ubicacion": ("ubicacion fisica", "ubicacion", "location"),
    "responsable": ("responsable asignado", "responsable", "usuario asignado", "asignado a", "assigned to",
                    "custodio", "owner"),
    "lugar": ("lugar donde se realizo el informe", "lugar", "sede", "ciudad", "site"),
    "direccion": ("direccion especifica", "direccion", "address"),
    "estado": ("estado del equipo", "estado", "status"),
    "fecha": ("fecha del informe", "fecha", "date"),
}

# Encabezados de Excel: además de las etiquetas anteriores, el texto exacto de la plantilla.
EXTRA_HEADERS: dict[str, tuple[str, ...]] = {
    "config_minimas": ("validacion de configuraciones minimas requeridas", "configuraciones minimas"),
    "politicas_reemplazo": ("politicas de reemplazo", "politica de reemplazo"),
    "normas_seguridad": ("normas de seguridad",),
    "normas_ambientales": ("normas medioambientales", "normas ambientales"),
    "proteccion_robos": ("medidas de proteccion contra robos", "proteccion contra robos"),
    "climatizacion": ("sistema de climatizacion", "climatizacion"),
    "control_acceso": ("control de acceso a los centros de datos", "control de acceso"),
    "contra_incendios": ("sistema contra incendios", "contra incendios"),
    "respaldo_electrico": ("respaldo electrico", "ups"),
    "disco": ("diagnostico de desgaste / vida util estimada del disco duro", "diagnostico del disco"),
    "ram": ("estado de memoria ram",),
    "temperatura": ("temperatura de operacion",),
}

BRANDS = ("Dell", "HP", "Hewlett-Packard", "Hewlett Packard Enterprise", "Lenovo", "ASUS", "Acer", "Apple", "Toshiba",
          "Samsung", "MSI", "Huawei", "Microsoft", "Sony", "Fujitsu", "Gigabyte", "Intel", "Supermicro", "Cisco",
          "Epson", "Brother", "Canon", "Xerox", "Kyocera", "Ricoh", "LG", "Compaq", "Alienware", "Razer", "Xiaomi",
          "Positivo", "Janus", "Panasonic", "Getac", "Zebra", "Ubiquiti", "Mikrotik", "TP-Link", "APC", "Eaton",
          "Vertiv", "Synology", "QNAP", "Western Digital", "Seagate", "Kingston", "Crucial", "Sandisk", "Hikvision")
_BRAND_ALIASES = {"hewlett-packard": "HP", "hewlett packard": "HP", "hp inc.": "HP", "hp inc": "HP",
                  "dell inc.": "Dell", "dell inc": "Dell", "lenovo group": "Lenovo", "asustek": "ASUS",
                  "asustek computer inc.": "ASUS", "micro-star": "MSI", "apple inc.": "Apple",
                  "microsoft corporation": "Microsoft", "acer inc.": "Acer", "toshiba corporation": "Toshiba"}

_DEVICE_HINTS = (
    ("Servidor", ("server", "servidor", "poweredge", "proliant", "primergy", "rack mount", "thinksystem")),
    ("Portátil", ("laptop", "notebook", "portatil", "portable", "thinkpad", "latitude", "elitebook", "probook",
                  "ideapad", "vivobook", "zenbook", "macbook", "inspiron 14", "inspiron 15", "chromebook", "tablet")),
    ("Computador todo en uno", ("all-in-one", "all in one", "todo en uno", "aio", "imac")),
    ("Computador de escritorio", ("desktop", "escritorio", "optiplex", "prodesk", "elitedesk", "thinkcentre",
                                  "vostro", "torre", "tower", "mini pc", "sff", "workstation", "precision")),
    ("Impresora", ("printer", "impresora", "laserjet", "deskjet", "ecotank", "officejet", "multifuncional")),
    ("Monitor", ("monitor", "display")),
    ("Switch de red", ("switch",)),
    ("Router", ("router", "enrutador")),
    ("UPS", ("ups", "uninterruptible", "sai")),
)

_SEPARATORS = r"[:=\-–—|>.]*"
_TEMP_RE = re.compile(r"(-?\d{1,3}(?:[.,]\d)?)\s*(?:°|º|o)?\s*([CF])\b", re.I)
_RPM_RE = re.compile(r"\b(\d{3,5})\s*rpm\b", re.I)
_RAM_RE = re.compile(r"\b(\d{1,4}(?:[.,]\d{1,2})?)\s*(GB|GiB|MB|TB)\b(?:\s*(?:RAM|DDR\d\w*|de RAM))?", re.I)
_CPU_RE = re.compile(r"((?:Intel|AMD|Apple|Qualcomm)[^\n]{3,90})", re.I)
_DISK_RE = re.compile(r"\b(\d{2,4}(?:[.,]\d)?)\s*(GB|TB)\b[^\n]{0,40}?\b(SSD|HDD|NVMe|SATA|M\.2|eMMC)\b", re.I)
_HEALTH_RE = re.compile(r"\b(?:salud|health)\b[^\n]{0,20}?(\d{1,3})\s*%", re.I)
_SERIAL_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9\-/]{3,40}$", re.I)


def _norm_map(raw: str) -> tuple[str, list[int]]:
    """Texto sin tildes, en minúsculas y con espacios simples, más la posición original de cada carácter."""
    out: list[str] = []
    pos: list[int] = []
    for i, ch in enumerate(raw):
        if ch in "º°ª":
            continue
        for c in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(c):
                continue
            c = " " if (c.isspace() or c == ".") else c.lower()
            if c == " " and (not out or out[-1] == " "):
                continue
            out.append(c)
            pos.append(i)
    while out and out[-1] == " ":
        out.pop()
        pos.pop()
    return "".join(out), pos


def _norm(text: str) -> str:
    return _norm_map(text)[0]


def _label_patterns() -> list[tuple[str, re.Pattern]]:
    pairs = [(field_id, _norm(label)) for field_id, labels in LABELS.items() for label in labels]
    # Las etiquetas más largas primero: «model name» (CPU en Linux) gana a «model».
    pairs.sort(key=lambda p: -len(p[1]))
    return [(field_id, re.compile(rf"^{re.escape(label)}(?![a-z0-9])\s*{_SEPARATORS}\s*(.*)$"))
            for field_id, label in pairs]


_PATTERNS = _label_patterns()


def _canonical_brand(value: str) -> str | None:
    low = value.lower().strip(" .,;")
    for alias, brand in _BRAND_ALIASES.items():
        if low.startswith(alias):
            return brand
    for brand in BRANDS:
        if re.search(rf"(?<![a-z0-9]){re.escape(brand.lower())}(?![a-z0-9])", low):
            return "HP" if brand.startswith("Hewlett") and "enterprise" not in brand.lower() else brand
    return None


def _guess_device(text: str) -> str | None:
    low = _norm(text)
    for label, hints in _DEVICE_HINTS:
        if any(re.search(rf"(?<![a-z0-9]){re.escape(h)}(?![a-z0-9])", low) for h in hints):
            return label
    return None


def _clean_line_value(value: str) -> str:
    value = value.strip().strip("|:;=-–—").strip()
    return re.sub(r"\s{2,}", " ", value)


def from_text(text: str, template: schema.Template) -> dict[str, str]:
    """Sugerencias {campo: valor} a partir de texto reconocido en una foto."""
    text = (text or "")[:20000]
    lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln]
    found: dict[str, str] = {}

    for i, raw in enumerate(lines):
        norm_line, pos = _norm_map(raw)
        for field_id, pat in _PATTERNS:
            if field_id in found:
                continue
            m = pat.match(norm_line)
            if not m:
                continue
            # El valor se toma del texto original (conserva mayúsculas y tildes).
            value = _clean_line_value(raw[pos[m.start(1)]:] if m.group(1) else "")
            if not value and i + 1 < len(lines):
                value = _clean_line_value(lines[i + 1])  # valor en la línea siguiente
            if value:
                found[field_id] = value
            break

    # Validaciones y deducciones a partir del texto completo.
    if "numero_serie" in found:
        token = found["numero_serie"].split()[0] if found["numero_serie"].split() else ""
        found["numero_serie"] = token if _SERIAL_TOKEN.match(token) else found["numero_serie"]
    brand_src = found.get("marca") or found.get("modelo") or ""
    # Sin etiqueta de marca se busca en el resto del texto, ignorando las líneas del procesador
    # (Intel/AMD fabrican la CPU, no el equipo).
    other_lines = "\n".join(ln for ln in lines if not re.search(r"cpu|process|procesador|xeon|core|ryzen", ln, re.I))
    brand = _canonical_brand(brand_src) or _canonical_brand(other_lines)
    if brand:
        found["marca"] = brand
    elif "marca" in found and len(found["marca"]) > 40:
        del found["marca"]
    if "procesadores" not in found or not re.search(r"\d", found.get("procesadores", "")):
        m = _CPU_RE.search(text)
        if m:
            found["procesadores"] = _clean_line_value(m.group(1))
    ram = found.get("ram", "")
    if not _RAM_RE.search(ram):
        found.pop("ram", None)
        m = re.search(r"(?:ram|memoria|memory)[^\n]{0,30}?" + _RAM_RE.pattern, text, re.I)
        if m:
            found["ram"] = f"{m.group(1)} {m.group(2).upper()} instalada"
    else:
        sizes = _RAM_RE.findall(ram)
        found["ram"] = f"{sizes[0][0]} {sizes[0][1].upper()} instalada"
        if len(sizes) > 1 and re.search(r"utiliz|usable", ram, re.I):
            found["ram"] += f" ({sizes[1][0]} {sizes[1][1].upper()} utilizable)"
    disk_bits = []
    m = _DISK_RE.search(text)
    if m:
        disk_bits.append(f"{m.group(1)} {m.group(2).upper()} {m.group(3).upper()}")
    h = _HEALTH_RE.search(text)
    if h:
        disk_bits.append(f"salud {h.group(1)} %")
    if disk_bits:
        found["disco"] = "; ".join(disk_bits)
    elif "disco" in found and not re.search(r"\d", found["disco"]):
        del found["disco"]
    if "temperatura" in found or re.search(r"temp", text, re.I):
        m = _TEMP_RE.search(found.get("temperatura", "")) or re.search(
            r"temp[^\n]{0,30}?" + _TEMP_RE.pattern, text, re.I)
        if m:
            found["temperatura"] = f"{m.group(1).replace(',', '.')} °{m.group(2).upper()}"
        else:
            found.pop("temperatura", None)
    if "ventiladores" in found or _RPM_RE.search(text):
        m = _RPM_RE.search(found.get("ventiladores", "")) or _RPM_RE.search(text)
        if m:
            found["ventiladores"] = f"Operativo · {m.group(1)} RPM"
        else:
            found.pop("ventiladores", None)
    if "parches_firmware" in found:
        found["parches_firmware"] = "BIOS/firmware " + found["parches_firmware"]
    device = _guess_device(found.get("dispositivo", "") + "\n" + found.get("modelo", "") + "\n" + text)
    if device:
        found["dispositivo"] = device
    else:
        found.pop("dispositivo", None)
    found.pop("estado", None)  # el estado lo decide el técnico
    found.pop("fecha", None)
    return _keep_valid(found, template)


def _keep_valid(found: dict, template: schema.Template) -> dict[str, str]:
    out = {}
    for field_id, value in found.items():
        field = template.field(field_id)
        if field is None:
            continue
        cleaned = schema.clean_value(field, value)
        if cleaned:
            out[field_id] = cleaned
    return out


# ---------------------------------------------------------------------------
# Excel / CSV
# ---------------------------------------------------------------------------
def header_map(headers: list, template: schema.Template) -> dict[int, str]:
    """Asocia cada columna con un campo de la plantilla según su encabezado."""
    by_label: dict[str, str] = {}
    for f in template.fields:
        by_label.setdefault(_norm(f.label.rstrip(":")), f.id)
        by_label.setdefault(_norm(f.id.replace("_", " ")), f.id)
    for source in (EXTRA_HEADERS, LABELS):
        for field_id, labels in source.items():
            for label in labels:
                by_label.setdefault(_norm(label), field_id)
    mapping: dict[int, str] = {}
    used = set()
    for idx, header in enumerate(headers):
        key = _norm(str(header or "")).rstrip(":").strip()
        field_id = by_label.get(key)
        if field_id and field_id not in used and template.field(field_id):
            mapping[idx] = field_id
            used.add(field_id)
    return mapping


def from_row(row: list, mapping: dict[int, str], template: schema.Template) -> dict[str, str]:
    found = {}
    for idx, field_id in mapping.items():
        if idx >= len(row):
            continue
        value = row[idx]
        if hasattr(value, "isoformat"):  # fechas de Excel
            value = value.isoformat()[:10]
        elif isinstance(value, float) and value.is_integer():
            value = str(int(value))
        found[field_id] = value
    if found.get("marca"):
        found["marca"] = _canonical_brand(str(found["marca"])) or found["marca"]
    return _keep_valid(found, template)
