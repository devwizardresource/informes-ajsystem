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

function runFlask(py, args, { allowFail = false } = {}) {
  return new Promise((resolve, reject) => {
    const p = spawn(py, ["-m", "flask", "--app", "wsgi", ...args], { cwd: ROOT, env: pythonEnv(), stdio: "inherit" });
    p.on("error", reject);
    p.on("exit", (code) => {
      if (code === 0 || allowFail) resolve(code);
      else reject(new Error(`flask ${args[0]} terminó con código ${code}`));
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

async function boot() {
  try {
    log(`${verifyVendorIntegrity()} archivos de terceros verificados (SHA-256).`);
    const py = ensurePythonEnv();
    if (process.env.AJS_AUTO_MIGRATE !== "0") {
      const migrator = process.env.AJS_DB_ADMIN_USER || process.env.AJS_DB_USER;
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
  } catch (err) {
    startupError = err;
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
      // El detalle técnico queda en el registro del servidor (hPanel), no en la página.
      return statusPage(res, 503, "Servicio no disponible",
        "La aplicación no pudo iniciarse. El responsable del sistema debe revisar el registro de la aplicación en Hostinger.");
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
