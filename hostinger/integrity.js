"use strict";
/*
 * Verifica, al arrancar, que los recursos de terceros servidos al navegador (motor OCR y fuentes)
 * coinciden con las sumas SHA-256 fijadas en ajsystem/static/vendor/SHA256SUMS. Si un archivo
 * fue alterado en el despliegue, la aplicación no arranca (falla cerrado).
 */
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const STATIC_DIR = path.resolve(__dirname, "..", "ajsystem", "static");
const MANIFEST = path.join(STATIC_DIR, "vendor", "SHA256SUMS");

function verifyVendorIntegrity() {
  const problems = [];
  const lines = fs.readFileSync(MANIFEST, "utf8").split("\n").filter(Boolean);
  for (const line of lines) {
    const m = line.match(/^([0-9a-f]{64}) [ *](.+)$/);
    if (!m) { problems.push(`línea inválida en SHA256SUMS: ${line.slice(0, 80)}`); continue; }
    const file = path.resolve(STATIC_DIR, m[2]);
    if (!file.startsWith(STATIC_DIR + path.sep)) { problems.push(`ruta fuera de static: ${m[2]}`); continue; }
    let digest;
    try {
      digest = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
    } catch (err) {
      problems.push(`${m[2]}: ${err.code || err.message}`);
      continue;
    }
    if (digest !== m[1]) problems.push(`${m[2]}: suma SHA-256 distinta`);
  }
  if (problems.length) {
    throw new Error(`Integridad de recursos de terceros comprometida:\n  ${problems.join("\n  ")}`);
  }
  return lines.length;
}

module.exports = { verifyVendorIntegrity };

if (require.main === module) {
  try {
    console.log(`[ajsystem] ${verifyVendorIntegrity()} archivos de terceros verificados.`);
  } catch (err) {
    console.error(`[ajsystem] ${err.message}`);
    process.exit(1);
  }
}
