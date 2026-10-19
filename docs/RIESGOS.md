# Riesgos conocidos, limitaciones y pendientes

## Decisiones pendientes del propietario

| # | Tema | Impacto |
|---|---|---|
| 1 | **Plan de Hostinger**: Python 3.10+, Pango (PDF) y permiso para disparadores en MariaDB | Si falta alguno, la app no arranca o no genera PDF (ver DESPLIEGUE.md); alternativa: VPS |
| 2 | Fila «Marca» añadida al formato oficial | Confirmar que se mantiene (permite ordenar equipos por marca y serie) |
| 3 | Nombre visible del perfil responsable | Configurable en cualquier momento, sin código |
| 4 | Interpretaciones de negocio de ROLES_Y_PERMISOS.md (visibilidad de borradores, «pendiente de revisión», edición tras reabrir) | Confirmar o ajustar |
| 5 | Política de copias (frecuencia, retención, cifrado y ubicación externa) | Propuesta en RESPALDO_Y_RESTAURACION.md |
| 6 | Protección de la rama `main` en GitHub exigiendo la CI | Sin ella, un push con pruebas fallidas también se despliega en Hostinger |

## Riesgos técnicos residuales

- **Auditoría y DBA**: un superusuario de MySQL puede alterar el registro. Se **detecta** con la cadena HMAC, pero no puede impedirse desde la aplicación. Si además obtiene la clave HMAC, podría reescribir la cadena. Conviene anclar fuera del servidor la cabeza de la cadena (ver AUDITORIA.md).
- **Clave HMAC**: si se pierde, no podrá verificarse el histórico; si se filtra, se debilita la detección. Aún no existe un procedimiento de rotación.
- **Sistema operativo e IP**: el sistema operativo lo declara el cliente; la IP solo es fiable sin proxy o con `AJS_TRUST_PROXY_HOPS` correcto.
- **Límite de intentos por IP**: un atacante con muchas IP puede repartir los intentos. El límite por usuario lo mitiga, aunque permite bloquear temporalmente una cuenta de forma deliberada (15 minutos).
- **OCR**: la calidad depende de la foto (enfoque, reflejos, ángulo). Las sugerencias solo rellenan campos vacíos y el técnico debe revisarlas antes de guardar. La primera foto en cada dispositivo descarga ≈ 5 MB de modelos de idioma (quedan en caché).
- **Tamaño de la base**: las fotos se guardan en MariaDB/MySQL (≈ 0,3–1,5 MB cada una, máximo 40 por informe). Vigile la cuota de la base del plan de Hostinger.
- **Serialización de la auditoría**: todas las escrituras pasan por una fila bloqueada. Es suficiente para decenas de usuarios; con mucha concurrencia habría que revisarlo.
- **DDL no transaccional**: una migración que falle a mitad puede dejar el esquema parcial. Por eso se exige un respaldo previo.
- **WeasyPrint** depende de Pango del sistema: en un alojamiento compartido podría no estar disponible. La aplicación sigue funcionando y la descarga muestra un error controlado y auditado.
- **Mensajes flash**: viajan en una cookie firmada de Flask (`ajs_flash`). No contienen datos sensibles.
- **Respaldos sin cifrar** por el propio script: dependen del cifrado del almacenamiento de destino.

## Limitaciones actuales

- No hay recuperación de contraseña por correo (exigiría un servicio externo): la restablece el responsable administrativo.
- No hay autenticación de dos factores.
- No existe un estado formal de «revisado» o «aprobado» de los informes.
- Los adjuntos (fotos, archivos) no están contemplados.
- La interfaz no se ha probado con lectores de pantalla ni en navegadores automatizados.

## Entorno de desarrollo creado durante esta sesión (solo local)

- Contenedor Docker `ajsys-mysql-dev` (MySQL 8.4) en `127.0.0.1:3307`, con el volumen `ajsys-mysql-data`.
- `.env` local con secretos aleatorios (0600, excluido de git).
- Base `ajsystem` con el usuario `responsable` (contraseña temporal entregada por separado; obliga a cambiarla en el primer acceso).
- La copia de prueba `backups/ajsystem_20261019T100301Z.*`.
