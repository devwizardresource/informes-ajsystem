"""Autenticación, sesiones, CSRF y cabeceras de seguridad."""
import datetime as dt

from conftest import Browser


def last_event(q, event_type, username):
    rows = q("SELECT * FROM audit_log WHERE event_type = %s AND username = %s ORDER BY seq DESC LIMIT 1",
             (event_type, username))
    return rows[0] if rows else None


def test_login_correcto_registra_ip_y_so(browser, make_user, q):
    u = make_user("TECNICO")
    b = browser()
    r = b.login(u)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    home = b.get("/")
    assert home.status_code == 200 and f"Usuario {u['username']}" in home.get_data(as_text=True)
    ev = last_event(q, "LOGIN_EXITOSO", u["username"])
    assert ev and ev["outcome"] == "EXITO" and ev["user_id"] == u["id"] and ev["role_code"] == "TECNICO"
    assert ev["ip"] == "203.0.113.10" and ev["os_reported"] == "Windows 10/11" and ev["session_public_id"]


def test_contrasena_incorrecta(browser, make_user, q):
    u = make_user("TECNICO")
    b = browser()
    r = b.login(u, password="incorrecta-123456")
    assert r.status_code == 401
    assert "Usuario o contraseña incorrectos" in r.get_data(as_text=True)
    ev = last_event(q, "LOGIN_FALLIDO", u["username"])
    assert ev and ev["outcome"] == "FALLO"
    assert b.get("/").status_code == 302  # sigue sin sesión


def test_usuario_inexistente_mismo_mensaje(browser, q):
    b = browser()
    r = b.login({"username": "nadie_existe", "password": "x" * 14})
    assert r.status_code == 401 and "Usuario o contraseña incorrectos" in r.get_data(as_text=True)
    assert last_event(q, "LOGIN_FALLIDO", "nadie_existe")


def test_limite_de_intentos(browser, make_user, q):
    u = make_user("TECNICO")
    b = browser()
    for _ in range(5):
        assert b.login(u, password="incorrecta-123456").status_code == 401
    r = b.login(u)  # incluso con la contraseña correcta queda bloqueado temporalmente
    assert r.status_code == 429
    assert last_event(q, "LOGIN_BLOQUEADO", u["username"])


def test_cierre_de_sesion(browser, make_user, q):
    u = make_user("JEFE")
    b = browser(u)
    r = b.post("/logout")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert b.get("/").status_code == 302
    ev = last_event(q, "LOGOUT", u["username"])
    assert ev and ev["session_public_id"]
    s = q("SELECT end_reason FROM sessions WHERE public_id = %s", (ev["session_public_id"],))[0]
    assert s["end_reason"] == "LOGOUT"


def test_sesion_expirada_por_inactividad(browser, make_user, q, root_conn_factory):
    u = make_user("TECNICO")
    b = browser(u)
    conn = root_conn_factory()
    with conn.cursor() as cur:
        cur.execute("UPDATE sessions SET last_seen_at = last_seen_at - INTERVAL 2 HOUR WHERE user_id = %s AND ended_at IS NULL",
                    (u["id"],))
    conn.close()
    r = b.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    ev = last_event(q, "SESION_EXPIRADA", u["username"])
    assert ev and "inactividad" in ev["detail"]


def test_sesion_expirada_absoluta(browser, make_user, q, root_conn_factory):
    u = make_user("TECNICO")
    b = browser(u)
    conn = root_conn_factory()
    with conn.cursor() as cur:
        cur.execute("UPDATE sessions SET absolute_expires_at = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND "
                    "WHERE user_id = %s AND ended_at IS NULL", (u["id"],))
    conn.close()
    assert b.get("/").status_code == 302
    assert "caducidad absoluta" in last_event(q, "SESION_EXPIRADA", u["username"])["detail"]


def test_rutas_protegidas_sin_sesion(app):
    c = app.test_client()
    for path in ("/", "/informes", "/consolidacion", "/registro", "/administracion/usuarios", "/informes/1", "/informes/1/pdf"):
        r = c.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    assert c.post("/informes/nuevo").status_code in (400, 401)


def test_csrf_obligatorio(browser, make_user, q):
    u = make_user("TECNICO")
    b = browser(u)
    r = b.client.post("/informes/nuevo", data={})
    assert r.status_code == 400
    r = b.client.post("/informes/nuevo", data={"csrf_token": "0" * 64})
    assert r.status_code == 400
    assert q("SELECT COUNT(*) AS n FROM reports WHERE owner_id = %s", (u["id"],))[0]["n"] == 0
    assert last_event(q, "CSRF_INVALIDO", u["username"])


def test_origen_externo_rechazado(browser, make_user):
    u = make_user("TECNICO")
    b = browser(u)
    token = b.csrf("/")
    r = b.client.post("/informes/nuevo", data={"csrf_token": token}, headers={"Origin": "https://atacante.example"})
    assert r.status_code == 400


def test_fijacion_de_sesion(app, make_user, q):
    """La cookie de sesión que el navegador traía antes del login no se reutiliza."""
    u = make_user("TECNICO")
    b = Browser(app)
    cookie_name = app.config["AJS_SETTINGS"].cookie_name
    b.client.set_cookie(cookie_name, "token-impuesto-por-atacante")
    r = b.login(u)
    new_cookie = [h for h in r.headers.getlist("Set-Cookie") if h.startswith(cookie_name + "=")][0]
    assert "token-impuesto-por-atacante" not in new_cookie
    assert "HttpOnly" in new_cookie and "SameSite=Lax" in new_cookie


def test_cambio_de_contrasena_obligatorio(browser, make_user):
    u = make_user("TECNICO", must_change=True)
    b = browser(u)
    r = b.get("/")
    assert r.status_code == 302 and "/cuenta/contrasena" in r.headers["Location"]
    r = b.post("/cuenta/contrasena", {"current": u["password"], "new": "Nueva-clave-segura-1", "confirm": "Nueva-clave-segura-1"},
               csrf_from="/cuenta/contrasena")
    assert r.status_code == 302
    assert b.get("/").status_code == 200


def test_cabeceras_de_seguridad(browser, make_user):
    b = browser(make_user("TECNICO"))
    r = b.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_errores_no_exponen_detalles(browser, make_user):
    b = browser(make_user("JEFE"))
    r = b.get("/ruta/inexistente")
    body = r.get_data(as_text=True)
    assert r.status_code == 404 and "Traceback" not in body and "pymysql" not in body.lower()


def test_contrasena_no_queda_en_auditoria(q, make_user, browser):
    u = make_user("TECNICO")
    browser().login(u, password="Secreto-Que-No-Debe-Aparecer")
    browser(u)
    rows = q("SELECT COUNT(*) AS n FROM audit_log WHERE CAST(detail AS CHAR) LIKE %s OR user_agent LIKE %s",
             ("%Secreto-Que-No%", "%Secreto-Que-No%"))
    assert rows[0]["n"] == 0
    rows = q("SELECT COUNT(*) AS n FROM audit_log WHERE CAST(detail AS CHAR) LIKE %s", (f"%{u['password']}%",))
    assert rows[0]["n"] == 0


def test_tiempos_utc(q, make_user, browser):
    u = make_user("TECNICO")
    browser(u)
    ev = last_event(q, "LOGIN_EXITOSO", u["username"])
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    assert abs((now - ev["occurred_at"]).total_seconds()) < 120


def test_sin_redireccion_abierta_tras_login(browser, make_user):
    u = make_user("TECNICO")
    b = browser()
    token = b.csrf("/login")
    for target in ("https://atacante.example/", "//atacante.example/x", "/\\atacante.example"):
        r = b.client.post("/login", data={"username": u["username"], "password": u["password"],
                                          "csrf_token": token, "next": target})
        assert r.status_code == 302 and "atacante" not in r.headers["Location"], target
        b.post("/logout")
        token = b.csrf("/login")
