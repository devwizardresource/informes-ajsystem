"""Dictamen de auditoría de un equipo: lee lo que el técnico registró y concluye si puede operar.

Es una regla determinista y de solo lectura: se recalcula sobre el informe vigente, no guarda nada
y no inventa hallazgos. Cada conclusión se sustenta en el texto del informe (que se cita) o en el
campo Estado. Sin evidencia técnica no hay dictamen favorable.

  OPTIMO         Activo y sin hallazgos funcionales (lo estético se registra, pero no descalifica).
  OBSERVACIONES  Funciona, con una falla que conviene atender (batería, bisagra, teclado, puertos…).
  NO_APTO        Daño que impide el uso (no enciende, pantalla rota, disco dañado…) o Estado
                 «En reparación» / «Obsoleto».
  SIN_EVIDENCIA  Informe cerrado sin Estado o sin ningún dato técnico: no se puede concluir.
"""
from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

OPTIMO, OBSERVACIONES, NO_APTO, SIN_EVIDENCIA = "OPTIMO", "OBSERVACIONES", "NO_APTO", "SIN_EVIDENCIA"
EN_AUDITORIA, PENDIENTE, SIN_SERIAL = "EN_AUDITORIA", "PENDIENTE", "SIN_SERIAL"
LABELS = {OPTIMO: "Óptimo", OBSERVACIONES: "Funcional con observaciones", NO_APTO: "No apto",
          SIN_EVIDENCIA: "Sin evidencia técnica", EN_AUDITORIA: "En auditoría", PENDIENTE: "Sin auditar",
          SIN_SERIAL: "Sin serial en la matriz"}
ISSUED = (OPTIMO, OBSERVACIONES, NO_APTO, SIN_EVIDENCIA)  # con informe cerrado

CRITICO, MODERADO, ESTETICO = 3, 2, 1
SEVERITY_LABELS = {CRITICO: "Crítico", MODERADO: "Moderado", ESTETICO: "Estético"}

FIELDS = (("disco", "Almacenamiento"), ("ram", "Memoria RAM"), ("procesadores", "Procesador"),
          ("fuente_poder", "Energía"), ("observaciones", "General"))
MAX_EXCERPT = 180

_NEAR = r"\W+(?:\w+\W+){0,3}"
_BROKEN = r"(?:danad\w+|rot[oa]s?|quebrad\w+|partid\w+|fracturad\w+|defectuos\w+|averiad\w+|malogrado\w*)"
_FAILING = rf"(?:{_BROKEN}|fall\w+|no funciona\w*|no sirve\w*|no responde\w*|no (?:se )?detect\w+|no (?:se )?reconoc\w+)"
_HOUSING = r"(?:carcasa|chasis|tapa|base|marco)"
_PERIPHERAL = (r"(?:teclado|teclas?|touch ?pad|panel tactil|mouse|puertos?|usb|hdmi|ethernet|rj45|wi ?fi|bluetooth|"
               r"camara|webcam|microfono|parlantes?|altavoz|altavoces|audio|sonido)")


class Rule(NamedTuple):
    severity: int
    component: str
    label: str
    pattern: re.Pattern
    unless: re.Pattern | None = None


def _rule(severity: int, component: str, label: str, pattern: str, unless: str | None = None) -> Rule:
    return Rule(severity, component, label, re.compile(pattern), re.compile(unless) if unless else None)


RULES = (
    _rule(CRITICO, "Encendido", "No enciende o no da imagen",
          r"\bno (?:enciende|encendio|prende|prendio|arranca|arranco|inicia|bootea|da video|da imagen|muestra imagen|da senal)\b"
          r"|\bno (?:se )?(?:pudo|puede|logro) (?:encender|prender|iniciar|arrancar)\b|\bsin (?:video|imagen)\b"),
    _rule(CRITICO, "Pantalla", "Pantalla dañada",
          rf"\b(?:pantalla|display|lcd){_NEAR}(?:{_BROKEN}|estallad\w+|fisurad\w+|no funciona\w*|negra|en negro)\b"
          r"|\b(?:rotura|quiebre|fractura|fisura) de (?:la )?(?:pantalla|display)\b"),
    _rule(CRITICO, "Almacenamiento", "Disco con falla",
          rf"\b(?:disco|ssd|hdd|nvme|m\.?2){_NEAR}{_FAILING}\b|\bsectores (?:danados|defectuosos)\b"
          r"|\bno (?:detecta|reconoce) (?:el |la )?(?:disco|ssd|hdd|unidad)\b|\bsin (?:disco|ssd|hdd)\b"
          r"|\bno (?:tiene|trae) (?:disco|ssd|hdd)\b"),
    _rule(CRITICO, "Memoria RAM", "Memoria RAM con falla",
          rf"\b(?:memoria|memorias|ram){_NEAR}{_FAILING}\b|\bno (?:detecta|reconoce) (?:la )?(?:memoria|ram)\b"
          r"|\bsin (?:memoria|ram)\b|\bno (?:tiene|trae) (?:memoria|ram)\b"),
    _rule(CRITICO, "Tarjeta principal", "Tarjeta principal o procesador dañado",
          rf"\b(?:board|mainboard|motherboard|placa|tarjeta madre|tarjeta principal|procesador|cpu){_NEAR}{_FAILING}\b"),
    _rule(CRITICO, "Estabilidad", "Se apaga o reinicia solo",
          r"\bse (?:apaga|reinicia|bloquea|congela) (?:sol[oa]|constantemente|repentinamente)\b|\bpantallazos? azul\w*\b|\bbsod\b"),
    _rule(CRITICO, "Daño por líquido", "Daño por líquido",
          r"\bdanos? por (?:liquido|humedad|agua)\b|\bderrame\b|\bmojad[oa]\b|\bsulfatad\w+\b"),
    _rule(CRITICO, "Batería", "Batería inflada (riesgo de seguridad)",
          rf"\bbateria{_NEAR}(?:hinchad\w+|inflad\w+|abombad\w+)\b"),
    _rule(CRITICO, "Energía", "No recibe carga",
          r"\bno (?:carga|recibe carga|toma carga)\b", r"\bbateria\b"),
    _rule(CRITICO, "Equipo completo", "Equipo inoperativo",
          rf"\b(?:equipo|portatil|computador|laptop|maquina|pc){_NEAR}(?:no funciona|no sirve|{_BROKEN})\b"
          r"|\b(?:irreparable|inservible|inoperativ\w+|inoperante|chatarra|perdida total)\b"
          r"|\bfuera de servicio\b|\bsin reparacion\b|\bdad[oa] de baja\b"),
    _rule(MODERADO, "Batería", "Batería deteriorada",
          rf"\bbateria{_NEAR}(?:{_FAILING}|agotad\w+|degradad\w+|mala|no (?:carga|retiene|dura|sostiene|mantiene)\w*)\b"
          r"|\bsin bateria\b|\bno (?:tiene|trae) bateria\b"),
    _rule(MODERADO, "Energía", "Cargador con falla o ausente",
          rf"\b(?:cargador|adaptador|fuente(?: de poder)?){_NEAR}{_FAILING}\b"
          r"|\b(?:sin|falta(?: de)?) (?:cargador|adaptador)\b|\bno (?:tiene|trae) (?:cargador|adaptador)\b"),
    _rule(MODERADO, "Estructura", "Bisagra dañada",
          rf"\bbisagras?{_NEAR}(?:{_BROKEN}|floj\w+|desprendid\w+|suelt\w+)\b"),
    _rule(MODERADO, "Estructura", "Carcasa rota",
          rf"\b{_HOUSING}{_NEAR}(?:{_BROKEN}|despegad\w+|desprendid\w+)\b"),
    _rule(MODERADO, "Periféricos integrados", "Teclado, puerto o componente integrado con falla",
          rf"\b{_PERIPHERAL}{_NEAR}(?:{_FAILING}|atascad\w+|faltantes?)\b|\b(?:faltan?|sin) (?:teclas?|touch ?pad)\b"),
    _rule(MODERADO, "Pantalla", "Pantalla con defecto visual",
          rf"\b(?:pantalla|display){_NEAR}(?:parpade\w+|lineas?|pixel\w*|manchas?)\b"
          r"|\b(?:lineas?|manchas?|pixel\w*) en (?:la )?(?:pantalla|display)\b|\bpixeles muertos\b"),
    _rule(MODERADO, "Rendimiento", "Sobrecalentamiento o lentitud",
          r"\bsobrecalent\w+\b|\bse calienta\b|\bmuy lent[oa]\b|\blentitud\b|\bventilador\w*\W+(?:\w+\W+){0,3}(?:ruidos\w+|danad\w+|no funciona\w*)\b"),
    _rule(MODERADO, "Mantenimiento", "Reparación o reemplazo pendiente",
          r"\b(?:requiere|necesita|pendiente|se recomienda|recomienda|solicitar)\b.{0,40}\b(?:cambio|cambiar|reemplazo|reemplazar|reparacion|reparar|mantenimiento correctivo)\b"),
    _rule(MODERADO, "Estructura", "Golpe fuerte",
          r"\bgolpes? (?:fuerte|grave|severo)s?\b"),
    _rule(ESTETICO, "Estética", "Desgaste estético",
          r"\b(?:rayon\w*|rayad\w+|rayas?|rallad\w+|rasgun\w*|raspon\w*|desgast\w+|golpes?|golpead\w+|abolladur\w+|"
          r"manchas?|manchad\w+|sucio\w*|sucia\w*|suciedad|polvo|stickers?|calcomanias?)\b"),
)

# Término de falla sin componente reconocido: se trata como hallazgo y nunca se ignora.
_GENERIC = re.compile(rf"\b(?:{_FAILING}|mal estado|mal[oa]s?)\b")
_GENERIC_SEVERITY = {"disco": CRITICO, "ram": CRITICO, "procesadores": CRITICO}
_NEGATION = re.compile(r"\b(?:sin|no|ni|ningun|ninguna|ninguno|libre de|descart\w+|ausencia de)\b")
_CONTRAST = re.compile(r"\b(?:pero|aunque|excepto|salvo|sin embargo|solo|solamente)\b")
_RESOLVED = re.compile(r"\bse (?:reemplazo|cambio|reparo|soluciono|corrigio|sustituyo)\b"
                       r"|\b(?:reemplazad|cambiad|reparad|solucionad|corregid|sustituid)[oa]s?\b")
_CLAUSE = re.compile(r"[.;:,\n\r]+")
_BLANK = frozenset({"", "na", "n a", "no aplica", "ninguna", "ninguno", "sin observaciones", "sin dato", "pendiente"})


def _norm(text: str) -> str:
    """Minúsculas y sin tildes ni eñes, para comparar lo que los técnicos escriben de muchas formas."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.lower().split())


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _negated(clause: str, start: int) -> bool:
    before = clause[:start]
    contrast = None
    for contrast in _CONTRAST.finditer(before):
        pass
    if contrast:
        before = before[contrast.end():]
    return bool(_NEGATION.search(" ".join(before.split()[-5:])))


def _excerpt(clause: str) -> str:
    return clause if len(clause) <= MAX_EXCERPT else clause[:MAX_EXCERPT - 1].rstrip() + "…"


def _clause_findings(raw: str, field: str, field_label: str) -> list[dict]:
    clause = _norm(raw)
    found, covered = [], False
    for rule in RULES:
        if rule.unless and rule.unless.search(clause):
            continue
        for match in rule.pattern.finditer(clause):
            if not _negated(clause, match.start()):
                found.append({"severity": rule.severity, "component": rule.component, "label": rule.label})
                covered = True
                break
    if not covered:
        for match in _GENERIC.finditer(clause):
            if not _negated(clause, match.start()):
                found.append({"severity": _GENERIC_SEVERITY.get(field, MODERADO), "component": field_label,
                              "label": f"Falla reportada: {field_label.lower()}"})
                break
    if found and _RESOLVED.search(clause):
        # Lo ya corregido por el técnico queda como antecedente, sin afectar la operación.
        found = [{"severity": ESTETICO, "component": f["component"], "label": f"{f['label']} (corregido)"} for f in found]
    for f in found:
        f.update(severity_label=SEVERITY_LABELS[f["severity"]], excerpt=_excerpt(raw.strip()))
    return found


def _state_finding(state: str) -> dict:
    label = "Equipo en reparación" if state == "En reparación" else "Equipo declarado obsoleto"
    return {"severity": CRITICO, "severity_label": SEVERITY_LABELS[CRITICO], "component": "Estado del equipo",
            "label": label, "excerpt": f"Estado: {state}"}


def assess(data: dict) -> dict:
    """Dictamen de un informe cerrado, con los datos ya adaptados a la plantilla oficial."""
    data = data or {}
    state = _text(data.get("estado"))
    findings, evidence = [], False
    for field, label in FIELDS:
        text = _text(data.get(field))
        if field == "observaciones" and data.get("observaciones_habilitadas") is False:
            continue  # el técnico las excluyó del informe: tampoco cuentan aquí
        if _norm(text) in _BLANK:
            continue
        evidence = True
        for clause in _CLAUSE.split(text):
            if clause.strip():
                findings.extend(_clause_findings(clause, field, label))
    if state in ("En reparación", "Obsoleto"):
        findings.append(_state_finding(state))
    findings.sort(key=lambda f: -f["severity"])
    worst = findings[0]["severity"] if findings else 0
    if worst == CRITICO:
        code = NO_APTO
    elif not evidence or state != "Activo":
        code = SIN_EVIDENCIA
    elif worst == MODERADO:
        code = OBSERVACIONES
    else:
        code = OPTIMO
    return {"code": code, "label": LABELS[code], "findings": findings, "worst": worst}


def status_only(code: str) -> dict:
    """Equipo sin dictamen: sin informe, sin serial o con el informe todavía abierto."""
    return {"code": code, "label": LABELS[code], "findings": [], "worst": 0}
