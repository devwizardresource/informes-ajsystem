# Recursos de terceros alojados localmente

No se cargan recursos de CDN (la CSP solo permite `'self'`). Cada archivo está fijado por versión y
su SHA-256 figura en `SHA256SUMS`; `server.js` lo comprueba al arrancar en Hostinger y la
auditoría de CI (`scripts/security_audit.sh`) también.

| Paquete (npm) | Versión | Publicado | Licencia | Archivos |
|---|---|---|---|---|
| tesseract.js | 7.0.0 | 2025-12-15 | Apache-2.0 | `tesseract/tesseract.min.js`, `tesseract/worker.min.js` |
| tesseract.js-core | 7.0.0 | 2025-12-15 | Apache-2.0 | `tesseract/core/tesseract-core{,-simd,-relaxedsimd}-lstm.wasm.js` |
| @tesseract.js-data/spa | 1.0.0 | — | Apache-2.0 | `tesseract/lang/spa.traineddata.gz` (4.0.0_best_int) |
| @tesseract.js-data/eng | 1.0.0 | — | Apache-2.0 | `tesseract/lang/eng.traineddata.gz` (4.0.0_best_int) |
| @fontsource/ubuntu-sans | 5.3.0 | 2026-07-19 | Ubuntu Font Licence 1.0 | `../fonts/ubuntu-sans-latin*-{400..800}-normal.woff2` |

Actualizar: `npm pack <paquete>@<versión> --ignore-scripts` en un directorio vacío, copiar los
archivos, ejecutar `npm audit` sobre `scripts/vendor-audit/package.json` y regenerar `SHA256SUMS`
con `cd ajsystem/static && find vendor fonts -type f ! -name SHA256SUMS ! -name "*.txt" ! -name "*.md" | sort | xargs sha256sum > vendor/SHA256SUMS`.
