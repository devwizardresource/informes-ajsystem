"""Métricas de solo lectura: panel de actividad del técnico y tablero ejecutivo del Director.

Todo se calcula con los datos guardados (nunca se modifican) y respeta la eliminación lógica:
los informes eliminados no cuentan. Se distingue un cero real (hubo periodo y no hubo informes)
de un dato faltante (el campo no se diligenció o no hay base para calcular el promedio): este
último se devuelve como None y la interfaz lo muestra como «Sin datos» o «Sin dato».
"""
from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

from . import db
from .reports.service import CERRADO, CLOSED, EN_ELABORACION, ENVIADO, REABIERTO, STATUS_LABELS

MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
BAD_CONDITION = ("En reparación", "Obsoleto")  # opciones de «Estado» que indican mal estado
CONDITIONS = ("Activo", "En reparación", "Obsoleto")
MAX_RANGE_DAYS = 731
MAX_ROWS = 50000  # tope de seguridad: con más informes en el rango se avisa en pantalla


def _utc_bound(day: dt.date, tz: ZoneInfo) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc).replace(tzinfo=None)


def _local_day(value: dt.datetime, tz: ZoneInfo) -> dt.date:
    return value.replace(tzinfo=dt.timezone.utc).astimezone(tz).date()


def local_today(tz_name: str) -> dt.date:
    return dt.datetime.now(ZoneInfo(tz_name)).date()


def day_label(day: dt.date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def level(n: int, top: int) -> int:
    """Intensidad 0–4 (0 = sin informes), proporcional al día con más informes."""
    if n <= 0 or top <= 0:
        return 0
    return max(1, min(4, math.ceil(4 * n / top)))


def _streaks(days: set[dt.date], today: dt.date) -> tuple[int, int]:
    longest = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and (d - prev).days == 1 else 1
        longest = max(longest, run)
        prev = d
    current = 0
    d = today if today in days else today - dt.timedelta(days=1)
    while d in days:
        current += 1
        d -= dt.timedelta(days=1)
    return current, longest


# ---------------------------------------------------------------------------
# Panel de actividad (Inicio del técnico)
# ---------------------------------------------------------------------------
def activity_calendar(owner_id: int, tz_name: str, today: dt.date | None = None, weeks: int = 53) -> dict:
    """Informes creados por el usuario cada día (calendario de 53 semanas, de lunes a domingo)."""
    tz = ZoneInfo(tz_name)
    today = today or local_today(tz_name)
    start = today - dt.timedelta(days=today.weekday()) - dt.timedelta(weeks=weeks - 1)
    rows = db.query_all(
        """SELECT created_at FROM reports
            WHERE owner_id = %s AND deleted_at IS NULL AND created_at >= %s AND created_at < %s""",
        (owner_id, _utc_bound(start, tz), _utc_bound(today + dt.timedelta(days=1), tz)))
    counts = Counter(_local_day(r["created_at"], tz) for r in rows)
    top = max(counts.values(), default=0)
    cells, months, last_month = [], [], None
    day = start
    while day <= today:
        col, row = (day - start).days // 7, day.weekday()
        n = counts.get(day, 0)
        cells.append({"col": col, "row": row, "n": n, "level": level(n, top), "date": day.isoformat(),
                      "tip": f"{n} informe{'' if n == 1 else 's'} · {day_label(day)}"})
        if row == 0 and day.month != last_month:
            if not months or col - months[-1]["col"] >= 3:
                months.append({"col": col, "label": MONTHS[day.month - 1]})
            last_month = day.month
        day += dt.timedelta(days=1)
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0]), default=None)
    current, longest = _streaks(set(counts), today)
    return {
        "cells": cells, "months": months, "weeks": weeks, "total": sum(counts.values()),
        "active_days": len(counts), "best": {"n": best[1], "label": day_label(best[0])} if best else None,
        "current_streak": current, "longest_streak": longest,
        "start_label": day_label(start), "end_label": day_label(today),
    }


# ---------------------------------------------------------------------------
# Inicio del Director: informes cerrados pendientes de su firma
# ---------------------------------------------------------------------------
_PENDING_SIGNATURE = """
    FROM reports r JOIN users o ON o.id = r.owner_id
   WHERE r.deleted_at IS NULL AND r.status_code IN (%s, %s)
     AND NOT EXISTS (SELECT 1 FROM report_attachments a
                      WHERE a.report_id = r.id AND a.kind = 'FIRMA_RECIBE' AND a.deleted_at IS NULL)"""


def pending_signature(limit: int = 15) -> tuple[list[dict], int]:
    total = db.query_one("SELECT COUNT(*) AS n" + _PENDING_SIGNATURE, (CERRADO, ENVIADO))["n"]
    rows = db.query_all(
        """SELECT r.id, r.code, r.title, r.equipment_serial, r.equipment_brand, r.status_code, r.closed_at,
                  r.reopen_count, o.full_name AS owner_name""" + _PENDING_SIGNATURE +
        " ORDER BY r.closed_at DESC, r.id DESC LIMIT %s", (CERRADO, ENVIADO, limit))
    return rows, total


# ---------------------------------------------------------------------------
# Tablero ejecutivo
# ---------------------------------------------------------------------------
def _json_text(value) -> str | None:
    """Texto de un campo JSON extraído en SQL; None si falta, es nulo o está vacío."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text in ("", "null") else text


def _flag(value) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return True if text in ("true", "1") else False if text in ("false", "0") else None


def _ratio(num, den):
    return None if not den else num / den


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _equipment_key(row: dict) -> str:
    serial = (row["equipment_serial"] or "").strip().upper()
    if not serial:
        return f"informe:{row['id']}"  # sin número de serie no se puede saber si es el mismo equipo
    return f"{(row['equipment_brand'] or '').strip().upper()}|{serial}"


def _distribution(values: list[str | None], top: int | None = None) -> list[dict]:
    counts = Counter(v for v in values if v is not None)
    missing = sum(1 for v in values if v is None)
    items = counts.most_common()
    if top is not None and len(items) > top:
        rest = sum(n for _label, n in items[top:])
        items = items[:top] + [("Otros", rest)]
    out = [{"label": label, "n": n, "missing": False} for label, n in items]
    if missing:
        out.append({"label": "Sin dato", "n": missing, "missing": True})
    total = len(values)
    for item in out:
        item["pct"] = _ratio(item["n"], total)
    return out


def executive(date_from: dt.date, date_to: dt.date, owner_id: int | None, tz_name: str) -> dict:
    """Métricas del tablero para informes creados entre date_from y date_to (fechas locales, inclusive)."""
    tz = ZoneInfo(tz_name)
    where = ["r.deleted_at IS NULL", "r.created_at >= %s", "r.created_at < %s"]
    args: list = [_utc_bound(date_from, tz), _utc_bound(date_to + dt.timedelta(days=1), tz)]
    if owner_id:
        where.append("r.owner_id = %s")
        args.append(owner_id)
    rows = db.query_all(
        f"""SELECT r.id, r.code, r.title, r.owner_id, o.full_name AS owner_name, r.status_code, r.created_at,
                   r.closed_at, r.equipment_brand, r.equipment_serial,
                   JSON_UNQUOTE(JSON_EXTRACT(r.data, '$.estado')) AS estado,
                   JSON_UNQUOTE(JSON_EXTRACT(r.data, '$.dispositivo')) AS dispositivo,
                   JSON_UNQUOTE(JSON_EXTRACT(r.data, '$.observaciones')) AS observaciones,
                   JSON_EXTRACT(r.data, '$.observaciones_habilitadas') AS obs_on,
                   (SELECT COUNT(*) FROM report_attachments a
                     WHERE a.report_id = r.id AND a.kind = 'FOTO' AND a.deleted_at IS NULL) AS fotos,
                   EXISTS (SELECT 1 FROM report_attachments a
                            WHERE a.report_id = r.id AND a.kind = 'FIRMA_RECIBE' AND a.deleted_at IS NULL) AS firmado
              FROM reports r JOIN users o ON o.id = r.owner_id
             WHERE {' AND '.join(where)}
             ORDER BY r.created_at, r.id LIMIT %s""", (*args, MAX_ROWS + 1))
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    for r in rows:
        r["day"] = _local_day(r["created_at"], tz)
        r["estado"] = _json_text(r["estado"])
        r["dispositivo"] = _json_text(r["dispositivo"])
        obs = _json_text(r["observaciones"])
        r["has_obs"] = bool(obs) and _flag(r["obs_on"]) is not False
        r["observaciones"] = obs if r["has_obs"] else None
        r["fotos"] = int(r["fotos"] or 0)
        r["firmado"] = bool(r["firmado"])
        r["close_hours"] = ((r["closed_at"] - r["created_at"]).total_seconds() / 3600
                            if r["closed_at"] and r["status_code"] in CLOSED else None)

    days = [date_from + dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    n_days = len(days)
    per_day = Counter(r["day"] for r in rows)
    daily = []
    for i, d in enumerate(days):
        window = [per_day.get(days[j], 0) for j in range(max(0, i - 6), i + 1)]
        daily.append({"date": d.isoformat(), "label": day_label(d), "n": per_day.get(d, 0),
                      "avg7": sum(window) / len(window)})

    # Técnicos: todos los técnicos activos (un cero es real) más cualquier autor con informes en el rango.
    tech_rows = db.query_all(
        """SELECT u.id, u.full_name, u.is_active FROM users u JOIN roles ro ON ro.id = u.role_id
            WHERE ro.code = 'TECNICO' AND u.is_active = 1 ORDER BY u.full_name""")
    names = {t["id"]: t["full_name"] for t in tech_rows}
    names.update({r["owner_id"]: r["owner_name"] for r in rows})
    if owner_id:
        names = {k: v for k, v in names.items() if k == owner_id}
        if owner_id not in names:
            user = db.query_one("SELECT full_name FROM users WHERE id = %s", (owner_id,))
            if user:
                names[owner_id] = user["full_name"]
    by_owner = defaultdict(list)
    for r in rows:
        by_owner[r["owner_id"]].append(r)
    technicians = []
    for uid, name in names.items():
        own = by_owner.get(uid, [])
        own_days = Counter(r["day"] for r in own)
        technicians.append({
            "id": uid, "name": name, "n": len(own),
            "closed": sum(1 for r in own if r["status_code"] in CLOSED),
            "signed": sum(1 for r in own if r["status_code"] in CLOSED and r["firmado"]),
            "active_days": len(own_days), "per_day": _ratio(len(own), n_days),
            "per_active_day": _ratio(len(own), len(own_days)),
            "photos_pct": _ratio(sum(1 for r in own if r["fotos"]), len(own)),
            "obs": sum(1 for r in own if r["has_obs"]),
            "bad": sum(1 for r in own if r["estado"] in BAD_CONDITION),
            "close_hours": _mean([r["close_hours"] for r in own]),
            "last": max((r["created_at"] for r in own), default=None),
            "by_day": [own_days.get(d, 0) for d in days],
        })
    technicians.sort(key=lambda t: (-t["n"], -t["closed"], t["name"].lower()))
    for i, t in enumerate(technicians, 1):
        t["rank"] = i
    top_day_tech = max((n for t in technicians for n in t["by_day"]), default=0)
    for t in technicians:
        t["cells"] = [{"n": n, "level": level(n, top_day_tech), "tip": f"{t['name']} · {n} informe"
                       f"{'' if n == 1 else 's'} · {days[i].strftime('%d/%m/%Y')}"} for i, n in enumerate(t["by_day"])]

    # Inventario: un equipo (marca + serie) cuenta una vez, con su informe más reciente del rango.
    latest: dict[str, dict] = {}
    for r in rows:
        latest[_equipment_key(r)] = r  # rows viene en orden cronológico
    equipment = list(latest.values())
    no_serial = sum(1 for k in latest if k.startswith("informe:"))

    statuses = Counter(r["status_code"] for r in rows)
    statuses[CERRADO] += statuses.pop(ENVIADO, 0)  # los informes «enviados» antiguos cuentan como cerrados
    status_dist = [{"code": code, "label": STATUS_LABELS[code], "n": statuses.get(code, 0),
                    "pct": _ratio(statuses.get(code, 0), len(rows))}
                   for code in (EN_ELABORACION, REABIERTO, CERRADO)]
    closed_rows = [r for r in rows if r["status_code"] in CLOSED]
    active_techs = [t for t in technicians if t["n"]]

    def eq_list(pred, limit=100):
        items = sorted((e for e in equipment if pred(e)), key=lambda e: e["created_at"], reverse=True)
        return {"total": len(items), "rows": items[:limit]}

    return {
        "rows_total": len(rows), "truncated": truncated, "days": n_days,
        "date_from": date_from, "date_to": date_to,
        "kpi": {
            "reports": len(rows),
            "equipment": len(equipment),
            "duplicates": len(rows) - len(equipment),
            "no_serial": no_serial,
            "techs_active": len(active_techs),
            "techs_total": len(technicians),
            "per_day": _ratio(len(rows), n_days),
            "per_tech": _ratio(len(rows), len(active_techs)),
            "closed": len(closed_rows),
            "closed_pct": _ratio(len(closed_rows), len(rows)),
            "pending_signature": sum(1 for r in closed_rows if not r["firmado"]),
            "close_hours": _mean([r["close_hours"] for r in rows]),
            "with_photos": sum(1 for e in equipment if e["fotos"]),
            "with_photos_pct": _ratio(sum(1 for e in equipment if e["fotos"]), len(equipment)),
            "photos_total": sum(r["fotos"] for r in rows),
            "with_obs": sum(1 for e in equipment if e["has_obs"]),
            "bad": sum(1 for e in equipment if e["estado"] in BAD_CONDITION),
            "condition_missing": sum(1 for e in equipment if e["estado"] is None),
            "peak": max(daily, key=lambda d: d["n"]) if rows else None,
        },
        "daily": daily,
        "daily_max": max((d["n"] for d in daily), default=0),
        "technicians": technicians,
        "status": status_dist,
        "condition": _distribution([e["estado"] for e in equipment]),
        "devices": _distribution([e["dispositivo"] for e in equipment], top=8),
        "brands": _distribution([(e["equipment_brand"] or "").strip() or None for e in equipment], top=8),
        "with_obs": eq_list(lambda e: e["has_obs"]),
        "bad": eq_list(lambda e: e["estado"] in BAD_CONDITION),
        "with_photos": eq_list(lambda e: e["fotos"] > 0),
        "weekday": [{"label": WEEKDAYS[i], "n": sum(1 for r in rows if r["day"].weekday() == i)} for i in range(7)],
    }
