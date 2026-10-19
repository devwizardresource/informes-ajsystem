"""La plantilla del propietario como hoja de trabajo del informe.

Fuente única: ``resources/Plantilla_Informe_AJ_System.html`` (entregada por el propietario).
La aplicación NO usa una copia: lee ese archivo y, sin alterar su diseño,
- separa su hoja de estilos (se sirve como archivo propio: la CSP no admite estilos en línea),
- convierte cada celda de valor en el campo correspondiente del informe (por la etiqueta),
- rellena el número, el año, el registro de quien elabora y las firmas,
- quita su script en línea (las mismas funciones las da static/js/report.js).

Si el propietario cambia la plantilla (textos, colores, orden), la pantalla y el PDF la siguen.
Las etiquetas de la plantilla deben coincidir con las de report_templates/aj_v1.json; las filas
del JSON que no existen en la plantilla (p. ej. «Marca:») se insertan tras la fila indicada.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

from . import schema

SOURCE = Path(__file__).resolve().parent.parent.parent / "resources" / "Plantilla_Informe_AJ_System.html"
# Filas del formato que no están en la plantilla original: campo → etiqueta tras la que se insertan.
EXTRA_ROWS_AFTER = {"marca": "Dispositivo:"}

_ROW_RE = re.compile(r'<tr><td class="lbl">(?P<label>.*?)</td><td class="val">(?P<val>.*?)</td></tr>', re.S)
_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.S)
_MAIN_RE = re.compile(r'<main class="sheet">.*?</main>', re.S)
_LOGO_RE = re.compile(r'(<div class="logo-badge"><img src=")(data:image/[^"]+)(")')
_META_RE = re.compile(r'Informe N\.º <span contenteditable="true">.*?</span> de <span contenteditable="true">.*?</span>')
_REG_RE = re.compile(r'(<small>(?P<cap>[^<]+)</small>)<div contenteditable="true" data-ph="[^"]*"></div>')
_SIG_RE = re.compile(r'<div class="sig">.*?<input type="file" accept="image/\*" hidden></div>\s*</div>', re.S)
_CAP_RE = re.compile(r'<div class="sig-cap">(.*?)</div>')
SIG_KINDS = ("FIRMA_ELABORA", "FIRMA_RECIBE")


class SheetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    css: str
    css_hash: str
    main: str
    logo_data_uri: str


_cache: dict = {}


def source() -> Source:
    """Lee la plantilla (se recarga sola si el archivo cambia)."""
    try:
        stat = SOURCE.stat()
    except FileNotFoundError as exc:
        raise SheetError(f"No se encuentra la plantilla del informe: {SOURCE}") from exc
    key = (stat.st_mtime_ns, stat.st_size)
    if _cache.get("key") != key:
        text = SOURCE.read_text(encoding="utf-8")
        style, main, logo = _STYLE_RE.search(text), _MAIN_RE.search(text), _LOGO_RE.search(text)
        if not (style and main and logo):
            raise SheetError("La plantilla no tiene la estructura esperada (<style>, <main class=\"sheet\">, logo).")
        css = style.group(1)
        _cache.update(key=key, value=Source(css=css, css_hash=hashlib.sha256(css.encode()).hexdigest()[:12],
                                            main=main.group(0), logo_data_uri=logo.group(2)))
    return _cache["value"]


def _norm(label: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", label)).split()).lower()


def _attr(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _field_html(f: schema.Field, value, editable: bool, error: str | None, static: bool = False) -> str:
    v = "" if value is None else str(value)
    if f.widget == "radio" and static:  # PDF: casillas dibujadas como en la plantilla (✓ sobre violeta)
        opts = "".join(f'<label><span class="box{" on" if v == o else ""}">{"✓" if v == o else ""}</span> '
                       f'{html.escape(o)}</label>' for o in f.options)
        out = f'<div class="choices">{opts}</div>'
    elif f.widget == "radio":
        opts = "".join(
            f'<label><input type="radio" name="{f.id}" value="{_attr(o)}"{" checked" if v == o else ""}'
            f'{"" if editable else " disabled"}> {html.escape(o)}</label>' for o in f.options)
        out = f'<div class="choices">{opts}</div>'
    elif not editable:
        ph = f' data-ph="{_attr(f.placeholder)}"' if f.placeholder else ""
        out = f'<div contenteditable="false"{ph}>{html.escape(v)}</div>'
    elif f.type == "date":
        out = f'<input type="date" id="f-{f.id}" name="{f.id}" value="{_attr(v)}">'
    elif f.type == "textarea":
        ph = f' placeholder="{_attr(f.placeholder)}"' if f.placeholder else ""
        out = (f'<textarea id="f-{f.id}" name="{f.id}" rows="1" maxlength="{f.max_length}" data-autogrow{ph}>'
               f'{html.escape(v)}</textarea>')
    else:
        ph = f' placeholder="{_attr(f.placeholder)}"' if f.placeholder else ""
        out = f'<input type="text" id="f-{f.id}" name="{f.id}" maxlength="{f.max_length}" value="{_attr(v)}"{ph}>'
    if error:
        out += f'<p class="field-error" role="alert">{html.escape(error)}</p>'
    return out


def _row(f: schema.Field, value, editable: bool, error: str | None, static: bool = False) -> str:
    req = ' <span class="req" title="Obligatorio para cerrar">*</span>' if editable and f.required_on_close else ""
    label = f'<label for="f-{f.id}">{html.escape(f.label)}</label>' if editable and f.widget != "radio" \
        else html.escape(f.label)
    cls = "val has-error" if error else "val"
    return (f'<tr><td class="lbl">{label}{req}</td>'
            f'<td class="{cls}" data-field="{f.id}">{_field_html(f, value, editable, error, static)}</td></tr>')


def render(template: schema.Template, values: dict, *, editable: bool, errors: dict | None = None,
           number: str = "", year: str = "", registro: dict | None = None, signatures: dict | None = None,
           logo_src: str | None = None, static: bool = False) -> str:
    """HTML de la hoja (<main class="sheet">) con los datos del informe.

    signatures: {"FIRMA_ELABORA": url_o_data_uri, ...}; logo_src: URL del logo (None = el de la plantilla);
    static: True para el PDF (sin controles de formulario).
    """
    src = source()
    errors = errors or {}
    registro = registro or {}
    signatures = signatures or {}
    by_label = {_norm(f.label): f for f in template.fields}
    used: set[str] = set()

    def row_sub(m: re.Match) -> str:
        f = by_label.get(_norm(m.group("label")))
        if f is None:  # fila de la plantilla sin campo: se deja tal cual, vacía y sin edición
            return m.group(0).replace('contenteditable="true"', 'contenteditable="false"')
        used.add(f.id)
        out = _row(f, values.get(f.id), editable, errors.get(f.id), static)
        for extra_id, after in EXTRA_ROWS_AFTER.items():
            if _norm(after) == _norm(f.label):
                extra = template.field(extra_id)
                if extra is not None:
                    used.add(extra.id)
                    out += _row(extra, values.get(extra.id), editable, errors.get(extra.id), static)
        return out

    main = _ROW_RE.sub(row_sub, src.main)
    missing = [f.label for f in template.fields if f.id not in used]
    if missing:
        raise SheetError(f"Campos sin fila en la plantilla: {', '.join(missing)}")

    main = _META_RE.sub(
        f'Informe N.º <span contenteditable="false">{html.escape(number)}</span> de '
        f'<span contenteditable="false">{html.escape(year)}</span>', main)
    reg_values = {"usuario": registro.get("usuario"), "nombre completo": registro.get("nombre"),
                  "n.º de identificación": registro.get("identificacion")}
    main = _REG_RE.sub(lambda m: f'{m.group(1)}<div contenteditable="false">'
                                 f'{html.escape(reg_values.get(_norm(m.group("cap"))) or "")}</div>', main)

    kinds = iter(SIG_KINDS)

    def sig_sub(m: re.Match) -> str:
        kind = next(kinds, None)
        cap = _CAP_RE.search(m.group(0))
        caption = cap.group(1) if cap else ""
        url = signatures.get(kind)
        img = f'<img alt="{_attr(caption)}" src="{_attr(url)}">' if url else '<img alt="" hidden>'
        if not editable:
            return (f'<div class="sig readonly{" has" if url else ""}" data-kind="{kind}"><div class="pad">{img}</div>'
                    f'<div class="sig-cap">{caption}</div></div>')
        return (f'<div class="sig{" has" if url else ""}" data-kind="{kind}"><div class="pad"><canvas></canvas>{img}'
                f'<span class="ph">Firme aquí o cargue una imagen</span></div><div class="sig-cap">{caption}</div>'
                '<div class="tools"><button type="button" data-act="save">Guardar firma</button>'
                '<button type="button" data-act="load">Cargar imagen</button>'
                '<button type="button" data-act="clear">Borrar</button>'
                '<input type="file" accept="image/*" hidden><span class="status" aria-live="polite"></span></div></div>')

    main = _SIG_RE.sub(sig_sub, main)
    if logo_src:
        main = main.replace(src.logo_data_uri, _attr(logo_src), 1)
    if 'contenteditable="true"' in main:
        main = main.replace('contenteditable="true"', 'contenteditable="false"')
    return main


def css() -> str:
    """Hoja de estilos de la plantilla, tal cual."""
    return source().css


def check_matches(template: schema.Template) -> None:
    """Falla si la plantilla y el JSON de campos dejan de coincidir (se usa al arrancar y en pruebas)."""
    render(template, schema.empty_data(template), editable=True)
