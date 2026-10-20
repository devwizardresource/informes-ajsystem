"use strict";
/*
 * AJ System · punto de entrada para Hostinger (aplicación Node.js importada desde Git).
 *
 * La aplicación real es Flask (Python). Este proceso:
 *   1. escucha de inmediato en process.env.PORT (Hostinger lo exige) y muestra una página
 *      de «iniciando» mientras se prepara el backend;
 *   2. verifica la integridad de los recursos de terceros y prepara el entorno Python
 *      (hostinger/integrity.js y hostinger/python-env.js, dependencias verificadas por hash);
 *   3. aplica migraciones pendientes y crea el primer usuario si así se configura;
 *   4. arranca su propio gunicorn en una dirección interna (socket Unix por proceso) y le
 *      reenvía todo el tráfico.
 *
 * No tiene dependencias npm: solo módulos nativos de Node.
 */
const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { ensurePythonEnv, ROOT, log } = require("./hostinger/python-env");
const { verifyVendorIntegrity } = require("./hostinger/integrity");

const PORT = Number(process.env.PORT || 3000);
// Proxies de confianza delante de este proceso (el de Hostinger que termina TLS).
const EDGE_PROXY_HOPS = Math.max(0, Number(process.env.AJS_EDGE_PROXY_HOPS ?? 1));
const WORKERS = String(process.env.AJS_WORKERS || 2);
const THREADS = String(process.env.AJS_THREADS || 4);

// Hostinger puede tener varios procesos Node de la misma aplicación a la vez (y lanzar uno
// nuevo antes de que muera el anterior). Cada proceso tiene su propio gunicorn en su propia
// dirección interna: un socket compartido se lo quitaban unos a otros.
const SOCKET_DIR = path.join(ROOT, "tmp");
const SOCKET_PREFIX = "gunicorn-";

let backendPort = null;
let backendSocketPath = null;
let backendReady = false;
let startupError = null;
let shuttingDown = false;
let child = null;
let bindTargets = [];
let bindIndex = 0;
let backendGen = 0;
let lastBackendHint = null;
let failures = 0;
const bootStartedAt = Date.now();

// ---------------------------------------------------------------- utilidades

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function pythonEnv() {
  // Este proceso es siempre un proxy inverso delante de Flask: Flask confía en un salto.
  return { ...process.env, AJS_TRUST_PROXY_HOPS: process.env.AJS_TRUST_PROXY_HOPS || "1", PYTHONUNBUFFERED: "1" };
}

// Causa del fallo de arranque en lenguaje del operador. Solo nombra variables o pasos que
// revisar; nunca muestra valores (contraseñas, usuarios, rutas) del entorno.
const STARTUP_HINTS = [
  [/ConfigError: Configuración inválida: (.*)/, (m) => `Configuración inválida en las variables de entorno: ${m[1].replace(/[^\w\sáéíóúñÁÉÍÓÚÑ(),.;:=\-]/g, "")}.`],
  [/\(1045,/, () => "La base de datos rechazó el usuario o la contraseña. Revise AJS_DB_USER y AJS_DB_PASSWORD, y también AJS_DB_ADMIN_USER y AJS_DB_ADMIN_PASSWORD (use la misma cuenta de hPanel o déjelas vacías)."],
  [/\(1044,|\(1049,/, () => "La base de datos indicada no existe o el usuario no tiene acceso a ella. Revise AJS_DB_NAME (nombre completo de hPanel, con el prefijo u123_)."],
  [/\(2003,|\(2005,|\(2002,|\(2013,/, () => "No se pudo conectar con el servidor de base de datos. Revise AJS_DB_HOST (localhost en Hostinger) y AJS_DB_PORT (3306)."],
  [/\((1142|1227|1370|1419),/, () => "La cuenta de base de datos no tiene permiso para actualizar la estructura (migraciones). Use la cuenta de hPanel con todos los privilegios sobre la base en AJS_DB_USER/AJS_DB_PASSWORD o en AJS_DB_ADMIN_USER/AJS_DB_ADMIN_PASSWORD."],
  [/checksum distinto/, () => "Una migración ya aplicada en la base fue modificada en el repositorio. Restaure el archivo de migración original."],
  [/No se encontró Python/, () => "El servidor no tiene Python 3.10 o superior disponible."],
  [/pip install falló/, () => "No se pudieron instalar las dependencias de Python verificadas por hash."],
  [/SHA-256|integridad/i, () => "Un archivo de terceros no coincide con su suma de verificación."],
  [/ModuleNotFoundError: No module named '([\w.]+)'/, (m) => `Falta el módulo de Python '${m[1]}' en el entorno.`],
  [/Can't connect to|Connection in use/, () => "gunicorn no pudo abrir su dirección interna; se prueba la siguiente alternativa."],
  [/Worker failed to boot|App failed to load/, () => "La aplicación Python falla al cargar; el detalle está en las líneas «gunicorn:» del registro."],
  [/gunicorn no respondió/, () => "El servidor de la aplicación no respondió a tiempo tras iniciar."],
];

// Valores de variables sensibles que nunca deben llegar al registro.
const SECRET_VALUES = Object.entries(process.env)
  .filter(([k, v]) => /PASS|SECRET|TOKEN|KEY/i.test(k) && v && v.length >= 4)
  .map(([, v]) => v)
  .sort((a, b) => b.length - a.length);

function redact(text) {
  let out = String(text).replace(/(\/\/[^\s:/@]+:)[^\s@]+@/g, "$1***@");
  for (const v of SECRET_VALUES) out = out.split(v).join("***");
  return out;
}

function startupHint(text) {
  for (const [re, fmt] of STARTUP_HINTS) {
    const m = re.exec(text);
    if (m) return fmt(m);
  }
  return null;
}

// Cuenta para migrar: la propietaria solo si tiene contraseña definida; si no (Hostinger con una
// sola cuenta, o la clave de ejemplo sin valor), la cuenta de la aplicación.
function migrationUser() {
  const admin = (process.env.AJS_DB_ADMIN_USER || "").trim();
  return admin && process.env.AJS_DB_ADMIN_PASSWORD ? admin : process.env.AJS_DB_USER;
}

function runFlask(py, args, { allowFail = false } = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(py, ["-m", "flask", "--app", "wsgi", ...args],
      { cwd: ROOT, env: pythonEnv(), stdio: ["ignore", "inherit", "pipe"] });
    let stderr = "";
    p.stderr.on("data", (chunk) => {
      process.stderr.write(chunk);
      stderr = (stderr + chunk.toString()).slice(-20000);
    });
    p.on("error", reject);
    p.on("exit", (code) => {
      if (code === 0 || allowFail) return resolve(code);
      const err = new Error(`flask ${args[0]} terminó con código ${code}`);
      err.hint = startupHint(stderr);
      reject(err);
    });
  });
}

// Solo la espera del gunicorn vigente cuenta; las de arranques anteriores se abandonan.
function waitForBackend(timeoutMs = 60000) {
  const gen = backendGen;
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      if (gen !== backendGen) return;
      const sock = backendSocketPath
        ? net.connect(backendSocketPath)
        : net.connect(backendPort, "127.0.0.1");
      sock.once("connect", () => { sock.destroy(); if (gen === backendGen) resolve(); });
      sock.once("error", () => {
        sock.destroy();
        if (gen !== backendGen) return;
        if (Date.now() > deadline) {
          const err = new Error("gunicorn no respondió a tiempo");
          err.hint = lastBackendHint;
          reject(err);
        } else setTimeout(attempt, 500);
      });
    };
    attempt();
  });
}

function markBackendReady() {
  backendReady = true;
  startupError = null;
  log(`AJ System listo (backend en ${backendSocketPath || `127.0.0.1:${backendPort}`}).`);
}

// ---------------------------------------------------------------- backend Python

function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (err) { return err.code === "EPERM"; }
}

// Borra los sockets de procesos Node que ya no existen (el nombre lleva su PID).
function removeStaleSockets(dir) {
  let names = [];
  try { names = fs.readdirSync(dir); } catch { return; }
  for (const name of names) {
    const m = new RegExp(`^${SOCKET_PREFIX}(\\d+)\\.sock$`).exec(name);
    if (m && !pidAlive(Number(m[1]))) {
      try { fs.unlinkSync(path.join(dir, name)); } catch { /* otro proceso ya lo borró */ }
    }
  }
}

// Direcciones internas en orden de preferencia. El socket Unix bajo la aplicación es el que
// funciona en Hostinger; si gunicorn no puede abrirlo se prueba /tmp y, por último, TCP local.
function bindCandidates() {
  if (process.env.AJS_INTERNAL_PORT) return [{ port: Number(process.env.AJS_INTERNAL_PORT) }];
  if (process.env.AJS_INTERNAL_SOCKET) return [{ socket: process.env.AJS_INTERNAL_SOCKET }];
  const name = `${SOCKET_PREFIX}${process.pid}.sock`;
  const list = [];
  for (const dir of [SOCKET_DIR, os.tmpdir()]) {
    const p = path.join(dir, name);
    if (p.length < 100) list.push({ socket: p, dir });
  }
  list.push({ port: 0 });
  return list;
}

async function selectBindTarget() {
  const t = bindTargets[bindIndex];
  if (t.socket) {
    if (t.dir) {
      fs.mkdirSync(t.dir, { recursive: true });
      removeStaleSockets(t.dir);
    }
    try { fs.unlinkSync(t.socket); } catch (err) { if (err.code !== "ENOENT") throw err; }
    backendSocketPath = t.socket;
    backendPort = null;
  } else {
    backendSocketPath = null;
    backendPort = t.port || (await freePort());
  }
}

// gunicorn arranca con PR_SET_PDEATHSIG: si este proceso Node muere (incluso con SIGKILL),
// el kernel le envía SIGTERM y no quedan gunicorn huérfanos ocupando memoria.
const GUNICORN_LAUNCHER = [
  "import ctypes, signal, sys",
  "try:\n    ctypes.CDLL(None).prctl(1, signal.SIGTERM)\nexcept Exception:\n    pass",
  "from gunicorn.app.wsgiapp import run",
  "sys.argv = ['gunicorn'] + sys.argv[1:]",
  "sys.exit(run())",
].join("\n");

function startGunicorn(py) {
  const bind = backendSocketPath ? `unix:${backendSocketPath}` : `127.0.0.1:${backendPort}`;
  const gen = ++backendGen;
  const startedAt = Date.now();
  let stderrTail = "";
  child = spawn(py, [
    "-c", GUNICORN_LAUNCHER, "wsgi:app",
    "--bind", bind,
    "--workers", WORKERS,
    "--threads", THREADS,
    "--timeout", "120",
    "--graceful-timeout", "20",
    "--access-logfile", "-",
    "--forwarded-allow-ips", "127.0.0.1",
    // El socket de control de gunicorn usa una ruta única por usuario ($HOME/.gunicorn):
    // con varios procesos se pisarían. No se usa.
    "--no-control-socket",
  ], { cwd: ROOT, env: pythonEnv(), stdio: ["ignore", "inherit", "pipe"] });

  // La salida de error de gunicorn pasa por log() (redactada) para que el registro de
  // Hostinger la muestre; escrita directamente, Hostinger la descarta.
  let pending = "";
  child.stderr.on("data", (chunk) => {
    const text = pending + chunk.toString();
    const lines = text.split("\n");
    pending = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      const clean = redact(line).slice(0, 2000);
      log(`gunicorn: ${clean}`);
      stderrTail = (stderrTail + clean + "\n").slice(-20000);
    }
  });

  child.on("exit", (code, signal) => {
    if (gen !== backendGen) return;
    backendReady = false;
    if (pending.trim()) log(`gunicorn: ${redact(pending).slice(0, 2000)}`);
    if (shuttingDown) return;
    if (backendSocketPath) { try { fs.unlinkSync(backendSocketPath); } catch { /* ya no existe */ } }
    lastBackendHint = startupHint(stderrTail) || lastBackendHint;
    // Si no pudo abrir la dirección interna, se pasa a la siguiente alternativa.
    if (/Can't connect to|Connection in use/.test(stderrTail) && bindTargets.length > 1) {
      bindIndex = (bindIndex + 1) % bindTargets.length;
    }
    // Espera creciente para no saturar el servidor si falla siempre.
    const delay = Date.now() - startedAt > 60000 ? 3000 : Math.min(30000, 3000 * 2 ** Math.min(4, failures++));
    log(`gunicorn terminó (código ${code}, señal ${signal}); se reinicia en ${Math.round(delay / 1000)} s.`);
    if (Date.now() - bootStartedAt > 60000) {
      const err = new Error("gunicorn no respondió a tiempo");
      err.hint = lastBackendHint;
      failStartup(err);
    }
    setTimeout(async () => {
      if (shuttingDown) return;
      try {
        await selectBindTarget();
        startGunicorn(py);
        await waitForBackend();
        failures = 0;
        markBackendReady();
      } catch (e) { failStartup(e); }
    }, delay);
  });
}

// ---------------------------------------------------------------- recarga tras despliegue
//
// Hostinger puede reemplazar los archivos de un despliegue sin reiniciar este proceso. Los
// workers de gunicorn seguirían con el código Python anterior en memoria mientras leen
// plantillas nuevas del disco (versiones mezcladas: páginas que fallan o quedan en blanco).
// Se vigila el código y, cuando cambia y se estabiliza, se migra y se recargan los workers
// con SIGHUP (gunicorn arranca workers nuevos antes de retirar los antiguos: sin corte).

const WATCHED = ["ajsystem", "migrations", "resources", "sql", "requirements.lock", "wsgi.py"];
const NODE_FILES = ["server.js", "hostinger"];
const RELOAD_CHECK_MS = Math.max(5, Number(process.env.AJS_RELOAD_CHECK_SECONDS || 20)) * 1000;

function fingerprint(entries) {
  const hash = require("node:crypto").createHash("sha256");
  const walk = (rel) => {
    let st;
    try { st = fs.statSync(path.join(ROOT, rel)); } catch { return; }
    if (st.isDirectory()) {
      if (rel.endsWith("__pycache__")) return;
      for (const name of fs.readdirSync(path.join(ROOT, rel)).sort()) walk(path.join(rel, name));
    } else {
      hash.update(`${rel}\0${st.size}\0${st.mtimeMs}\n`);
    }
  };
  entries.forEach(walk);
  return hash.digest("hex");
}

let runningCode = null;
let runningNode = null;
let pendingCode = null;
let reloading = false;

async function checkForDeploy(py) {
  if (reloading || shuttingDown || !child) return;
  const current = fingerprint(WATCHED);
  if (current === runningCode) { pendingCode = null; return; }
  // Se espera a que el código deje de cambiar (despliegue terminado) antes de recargar.
  if (current !== pendingCode) { pendingCode = current; log("Cambio de código detectado; esperando a que termine el despliegue ..."); return; }
  reloading = true;
  try {
    verifyVendorIntegrity();
    py = ensurePythonEnv();
    if (process.env.AJS_AUTO_MIGRATE !== "0") {
      const migrator = migrationUser();
      await runFlask(py, ["db-migrate", "--user", migrator]);
    }
    runningCode = current;
    pendingCode = null;
    child.kill("SIGHUP");
    log("Código nuevo cargado: workers de gunicorn recargados.");
    if (fingerprint(NODE_FILES) !== runningNode) {
      log("AVISO: server.js u hostinger/ cambiaron; reinicie la aplicación en hPanel para aplicar esa parte.");
    }
  } catch (err) {
    log(`No se pudo recargar el código nuevo (se reintenta): ${err.message}`);
  } finally {
    reloading = false;
  }
}

async function boot() {
  try {
    log(`${verifyVendorIntegrity()} archivos de terceros verificados (SHA-256).`);
    const py = ensurePythonEnv();
    runningCode = fingerprint(WATCHED);
    runningNode = fingerprint(NODE_FILES);
    if (process.env.AJS_AUTO_MIGRATE !== "0") {
      const migrator = migrationUser();
      log(`Aplicando migraciones pendientes con la cuenta '${migrator}' ...`);
      await runFlask(py, ["db-migrate", "--user", migrator]);
    }
    if (process.env.AJS_INITIAL_ADMIN_USER) {
      await runFlask(py, ["ensure-initial-user"], { allowFail: true });
    }
    bindTargets = bindCandidates();
    bindIndex = 0;
    await selectBindTarget();
    setInterval(() => { checkForDeploy(py); }, RELOAD_CHECK_MS).unref();
    startGunicorn(py);
    await waitForBackend();
    markBackendReady();
  } catch (err) {
    if (err.retry && !shuttingDown) {
      log(err.message);
      setTimeout(boot, 10000);
      return;
    }
    failStartup(err);
  }
}

function failStartup(err) {
  if (backendReady || startupError) return;
  startupError = err;
  const hint = err.hint || startupHint(err.message);
  if (hint) log(`Causa probable: ${hint}`);
  log(`ERROR de arranque: ${err.message}`);
}

// ---------------------------------------------------------------- proxy HTTP

function clientIp(req) {
  // Cadena de saltos: entradas de X-Forwarded-For + la conexión directa. Se descartan
  // tantos saltos de confianza como proxies conocidos; el resto lo pudo falsear el cliente.
  const chain = String(req.headers["x-forwarded-for"] || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  chain.push(req.socket.remoteAddress || "");
  return chain[Math.max(0, chain.length - 1 - EDGE_PROXY_HOPS)];
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function statusPage(res, code, title, body) {
  res.writeHead(code, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Retry-After": "10",
    "X-Content-Type-Options": "nosniff",
  });
  res.end(`<!doctype html><html lang="es"><head><meta charset="utf-8"><title>AJ System</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>${title}</h1><p>${body}</p></body></html>`);
}

const server = http.createServer((req, res) => {
  if (!backendReady) {
    if (startupError) {
      // El detalle técnico queda en el registro del servidor (hPanel); la página solo indica qué revisar.
      const hint = startupError.hint || startupHint(startupError.message);
      return statusPage(res, 503, "Servicio no disponible",
        "La aplicación no pudo iniciarse." +
        (hint ? `</p><p><strong>Causa:</strong> ${escapeHtml(hint)}</p><p>Corrija el dato en hPanel y reinicie la aplicación.`
              : " El responsable del sistema debe revisar el registro de la aplicación en Hostinger."));
    }
    // Hostinger termina procesos que responden 5xx durante el health check inicial.
    // La página es transitoria y no se cachea; los errores reales siguen devolviendo 503.
    return statusPage(res, 200, "Iniciando AJ System…", "La aplicación se está preparando. Recargue en unos segundos.");
  }

  const headers = { ...req.headers };
  const proto = String(req.headers["x-forwarded-proto"] || "").split(",")[0].trim() || "http";
  headers["x-forwarded-for"] = clientIp(req);
  headers["x-forwarded-proto"] = proto;
  headers["x-forwarded-host"] = req.headers.host || "";
  delete headers["x-forwarded-port"];
  delete headers["x-forwarded-prefix"];

  const upstream = http.request(
    {
      ...(backendSocketPath ? { socketPath: backendSocketPath } : { host: "127.0.0.1", port: backendPort }),
      method: req.method,
      path: req.url,
      headers,
    },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      up.pipe(res);
    });
  upstream.on("error", (err) => {
    log(`Error del backend: ${err.message}`);
    if (!res.headersSent) statusPage(res, 502, "Servicio no disponible", "Inténtelo de nuevo en unos segundos.");
    else res.destroy();
  });
  req.pipe(upstream);
});

// Hostinger comprueba el puerto público por IPv4; no dejar el host implícito en ::.
server.listen(PORT, "0.0.0.0", () => {
  log(`Escuchando en el puerto ${PORT}.`);
  boot();
});

function shutdown() {
  shuttingDown = true;
  server.close();
  if (child) child.kill("SIGTERM");
  setTimeout(() => process.exit(0), 5000).unref();
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
process.on("exit", () => {
  if (child && child.exitCode === null) child.kill("SIGTERM");
  if (backendSocketPath) { try { fs.unlinkSync(backendSocketPath); } catch { /* ya no existe */ } }
});
