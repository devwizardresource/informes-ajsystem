"use strict";
/*
 * Prepara el entorno Python de AJ System en el alojamiento Node.js de Hostinger.
 *
 * Hostinger solo despliega aplicaciones Node.js desde Git; este módulo localiza un
 * intérprete Python 3.10+ del servidor, crea un entorno virtual propio (.venv-runtime)
 * e instala requirements.lock con `--require-hashes`: cada paquete se verifica contra la
 * suma SHA-256 fijada en el repositorio, así que un paquete alterado en el índice o en
 * tránsito se rechaza (falla cerrado). Es idempotente: si el lock no cambió y el entorno
 * funciona, no reinstala nada.
 */
const { spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(ROOT, ".venv-runtime");
const MARKER = path.join(VENV_DIR, ".ajs-requirements.sha256");
const LOCKFILE = path.join(ROOT, "requirements.lock");
// Hostinger arranca varios procesos Node a la vez: solo uno crea el entorno; si dos lo
// hicieran, uno borraría el entorno a medio instalar del otro.
const BUILD_LOCK = path.join(ROOT, "tmp", "python-env.lock");
const BUILD_LOCK_STALE_MS = 15 * 60 * 1000;
const CANDIDATES = ["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"];
const MIN_VERSION = [3, 10];

function log(msg) {
  console.log(`[ajsystem] ${msg}`);
}

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { cwd: ROOT, encoding: "utf8", env: process.env, ...opts });
  return { ok: res.status === 0 && !res.error, out: `${res.stdout || ""}${res.stderr || ""}`.trim() };
}

function pythonVersion(bin) {
  const res = run(bin, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"]);
  if (!res.ok) return null;
  const [major, minor] = res.out.split(".").map(Number);
  return [major, minor];
}

function findSystemPython() {
  const list = process.env.AJS_PYTHON ? [process.env.AJS_PYTHON] : CANDIDATES;
  for (const bin of list) {
    const v = pythonVersion(bin);
    if (v && (v[0] > MIN_VERSION[0] || (v[0] === MIN_VERSION[0] && v[1] >= MIN_VERSION[1]))) {
      return { bin, version: v.join(".") };
    }
  }
  return null;
}

function venvPython() {
  return path.join(VENV_DIR, "bin", "python");
}

function requirementsHash() {
  return crypto.createHash("sha256").update(fs.readFileSync(LOCKFILE)).digest("hex");
}

function isReady() {
  if (!fs.existsSync(venvPython()) || !fs.existsSync(MARKER)) return false;
  if (fs.readFileSync(MARKER, "utf8").trim() !== requirementsHash()) return false;
  return run(venvPython(), ["-c", "import flask, pymysql, gunicorn, argon2, PIL, openpyxl"]).ok;
}

function pidAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (err) { return err.code === "EPERM"; }
}

function takeBuildLock() {
  fs.mkdirSync(BUILD_LOCK);
  fs.writeFileSync(path.join(BUILD_LOCK, "pid"), String(process.pid));
}

// El bloqueo es abandonado si su dueño ya no existe (Hostinger termina procesos a mitad de la
// instalación al desplegar), si no dejó PID o si es demasiado antiguo.
function buildLockAbandoned() {
  const st = fs.statSync(BUILD_LOCK);
  if (Date.now() - st.mtimeMs > BUILD_LOCK_STALE_MS) return true;
  let pid = NaN;
  try { pid = Number(fs.readFileSync(path.join(BUILD_LOCK, "pid"), "utf8")); } catch { /* aún sin PID */ }
  if (!pid) return Date.now() - st.mtimeMs > 10000;
  return !pidAlive(pid);
}

function acquireBuildLock() {
  try {
    fs.mkdirSync(path.dirname(BUILD_LOCK), { recursive: true });
    takeBuildLock();
    return true;
  } catch (err) {
    // Sin directorio escribible para el bloqueo se continúa sin él (comportamiento anterior).
    if (err.code !== "EEXIST") return true;
  }
  try {
    if (buildLockAbandoned()) {
      fs.rmSync(BUILD_LOCK, { recursive: true, force: true });
      takeBuildLock();
      return true;
    }
  } catch { /* otro proceso lo tomó antes */ }
  return false;
}

/**
 * Devuelve la ruta del Python del entorno virtual, o lanza un Error con el diagnóstico.
 * Si otro proceso está creando el entorno, el Error lleva `retry = true`.
 */
function ensurePythonEnv() {
  if (isReady()) return venvPython();
  if (!acquireBuildLock()) {
    const err = new Error("Otro proceso está preparando el entorno Python; se reintenta en unos segundos.");
    err.retry = true;
    throw err;
  }
  try {
    if (isReady()) return venvPython();
    return buildPythonEnv();
  } finally {
    fs.rmSync(BUILD_LOCK, { recursive: true, force: true });
  }
}

function buildPythonEnv() {
  const sys = findSystemPython();
  if (!sys) {
    throw new Error(
      "No se encontró Python 3.10 o superior en el servidor. Defina AJS_PYTHON con la ruta del intérprete " +
        "o use un plan de Hostinger con Python (VPS).");
  }
  log(`Python del sistema: ${sys.bin} (${sys.version}). Creando entorno en .venv-runtime ...`);
  fs.rmSync(VENV_DIR, { recursive: true, force: true });
  let res = run(sys.bin, ["-m", "venv", VENV_DIR]);
  if (!res.ok) {
    // Algunos servidores no traen ensurepip: se crea sin pip y se instala con get-pip si existe.
    log("venv con pip falló; se intenta con virtualenv ...");
    res = run(sys.bin, ["-m", "virtualenv", VENV_DIR]);
    if (!res.ok) throw new Error(`No se pudo crear el entorno virtual:\n${res.out.slice(-2000)}`);
  }

  const py = venvPython();
  log("Instalando dependencias verificadas por hash (requirements.lock) ...");
  // --require-hashes: pip rechaza cualquier archivo cuyo SHA-256 no coincida con el lock.
  // --only-binary: no se ejecuta código de compilación (setup.py) de terceros en el servidor.
  res = run(py, ["-m", "pip", "install", "--no-cache-dir", "--require-hashes", "--only-binary", ":all:",
                 "--no-deps", "-r", LOCKFILE], { maxBuffer: 64 * 1024 * 1024 });
  if (!res.ok) throw new Error(`pip install falló (dependencias no verificadas o no disponibles):\n${res.out.slice(-4000)}`);

  fs.writeFileSync(MARKER, `${requirementsHash()}\n`);
  log("Entorno Python listo.");
  return py;
}

module.exports = { ensurePythonEnv, venvPython, ROOT, log };

// `npm run build`: prepara el entorno durante el despliegue. Si el contenedor de
// compilación no tiene Python, no se aborta el despliegue: server.js lo reintenta al arrancar.
if (require.main === module) {
  try {
    ensurePythonEnv();
  } catch (err) {
    log(`AVISO en compilación: ${err.message}`);
    log("Se reintentará al arrancar la aplicación.");
  }
}
