# Formato de informe y PDF

## Estado

La plantilla oficial es **el archivo del propietario `resources/Plantilla_Informe_AJ_System.html`**, y la aplicación lo usa directamente (no una copia):

| Pieza | Cómo |
|---|---|
| Hoja de trabajo del informe (pantalla) | `reports/sheet.py` lee el archivo, sirve su `<style>` en `/informes/plantilla.css` (la CSP no admite estilos en línea) y convierte cada celda de valor en el campo correspondiente, emparejando por la etiqueta de la fila. La página del informe es la plantilla a página completa, con su barra de herramientas («Imprimir / Guardar como PDF», «Limpiar formulario») más Foto, Guardar y Cerrar |
| PDF | La misma hoja y el mismo CSS, con el `@media print` de la plantilla, generados por WeasyPrint (`document_templates/aj_v1.html` solo añade la fuente y el anexo fotográfico) |
| Campos y validación | `report_templates/aj_v1.json`: las etiquetas deben coincidir con las de la plantilla; la aplicación **no arranca** si falta alguna fila |

Si el propietario modifica el archivo (textos, colores, orden de filas), la pantalla y el PDF siguen el cambio sin tocar código. Si añade o renombra una fila, hay que reflejarlo en `aj_v1.json`.

Diferencias con la referencia, intencionadas:
- Se añadió la fila **«Marca»** en la sección 1: los equipos se indexan y ordenan por marca y número de serie (Consolidación → «Ordenar por: Marca y número de serie»).
- El número del informe sale del identificador (`AJ-2026-000123` → «Informe N.º 00123 de 2026»).
- El bloque «Registro de quien realizó el informe» se rellena con el autor (usuario, nombre y N.º de identificación) y las firmas se dibujan o cargan en la propia hoja.
- El PDF añade, después de la hoja, un **anexo fotográfico** con las fotos del equipo en orden cronológico y la huella SHA-256 del contenido.

`provisional_v0` se conserva solo para mostrar informes creados antes de la plantilla oficial.

## Cómo está separado

```
report_templates/<codigo>.json     → QUÉ datos se capturan y validan
reports (JSON en MySQL)            → los datos, ligados a template_code
documents.build_document_data()    → contrato de datos del documento
document_templates/<archivo>.html  → CÓMO se presenta en el PDF
```

### JSON de campos

Secciones con `id`, `title` (vacío = encabezado sin franja), `number` y `fields`. Tipos: `text`, `textarea`, `date`, `integer`, `select`, `checkbox`; `select` con `"widget": "radio"` se muestra como casillas excluyentes. `maps_to` admite `title`, `equipment_serial` y `equipment_brand` (columnas indexadas). `"default": "today"` rellena la fecha al crear.

### Contrato del documento (`doc`)

| Clave | Contenido |
|---|---|
| `doc.numero`, `doc.anio` | Número de 5 cifras y año |
| `doc.valores` | `{campo: texto}` ya formateado |
| `doc.secciones_plantilla` | Secciones y campos de la plantilla |
| `doc.registro` | `usuario`, `nombre`, `identificacion` del autor |
| `doc.firmas` | `FIRMA_ELABORA`, `FIRMA_RECIBE` como `data:` URI |
| `doc.fotos[]` | `src`, `fecha`, `autor`, `sha256` |
| `doc.meta`, `doc.historial`, `doc.secciones` | Como antes (estado, huella, trazabilidad) |

WeasyPrint solo puede leer imágenes `data:` y archivos de `document_templates/`: cualquier otra URL se bloquea.
