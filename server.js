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
 *   4. arranca gunicorn en 127.0.0.1 (puerto interno) y le reenvía todo el tráfico.
 *
 * No tiene dependencias npm: solo módulos nativos de Node.
 */
const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { ensurePythonEnv, ROOT, log } = require("./hostinger/python-env");
const { verifyVendorIntegrity } = require("./hostinger/integrity");

const PORT = Number(process.env.PORT || 3000);
// Proxies de confianza delante de este proceso (el de Hostinger que termina TLS).
const EDGE_PROXY_HOPS = Math.max(0, Number(process.env.AJS_EDGE_PROXY_HOPS ?? 1));
const WORKERS = String(process.env.AJS_WORKERS || 2);
const THREADS = String(process.env.AJS_THREADS || 4);

let backendPort = null;
let backendSocketPath = process.env.AJS_INTERNAL_SOCKET || path.join(ROOT, "tmp", "gunicorn.sock");
let backendReady = false;
let startupError = null;
let shuttingDown = false;
let child = null;

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
  [/gunicorn no respondió/, () => "El servidor de la aplicación no respondió a tiempo tras iniciar."],
];

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

function waitForBackend(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const sock = backendSocketPath
        ? net.connect(backendSocketPath)
        : net.connect(port, "127.0.0.1");
      sock.once("connect", () => { sock.destroy(); resolve(); });
      sock.once("error", () => {
        sock.destroy();
        if (Date.now() > deadline) reject(new Error("gunicorn no respondió a tiempo"));
        else setTimeout(attempt, 500);
      });
    };
    attempt();
  });
}

// ---------------------------------------------------------------- backend Python

function startGunicorn(py) {
  const bind = backendSocketPath ? `unix:${backendSocketPath}` : `127.0.0.1:${backendPort}`;
  child = spawn(py, [
    "-m", "gunicorn", "wsgi:app",
    "--bind", bind,
    "--workers", WORKERS,
    "--threads", THREADS,
    "--timeout", "120",
    "--graceful-timeout", "20",
    "--access-logfile", "-",
    "--forwarded-allow-ips", "127.0.0.1",
  ], { cwd: ROOT, env: pythonEnv(), stdio: "inherit" });

  child.on("exit", (code, signal) => {
    backendReady = false;
    if (shuttingDown) return;
    log(`gunicorn terminó (código ${code}, señal ${signal}); se reinicia en 3 s.`);
    setTimeout(() => {
      startGunicorn(py);
      waitForBackend(backendPort).then(() => { backendReady = true; }).catch((e) => log(e.message));
    }, 3000);
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
    if (process.env.AJS_INTERNAL_PORT) {
      backendSocketPath = null;
      backendPort = Number(process.env.AJS_INTERNAL_PORT);
    } else if (backendSocketPath) {
      fs.mkdirSync(path.dirname(backendSocketPath), { recursive: true });
      try { fs.unlinkSync(backendSocketPath); } catch (err) {
        if (err.code !== "ENOENT") throw err;
      }
    } else {
      backendPort = await freePort();
    }
    startGunicorn(py);
    await waitForBackend(backendPort);
    backendReady = true;
    log(`AJ System listo (backend en ${backendSocketPath || `127.0.0.1:${backendPort}`}).`);
    setInterval(() => { checkForDeploy(py); }, RELOAD_CHECK_MS).unref();
  } catch (err) {
    startupError = err;
    const hint = err.hint || startupHint(err.message);
    if (hint) log(`Causa probable: ${hint}`);
    log(`ERROR de arranque: ${err.message}`);
  }
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
