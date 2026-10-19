"""Datos del cliente observados por el servidor: IP y sistema operativo reportado.

- La IP es request.remote_addr. Solo se confía en X-Forwarded-For si AJS_TRUST_PROXY_HOPS > 0
  (ProxyFix se activa en create_app con ese número exacto de saltos).
- El sistema operativo se deduce del User-Agent / Sec-CH-UA-Platform, que el cliente puede falsear:
  se registra como "reportado", nunca como un hecho verificado. No se hace geolocalización.
"""
from __future__ import annotations

import re

from flask import request

_OS_PATTERNS = [
    (re.compile(r"Windows NT 10\.0"), "Windows 10/11"),
    (re.compile(r"Windows NT 6\.3"), "Windows 8.1"),
    (re.compile(r"Windows NT 6\.1"), "Windows 7"),
    (re.compile(r"Windows"), "Windows"),
    (re.compile(r"Android"), "Android"),
    (re.compile(r"iPhone|iPod"), "iOS"),
    (re.compile(r"iPad"), "iPadOS"),
    (re.compile(r"CrOS"), "ChromeOS"),
    (re.compile(r"Mac OS X|Macintosh"), "macOS"),
    (re.compile(r"Ubuntu"), "Linux (Ubuntu)"),
    (re.compile(r"Linux"), "Linux"),
]


def parse_os(user_agent: str, platform_hint: str | None = None) -> str:
    if platform_hint:
        hint = platform_hint.strip().strip('"')[:40]
        if re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", hint):
            return hint
    for pattern, name in _OS_PATTERNS:
        if pattern.search(user_agent or ""):
            return name
    return "Desconocido"


def current_client() -> dict:
    ua = (request.headers.get("User-Agent") or "")[:512]
    return {
        "ip": (request.remote_addr or "desconocida")[:45],
        "user_agent": ua,
        "os_reported": parse_os(ua, request.headers.get("Sec-CH-UA-Platform")),
    }
