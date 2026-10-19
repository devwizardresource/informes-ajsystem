# Plantilla del informe y del PDF: integración pendiente

## Estado

- **Campos de captura:** provisionales y mínimos (`ajsystem/report_templates/provisional_v0.json`): título, número de serie y observaciones. **No son los campos definitivos de la auditoría.**
- **Documento PDF:** plantilla provisional (`ajsystem/document_templates/provisional_v0.html`), sin logos ni identidad institucional y con un aviso visible de «plantilla provisional».
- El motor (WeasyPrint), la descarga autorizada y auditada, la paginación, la trazabilidad y la huella SHA-256 del contenido **ya funcionan**.

## Cómo está separado

```
report_templates/<codigo>.json     → QUÉ datos se capturan y validan (formulario y servidor)
reports/data (JSON en MySQL)       → los datos, ligados a template_code
documents.build_document_data()    → CONTRATO estable de datos para el documento
document_templates/<archivo>.html  → CÓMO se presenta (HTML + CSS de impresión)
```

Cada informe guarda su `template_code`: al activar una plantilla nueva, los informes anteriores se siguen mostrando y generando con la suya.

### Formato del JSON de campos

```json
{ "code": "oficial_v1", "name": "...", "provisional": false, "document_template": "oficial_v1.html",
  "sections": [ { "id": "equipo", "title": "Datos del equipo", "fields": [
      { "id": "numero_serie", "label": "Número de serie", "type": "text", "max_length": 100,
        "required_on_close": true, "maps_to": "equipment_serial" },
      { "id": "estado_disco", "label": "Estado del disco", "type": "select", "options": ["Bueno", "Regular", "Malo"] } ] } ] }
```

Tipos: `text`, `textarea`, `date`, `integer`, `select`, `checkbox`. `maps_to` admite `title` y `equipment_serial` (las columnas indexadas para la búsqueda).

### Contrato de datos del documento (`doc`)

| Clave | Contenido |
|---|---|
| `doc.plantilla` | `codigo`, `nombre`, `provisional` |
| `doc.meta` | `codigo`, `estado`, `revision`, `autor`, `creado`, `cerrado`, `cerrado_por`, `enviado`, `enviado_por`, `reaperturas`, `huella`, `generado`, `generado_por`, `zona_horaria` |
| `doc.secciones[]` | `titulo`, `campos[]` → `id`, `etiqueta`, `tipo`, `valor` (texto ya formateado) |
| `doc.historial[]` | `accion`, `fecha`, `usuario`, `estado`, `revision`, `motivo` (sin guardados intermedios) |

## Tareas pendientes al recibir la plantilla oficial

1. [ ] Recibir del propietario: lista definitiva de campos (nombre, tipo, obligatoriedad al cerrar, opciones), diseño del documento, logos originales en alta resolución (SVG o PNG) y tamaño de página y márgenes.
2. [ ] Crear `report_templates/oficial_v1.json` con los campos definitivos.
3. [ ] Crear `document_templates/oficial_v1.html` respetando las dimensiones y la distribución; colocar los logos en `document_templates/` (el motor solo carga recursos de esa carpeta).
4. [ ] Controlar los saltos de página (`page-break-inside: avoid`, `@page`) con datos reales largos.
5. [ ] Mantener en el documento el identificador, la revisión, el historial de trazabilidad y la huella.
6. [ ] Activar con `AJS_REPORT_TEMPLATE=oficial_v1` y reiniciar.
7. [ ] Añadir pruebas: validación de los campos obligatorios oficiales y generación del PDF con datos máximos.
8. [ ] Validar el resultado impreso con el propietario y obtener su aprobación.
9. [ ] Instalar `libharfbuzz-subset` en el servidor para eliminar el aviso de WeasyPrint y reducir el tamaño del PDF.

**El diseño definitivo del PDF NO está terminado.**
