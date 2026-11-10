# Plan de integración entre Weyland Automate y los bots

Este contrato está dirigido al desarrollador web y a quien mantiene los bots ACME, supermercados y control de vencimientos. Define qué debe recibir cada parte, cuándo descargar archivos y cómo conservar la relación entre solicitud y ejecución.

**El contrato objetivo de este documento es una propuesta de implementación.** La autenticación por sesión y varias rutas ya existen; la operación atómica por ID, las credenciales segmentadas, la idempotencia y los nuevos cuerpos descritos requieren trabajo coordinado en backend y bots. Este documento no modifica el servidor ni anuncia esos cambios como disponibles.

Archivos para desarrollar: [OpenAPI propuesto](contrato_web_bots/openapi.json) y [ejemplos JSON](contrato_web_bots/ejemplos.json). Todos los IDs, nombres, hashes y usuarios de los ejemplos son ficticios.

## Reglas acordadas

1. El cliente crea la solicitud en la web. El operador selecciona esa solicitud y pulsa Ejecutar.
2. El operador inicia el bot indicando el ID real: `ejecutar.bat web-procesar --id-solicitud 123`.
3. **`id_solicitud = id_ejecucion`**: ambos nombres identifican el mismo registro de la plataforma. En los JSON se utilizan `solicitud_id` y `ejecucion_id`, siempre con el mismo valor entero positivo.
4. El bot ejecuta únicamente ese ID. No crea otra solicitud, no elige otro ID y no reutiliza la última completada cuando falta trabajo.
5. El bot inicia sesión por HTTP, consulta el ID, informa el comienzo, descarga el input cuando corresponde y entrega resultado o error al mismo ID.
6. El identificador local del bot y las claves de idempotencia sirven para trazabilidad o reintentos HTTP; no son nuevos IDs de ejecución web.

El backend es responsable del estado oficial, las autorizaciones, la persistencia de archivos y las notificaciones web. La lógica de negocio permanece en cada bot.

## Estados y flujo

Se conservan los nombres actuales del backend:

| Estado | Significado | Quién lo establece |
|---|---|---|
| `PENDIENTE` | El cliente creó la solicitud; todavía no se liberó al bot. | Web. |
| `EN_COLA` | El operador seleccionó Ejecutar. Está habilitada para comenzar. | Web, por acción del operador. |
| `EN_PROCESO` | El bot autorizado confirmó el comienzo de ese ID. | Backend, al aceptar `/siguiente` con el ID seleccionado. |
| `COMPLETADA` | El reporte quedó guardado y se confirmó el resultado. | Backend, al aceptar `/reporte`. |
| `ERROR` | Se registró un fallo de esa ejecución. | Backend, al aceptar `/error` o por intervención autorizada. |
| `CANCELADA` | Se canceló antes de comenzar según las reglas de la plataforma. | Web. |

La interfaz puede mostrar EN_COLA como “pendiente de ejecución” y COMPLETADA como “terminado”. No cambiar el significado de PENDIENTE sin coordinar una migración con los clientes existentes.

```text
Cliente crea → PENDIENTE
Operador pulsa Ejecutar → EN_COLA
Operador inicia bot con --id-solicitud → login y consulta de ese ID
Bot informa comienzo atómico → EN_PROCESO
Descarga input si se requiere → procesamiento
Reporte guardado → COMPLETADA → notificación web
Fallo confirmado → ERROR → notificación web
```

## Qué existe y qué debe implementar la web

| Operación | Backend actual | Contrato objetivo |
|---|---|---|
| CSRF, login y perfil | `/auth/csrf`, `/auth/login`, `/auth/perfil`. | Conservar sesión Django y distinguir errores de sesión y clave. |
| Crear solicitud y liberarla | `POST /ejecuciones` y `POST /ejecuciones/{id}/ejecutar`. | Siguen siendo acciones humanas; el bot no las utiliza. |
| Consultar el ID | Los bots usan `/ejecuciones/{id}` y `/procesos/{id}` con sesión. | Añadir `GET /bot/ejecuciones/{id}` con todos los metadatos y autorización por bot. |
| Comenzar trabajo | `/bot/ejecuciones/siguiente` utiliza la selección de trabajo heredada. | Adaptar `POST /bot/ejecuciones/siguiente` para iniciar atómicamente solo el ID obligatorio del cuerpo. |
| Descargar input | `/bot/ejecuciones/{id}/input`. | Conservar ruta, añadir segmentación de permisos y metadatos de integridad. |
| Entregar reporte | Multipart, campo `archivo`; devuelve ID y COMPLETADA. | Añadir `metadatos`, idempotencia, integridad y confirmación detallada. |
| Informar error | JSON con `mensaje`; devuelve ID y ERROR. | Añadir IDs explícitos, código de error e idempotencia. |
| Autorización del bot | Una clave global; no exige la sesión web en `/bot/`. | Exigir sesión y clave individual vinculadas, con alcance por empresa y proceso. |

Todas las rutas de las tablas están bajo `/api`. En producción la base debe ser HTTPS; HTTP se permite únicamente en loopback para pruebas explícitas.

Se conserva el nombre `/siguiente`, pero cambia su contrato: recibe obligatoriamente el ID seleccionado y nunca elige otra solicitud de la cola. Coordinar el despliegue con los clientes existentes; si necesitan conservar la selección heredada, separarla en una versión explícita. El contrato objetivo no utiliza esa selección como fallback ante un ID ausente, inválido o no ejecutable.

## Autenticación que debe habilitar la web

### Obtener CSRF

`GET /api/auth/csrf`, sin sesión previa. Respuesta 200:

```json
{"csrf": "TOKEN_CSRF_OPACO"}
```

La respuesta establece la cookie CSRF. El bot conserva las cookies usando una misma sesión HTTP; no intenta interpretar su contenido.

### Iniciar sesión

`POST /api/auth/login`, cookie y encabezados `X-CSRFToken` y `Referer` del mismo origen. Cuerpo:

```json
{"correo": "robot.supermercados@example.test", "password": "CONTRASENA_DE_PRUEBA_NO_REAL"}
```

Respuesta 200: perfil del usuario y cookie de sesión. Mantener la estructura actual de `usuario`, `empresa` y `permisos`. La cuenta debe ser de servicio, sin privilegios de administrador global. La empresa del perfil identifica la cuenta; el acceso a solicitudes de clientes se determina además mediante el alcance autorizado de su identidad de bot.

Después del login, el bot vuelve a obtener CSRF porque Django lo rota. El servidor nunca devuelve la contraseña ni la clave de API en el perfil.

### Consultar perfil y recuperar la sesión

`GET /api/auth/perfil`, con cookie. Responde 200 con el perfil, o 401 si caducó la sesión.

El mecanismo acordado es sesión Django: no hace falta un endpoint JWT `/refresh`. Al caducar, el bot vuelve a iniciar sesión una vez y reintenta únicamente la petición HTTP que corresponda. No vuelve a ejecutar el proceso de negocio.

### Autorizar las rutas del bot

El contrato objetivo exige, en todas las rutas `/bot/`:

- Cookie de sesión válida y cuenta de servicio activa.
- `X-Api-Key` individual del bot, vinculada a esa cuenta.
- Permiso de esa identidad sobre la empresa y el proceso de la solicitud.
- En POST, `X-CSRFToken` y `Referer` válidos, además de la sesión y clave.

No basta con que el cliente Python compruebe el perfil: el servidor debe imponer estos controles. La clave se registra mediante su huella, se emite por un canal privado y permite revocación/rotación. No compartir una sola clave con todos los bots. No usar IDs difíciles de adivinar como sustituto de autorización.

**Cambio necesario en los bots:** actualmente `/bot/` envía la clave y la cookie, pero no CSRF. Adaptar el transporte para estos POST y distinguir sesión caducada, clave inválida y permiso denegado antes de activar la nueva política. CORS del navegador no es necesario para permitir llamadas de `requests`; no desactivar CSRF ni habilitar orígenes arbitrarios para resolver la integración.

## Consulta de la solicitud seleccionada

Nuevo endpoint: `GET /api/bot/ejecuciones/123`. No cambia el estado. Debe devolver todo lo necesario para procesar y recuperar una entrega:

```json
{
  "solicitud_id": 123,
  "ejecucion_id": 123,
  "estado": "EN_COLA",
  "empresa": {"id": 10, "nombre": "Empresa demo"},
  "proceso": {"id": 3, "codigo": "SUPERMERCADOS", "version": "1.0"},
  "requiere_input": true,
  "parametros": {},
  "input": {
    "archivo_id": 51,
    "nombre": "Productos de hoy.xlsx",
    "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "size_bytes": 20480,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "descarga": "/api/bot/ejecuciones/123/input"
  },
  "resultado": null,
  "error": null
}
```

El hash del ejemplo es ilustrativo; en el servidor se calcula sobre los bytes reales. Fechas y horas del contrato, cuando se devuelvan, usan ISO 8601 en UTC. La interfaz las presenta en America/Santiago.

Reglas de respuesta:

- Los dos IDs son iguales al de la URL; nunca crear ni devolver otro registro.
- `requiere_input` es booleano; si es true, `input` es obligatorio y el archivo debe estar disponible.
- Si es false, `input` es null y se envían los parámetros necesarios para el proceso.
- La ruta de descarga es relativa al mismo backend y no contiene tokens en la URL.
- En COMPLETADA, `resultado` incluye el ID, nombre y SHA-256 del reporte confirmado. En ERROR, `error` incluye código y mensaje sanitizado.
- Un ID ajeno o inexistente devuelve 404 sin exponer información de otra empresa. Un estado existente puede consultarse, pero no iniciarse si no está EN_COLA.

## Inicio atómico de la ejecución

Endpoint a adaptar: `POST /api/bot/ejecuciones/siguiente`. Encabezados de autenticación, CSRF e `Idempotency-Key`. El ID viaja en el cuerpo, no en la URL:

```json
{"solicitud_id": 123, "ejecucion_id": 123}
```

El operador decide dónde lanzar el bot. Al comenzar, el bot notifica al backend mediante este POST que está iniciando el ID seleccionado; no envía información del equipo. La identidad proviene de la sesión y clave. Ambos IDs del cuerpo son obligatorios y deben coincidir; si faltan, difieren o no son enteros positivos, responder 400 sin cambiar ningún estado. El servidor debe ejecutar dentro de una transacción:

1. Buscar y bloquear directamente el registro 123, sin seleccionar otra solicitud.
2. Comprobar acceso de la identidad, proceso activo, input listo y estado EN_COLA.
3. Cambiar ese registro a EN_PROCESO y registrar identidad, hora de inicio y resultado de idempotencia.
4. Confirmar la transacción antes de responder.

Respuesta 200: el mismo sobre de la consulta, con EN_PROCESO y ambos IDs iguales al ID enviado. Un ID inexistente o ajeno devuelve 404; uno no habilitado devuelve 409. Este contrato no devuelve 204 ni busca la última solicitud cuando no puede iniciar el ID recibido. Si otro operador cambia la cola, no tomar otra solicitud. Si otro bot ya empezó ese ID, devolver 409 salvo un reintento idempotente de la misma operación autorizada.

El estado intermedio de procesando no crea una “reserva” nueva del cliente: confirma que el bot comenzó la solicitud que el operador ya había seleccionado. El bot espera la confirmación 200 antes de descargar el input o ejecutar el negocio. El control atómico evita dos comienzos simultáneos del mismo ID.

## Descarga del input cuando corresponde

`GET /api/bot/ejecuciones/123/input`, con sesión y clave del bot autorizado que inició esa ejecución. Se descarga **después de confirmar `/siguiente` con ese ID y antes del procesamiento**.

La respuesta 200 contiene bytes del archivo, no un JSON ni Base64. Incluir:

```text
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Content-Length: 20480
Content-Disposition: attachment; filename="entrada.xlsx"
X-Content-SHA256: <hash hexadecimal de los bytes reales>
Cache-Control: no-store
```

El bot descarga por streaming en una carpeta exclusiva de ese ID, usa un archivo parcial y verifica tamaño y SHA-256 antes de darlo por completo. No confía en el nombre original para decidir rutas locales. `X-Content-SHA256` es una cabecera acordada de esta API, no una afirmación de que el backend actual ya la emita.

Fijar inicialmente 20 MiB por archivo, con límites adicionales de contenido expandido XLSX, filas, columnas y tiempo de validación. El input queda inmutable al liberar la solicitud; para cambiarlo, cancelar según las reglas y crear otra solicitud seleccionada por el operador.

Si el proceso requiere input y falta el archivo, `/siguiente` devuelve 409 `INPUT_NO_DISPONIBLE`, conservando EN_COLA. Si el proceso no requiere input, el bot no llama a `/input`; una llamada indebida devuelve 409 `INPUT_NO_REQUERIDO`. Después del inicio, un fallo persistente de descarga se notifica para ese mismo ID.

No se requiere copiar manualmente el Excel. El backend conserva su asociación a la solicitud y el bot obtiene una copia temporal de trabajo.

## Procesos sin Excel y formatos por bot

Ejemplo de consulta para supermercados sin archivo:

```json
{
  "solicitud_id": 124,
  "ejecucion_id": 124,
  "estado": "EN_COLA",
  "empresa": {"id": 10, "nombre": "Empresa demo"},
  "proceso": {"id": 3, "codigo": "SUPERMERCADOS", "version": "1.0"},
  "requiere_input": false,
  "parametros": {"producto": "leche", "supermercado": "unimarc", "marca": "Colun"},
  "input": null,
  "resultado": null,
  "error": null
}
```

Los parámetros se validan por proceso. No aceptar comandos, rutas locales o código para ejecutar desde un JSON. El bot nunca debe usar el contenido para elegir un comando de sistema arbitrario.

| Bot o modo | Entrada actual | Qué acordar con la web |
|---|---|---|
| Supermercados | Excel, o productos por parámetros si no requiere archivo. | Definir producto, supermercado, marca y filtros de precio. |
| ACME Work Items | Excel de filtros. | Mantener la plantilla y el código de proceso de ese modo. |
| ACME reportes mensuales | Excel de solicitudes mensuales. | Registrar un código coherente con `--proceso descargar-reportes` si se separan los modos en catálogo. |
| Vencimientos | Archivo de inventario; CSV también disponible en CLI local. | Mantener la plantilla; FECHA_PROCESO y DIAS_ALERTA continúan en Monitor. |

Para la primera versión web, utilizar XLSX: el backend actual de subida admite ese formato. XLSM/CSV requieren ampliar y validar el contrato de subida antes de habilitarlos. La admisión local de esos formatos no significa que la plataforma ya los reciba.

El backend actual valida parámetros previamente definidos y limita TEXTO a 500 caracteres. Para múltiples productos como arrays estructurados, diseñar su validación JSON por proceso; no asumir que enviarlos como TEXTO evita ese límite. Los códigos de proceso utilizados en los ejemplos son propuestas, no registros ya creados.

## Entrega del resultado

`POST /api/bot/ejecuciones/123/reporte`, con sesión, clave, CSRF e `Idempotency-Key`. Utilizar multipart con dos campos:

| Campo | Contenido |
|---|---|
| `archivo` | Archivo binario XLSX, CSV, PDF, ZIP, TXT o JSON según lo permitido para el proceso. |
| `metadatos` | Texto JSON UTF-8 con IDs, resumen y datos de integridad. |

Ejemplo del campo `metadatos`:

```json
{
  "solicitud_id": 123,
  "ejecucion_id": 123,
  "ejecucion_bot_id": "11111111111141118111111111111111",
  "estado": "COMPLETADA",
  "resumen": {"procesados": 20, "correctos": 19, "errores": 1},
  "export": {
    "nombre": "resultado_06112026-130000.zip",
    "content_type": "application/zip",
    "size_bytes": 40960,
    "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}
```

COMPLETADA significa que el bot terminó y entregó su reporte. El resumen conserva los errores por ítem que no impidieron finalizar según las reglas del proceso. ERROR se utiliza cuando no pudo finalizar el proceso. La web no debe deducir éxito de negocio únicamente del HTTP 200.

El servidor valida ambos IDs contra la URL, el dueño de la ejecución y el estado EN_PROCESO. Calcula tamaño y hash de los bytes recibidos; no confía en el hash declarado. Guarda el reporte privado y su relación antes de marcar COMPLETADA.

Respuesta 200:

```json
{
  "solicitud_id": 123,
  "ejecucion_id": 123,
  "estado": "COMPLETADA",
  "repetida": false,
  "reporte": {
    "archivo_id": 91,
    "nombre": "resultado_06112026-130000.zip",
    "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "descarga": "/api/bot/ejecuciones/123/reporte"
  }
}
```

Añadir `GET /api/bot/ejecuciones/123/reporte` para recuperación autorizada del archivo confirmado. El GET de detalle también debe ofrecer su hash, permitiendo confirmar una entrega después de un timeout sin repetir el negocio.

Los bots actuales envían `archivo` y llevan la correlación dentro de `reportes/ejecucion.json` en el ZIP. Para este contrato objetivo deben añadir `metadatos` como campo multipart y leer la nueva confirmación. No quitar la compatibilidad actual antes de desplegar los clientes adaptados, o versionar explícitamente la transición.

Ejemplo Python de la forma de enviar el archivo, suponiendo una sesión y encabezados ya válidos:

```python
with open(ruta_reporte, "rb") as archivo:
    respuesta = sesion.post(
        url_api + "/bot/ejecuciones/123/reporte",
        data={"metadatos": json.dumps(metadatos)},
        files={"archivo": (nombre_reporte, archivo, "application/zip")},
        headers=encabezados_autenticacion_csrf_idempotencia,
        timeout=(10, 60),
        allow_redirects=False,
    )
```

La biblioteca calcula el boundary multipart. No establecer manualmente un Content-Type `application/json` para esta petición. No incluir el reporte completo en Base64 dentro del JSON.

## Notificación de error

`POST /api/bot/ejecuciones/123/error`, con los mismos controles de autenticación, CSRF e idempotencia. Cuerpo:

```json
{
  "solicitud_id": 123,
  "ejecucion_id": 123,
  "ejecucion_bot_id": "11111111111141118111111111111111",
  "codigo": "INPUT_INVALIDO",
  "mensaje": "El archivo no contiene la columna producto.",
  "reintentable": false
}
```

Respuesta 200: los mismos IDs, estado ERROR y `repetida=false`. El backend persiste el fallo y genera la notificación una sola vez.

No enviar contraseñas, cookies, claves, SQL, trazas completas ni rutas personales en `mensaje`. `reintentable` describe el fallo; no autoriza al bot a repetir automáticamente el procesamiento. Una ejecución que ya llegó a COMPLETADA no debe cambiar a ERROR por una petición atrasada.

## Idempotencia y respuestas inciertas

La cabecera `Idempotency-Key` es parte del contrato propuesto de esta API. Usar una clave exclusiva por operación de inicio, reporte o error, guardada en el journal antes del envío. Reutilizarla solo para reintentar esa misma operación, con el mismo contenido.

El backend debe guardar, de forma coherente con el cambio de estado, la identidad, solicitud, operación, clave, huella del contenido y respuesta confirmada. Repetir la misma clave/contenido devuelve la respuesta original con el mismo ID sin efectos adicionales. Cambiar el contenido con esa clave devuelve 409 `IDEMPOTENCIA_CONFLICTO`.

Conservar los registros de idempotencia durante toda la ejecución y la ventana de recuperación acordada. Aunque una clave expire, los estados finales deben seguir impidiendo ejecutar de nuevo el mismo registro.

| Situación | Conducta del bot y del servidor |
|---|---|
| Se pierde la respuesta de inicio | Consultar el mismo ID y reintentar el inicio con su misma clave. No elegir otra solicitud. |
| Se interrumpe la descarga | Puede reintentar GET del mismo input y comprobar hash antes de procesar. |
| Se pierde la respuesta de reporte | Consultar el mismo ID; confirmar COMPLETADA y hash o reintentar exactamente el mismo reporte con la misma clave. |
| El bot cae durante el negocio | Revisión o recuperación específica del proceso. La idempotencia HTTP no garantiza que repetir el negocio sea seguro. |
| Llega reporte y error concurrentemente | Una transición final válida; la otra devuelve conflicto. No sobrescribir resultados finales. |

## Errores HTTP acordados

Mantener una envoltura uniforme:

```json
{
  "error": {
    "codigo": "ESTADO_NO_EJECUTABLE",
    "mensaje": "La solicitud no está habilitada para iniciar.",
    "solicitud_id": 123
  }
}
```

| HTTP | Código de ejemplo | Qué debe hacer el bot |
|---|---|---|
| 400 | `SOLICITUD_INVALIDA` | Informar datos inválidos; no ejecutar ni elegir otra orden. |
| 401 | `SESION_EXPIRADA` | Iniciar sesión una vez; repetir solo la petición HTTP con su clave de idempotencia. |
| 401 | `CREDENCIALES_INVALIDAS` o `API_KEY_INVALIDA` | Detener; corregir credenciales. No entrar en un bucle de login. |
| 403 | `CSRF_INVALIDO` | Obtener CSRF otra vez y repetir la petición una sola vez. |
| 403 | `PERMISO_DENEGADO` | Detener; renovar sesión no concede permisos. |
| 404 | `SOLICITUD_NO_ENCONTRADA` | No existe o no es visible; no revelar datos de otra empresa. |
| 409 | `ESTADO_NO_EJECUTABLE`, `INPUT_NO_DISPONIBLE` o `IDEMPOTENCIA_CONFLICTO` | Revisar el ID y estado; no procesar otra solicitud. |
| 413 / 415 / 422 | Archivo demasiado grande, formato no admitido o contenido inválido. | Rechazar entrada; no procesarla. |
| 429 | `DEMASIADAS_SOLICITUDES` | Respetar Retry-After; conservar ID y claves. |
| 500 / 503 o timeout | `ERROR_INTERNO` / `SERVICIO_NO_DISPONIBLE` | Consultar estado antes de repetir POST con efectos. |

Los códigos específicos de sesión/clave son nuevos: hoy el backend utiliza códigos más generales y los bots tratan un 401 en `/bot/` como rechazo de la clave. Actualizar ambos lados en la misma fase. GET por ID y POST `/siguiente` con ID obligatorio no devuelven 204; utilizan 404/409 según el caso. El 204 de “sin trabajo” pertenece únicamente al comportamiento heredado de `/siguiente`, que este contrato reemplaza.

## Persistencia y notificaciones

La finalización debe garantizar que archivo, relación con la solicitud y estado son coherentes. Si el almacenamiento falla, no marcar COMPLETADA. Si hay un archivo temporal sin registro confirmado, eliminarlo mediante una limpieza de huérfanos controlada.

Persistir el evento de notificación junto con la transición final y publicarlo después del commit, o usar un outbox transaccional. Deduplicar por solicitud y transición para que un reintento de reporte no genere dos avisos. El bot informa a la API; la web decide sus notificaciones, SSE y mensajes a usuarios.

Guardar los archivos privados, con nombres internos generados por el servidor y descarga autorizada. No extraer ZIP sin límites ni ejecutar su contenido. Aplicar límites de archivos expandidos y protección de fórmulas en las salidas destinadas a hojas de cálculo.

## Trabajo del desarrollador de los bots

El plan no queda completado únicamente habilitando rutas. Se requiere:

- Cambiar la consulta al nuevo GET por ID y adaptar el comienzo en `/siguiente` para enviar obligatoriamente `solicitud_id` y `ejecucion_id`; retirar la selección heredada de trabajo.
- Enviar CSRF en los POST del bot, reconocer los códigos de sesión/clave y conservar archivos/cuerpos al reintentar.
- Persistir claves de idempotencia por operación sin alterar el ID de solicitud.
- Leer `requiere_input`, tamaño y hash desde el sobre y validar la descarga antes de procesar.
- Enviar `metadatos` y archivo, y confirmar IDs/estado/hash de la respuesta.
- Mantener el bloqueo y journal, corregir sus rutas no confinadas y neutralizar fórmulas de reportes según la auditoría.

## Fases de implementación

| Orden | Responsable | Entregable verificable |
|---|---|---|
| 1 | Web y bots | Aprobar este contrato, ejemplos, estados, tipos y límites. Registrar procesos con sus códigos reales. |
| 2 | Backend | Cuentas de servicio, claves segmentadas, GET del bot e inicio atómico por ID, con pruebas de aislamiento. |
| 3 | Backend | Descarga privada con hash, reporte/error idempotentes y notificaciones tras confirmar estado. |
| 4 | Bots | Adaptadores del contrato objetivo, errores, CSRF, integridad y recuperación; conservar CLI por ID. |
| 5 | Ambos | Probar en base y almacenamiento de pruebas, primero con negocio simulado y luego con una ejecución real autorizada por bot. |
| 6 | Operación | HTTPS, secretos en el proveedor acordado, cuenta de ejecución con permisos mínimos, monitoreo y revocación. Desactivar rutas/credenciales globales de compatibilidad. |

SQL Server y sus credenciales pertenecen al backend. No entregarlas a los bots como credenciales de API ni confundirlas con la cuenta de servicio web.

## Pruebas de aceptación

1. Sin sesión, sin clave o con clave de otro bot, no se accede a metadata, input, inicio o resultados fuera de alcance.
2. La cuenta de servicio no puede crear/liberar solicitudes ni administrar usuarios.
3. Un ID PENDIENTE, CANCELADA, ERROR o COMPLETADA no inicia una ejecución nueva.
4. Dos inicios simultáneos de un ID generan un solo comienzo; cambiar la cola no afecta a otro ID.
5. Solicitud, respuesta, descarga, reporte y error conservan el mismo número de ID.
6. `requiere_input=true` exige archivo íntegro; false exige `input=null` y parámetros válidos cuando el proceso los necesita.
7. La descarga incompleta o con hash incorrecto no inicia negocio.
8. Un timeout de inicio/reporte y su reintento idempotente no duplican ejecución, archivo ni aviso.
9. El reporte queda descargable y con hash confirmado antes de COMPLETADA.
10. Un fallo deja ERROR en el mismo ID y una sola notificación; ninguna petición atrasada revierte un estado final.
11. La expiración de sesión permite relogin sin repetir el RPA; una clave inválida o permiso insuficiente detiene el bot.
12. Excel, parámetros sin archivo, errores por ítem, archivos grandes y contenido inválido se prueban en cada proceso que los admite.

Hasta completar estas fases, los bots actuales conservan su flujo compatible por ID y validación antes/después de `/siguiente`, con la limitación de concurrencia y la clave global documentadas en la auditoría. El contrato objetivo sustituye esa selección heredada por el inicio del ID elegido y exige permisos desde el servidor. Adaptar también la acción humana de liberar solicitudes y las restricciones de persistencia heredadas a este contrato; quitar campos de los ejemplos no cambia por sí solo las obligaciones del backend actual.
