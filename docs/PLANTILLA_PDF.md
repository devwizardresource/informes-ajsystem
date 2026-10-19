# Formato de informe y PDF

## Estado

La plantilla oficial **«Formato de Informe AJ System»** está integrada (`aj_v1`) y es la predeterminada:

| Pieza | Archivo |
|---|---|
| Campos, secciones y validación | `ajsystem/report_templates/aj_v1.json` |
| Captura en pantalla (misma maquetación que la referencia, editable, adaptable a móvil) | `templates/report_detail.html` + `static/css/sheet.css` |
| PDF (tamaño carta, una hoja + anexo fotográfico) | `document_templates/aj_v1.html` + `document_templates/assets/` (logo y Ubuntu Sans TTF) |

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
