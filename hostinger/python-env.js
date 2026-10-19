"use strict";
/*
 * Prepara el entorno Python de AJ System en el alojamiento Node.js de Hostinger.
 *
 * Hostinger solo despliega aplicaciones Node.js desde Git; este módulo localiza un
 * intérprete Python 3.9+ del servidor, crea un entorno virtual propio (.venv-runtime)
 * e instala requirements.txt. Es idempotente: si los requisitos no cambiaron y el
 * entorno funciona, no reinstala nada.
 */
const { spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const VENV_DIR = path.join(ROOT, ".venv-runtime");
const MARKER = path.join(VENV_DIR, ".ajs-requirements.sha256");
const REQUIREMENTS = path.join(ROOT, "requirements.txt");
// WeasyPrint depende de Pango del sistema; si no se instala, la app funciona igual
// y solo la descarga de PDF devuelve un error controlado (import diferido).
const OPTIONAL_PACKAGES = new Set(["weasyprint"]);
const CANDIDATES = ["python3.13", "python3.12", "python3.11", "python3.10", "python3.9", "python3", "python"];
const MIN_VERSION = [3, 9];

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
  return crypto.createHash("sha256").update(fs.readFileSync(REQUIREMENTS)).digest("hex");
}

function parseRequirements() {
  const required = [];
  const optional = [];
  for (const raw of fs.readFileSync(REQUIREMENTS, "utf8").split("\n")) {
    const line = raw.replace(/#.*/, "").trim();
    if (!line) continue;
    const name = line.split(/[=<>~!\[ ]/)[0].toLowerCase();
    (OPTIONAL_PACKAGES.has(name) ? optional : required).push(line);
  }
  return { required, optional };
}

function isReady() {
  if (!fs.existsSync(venvPython()) || !fs.existsSync(MARKER)) return false;
  if (fs.readFileSync(MARKER, "utf8").trim() !== requirementsHash()) return false;
  return run(venvPython(), ["-c", "import flask, pymysql, gunicorn, argon2"]).ok;
}

/** Devuelve la ruta del Python del entorno virtual, o lanza un Error con el diagnóstico. */
function ensurePythonEnv() {
  if (isReady()) return venvPython();

  const sys = findSystemPython();
  if (!sys) {
    throw new Error(
      "No se encontró Python 3.9 o superior en el servidor. Defina AJS_PYTHON con la ruta del intérprete " +
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
  run(py, ["-m", "pip", "install", "--upgrade", "pip"]);
  const { required, optional } = parseRequirements();
  log(`Instalando dependencias: ${required.join(", ")}`);
  res = run(py, ["-m", "pip", "install", "--no-cache-dir", ...required], { maxBuffer: 64 * 1024 * 1024 });
  if (!res.ok) throw new Error(`pip install falló:\n${res.out.slice(-4000)}`);

  for (const pkg of optional) {
    log(`Instalando dependencia opcional: ${pkg}`);
    const opt = run(py, ["-m", "pip", "install", "--no-cache-dir", pkg], { maxBuffer: 64 * 1024 * 1024 });
    if (!opt.ok) log(`AVISO: no se pudo instalar ${pkg}; la generación de PDF no estará disponible.`);
  }

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
