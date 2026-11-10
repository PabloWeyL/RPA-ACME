# RPA ACME

Automatiza dos tareas de ACME System 1: consultar Work Items y descargar
reportes mensuales. Recibe una ejecucion desde Weyland Automate y entrega
sus archivos de resultado al backend.

## Preparacion

Requiere Python 3.11 o posterior, Node.js 22 o posterior y Google Chrome.
Desde esta carpeta:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Completar `.env` con las credenciales de ACME, URL del backend, correo/password,
API key, ID de VM y codigo de proceso registrados. Seleccionar
`ACME_PROCESO_WEB=consultar` o `descargar-reportes`, segun ese codigo.
En produccion usar HTTPS y un `BACKEND_TRABAJOS_DIR` privado fuera del repositorio.

## Ejecucion

```powershell
.\.venv\Scripts\python.exe main.py
```

Con el entorno activo basta `python main.py`. Tambien puede abrirse `ejecutar.bat`.
El arranque valida configuracion, dependencias, directorios y SQLite existente.
Luego inicia sesion, consulta `/siguiente`, descarga el Excel asignado, ejecuta
el proceso y envia el ZIP al backend. Un 204 termina sin trabajo, con salida 0.
No reutiliza solicitudes antiguas ni carga otros Excel locales en el modo web.

El Excel de consulta utiliza columnas `tipo` y `estado`. Para reportes mensuales
utiliza `proveedor_taxid`, `anio`, `mes_desde` y `mes_hasta`.

Otros comandos:

```powershell
python main.py api-verificar
python main.py web-procesar --proceso consultar
python main.py web-procesar --proceso descargar-reportes
python main.py procesar
```

`api-verificar` comprueba login/perfil. `web-procesar` entra directamente al flujo
web. `procesar` ejecuta el modo local con los Excel compatibles de `input/`.
Tambien se conservan los comandos locales `consultar` y `descargar-reportes`.

## Resultados y correo

Los archivos web se conservan bajo `BACKEND_TRABAJOS_DIR`, separados por ID.
Los resultados locales quedan en `reportes/` y las descargas en `descargas/`.
No borrar checkpoints de entregas inciertas para forzar otra ejecucion.

Para activar SMTP, completar credenciales y listas antes de configurar
`MODULO_CORREO=true`. Inicio y resultados llegan a ambas listas; fallas solo
a `LISTA_DISTRIBUCION_ERROR`. En el modo web, el aviso final se envia tras la
confirmacion del backend y adjunta el mismo archivo entregado.

## Publicacion

`.env`, inputs, media, bases, logs, temporales y exportaciones quedan excluidos.
Las plantillas de credenciales se comparten vacias. Activar los controles locales:

```powershell
git config core.hooksPath .githooks
python tools/verificar_publicacion.py
```

Los hooks locales y el workflow de GitHub complementan la revision humana.
Esta entrega operativa no incluye tests, simuladores ni documentacion adicional.


## Correo controlado desde Weyland

La configuracion de habilitacion de empresa/proceso puede incluir
`{"notificar_correo": false}` para suprimir inicio, resultados y fallas por SMTP.
`true` permite esos avisos si el correo local esta habilitado y configurado.
Si falta el campo, se conserva la configuracion local. Debe ser booleano JSON,
no el texto "false". El envio del resultado/error al backend sigue siendo obligatorio.

El backend actual ya permite guardar ese objeto mediante
`PUT /api/procesos/{proceso_id}/empresas/{empresa_id}` con
`{"activo": true, "configuracion": {"notificar_correo": false}}`, usando permisos
de gestion de procesos. Conservar las otras claves de configuracion al actualizarlo.
No se agrego un endpoint ni un control visual nuevo al backend.

El bot lee la bandera en la respuesta de `/siguiente` y la conserva para esa
ejecucion y sus recuperaciones. Los cambios web se aplican a las siguientes
asignaciones. Los journals antiguos o una toma recuperada sin su configuracion
no autorizan correos automaticos; el negocio y la entrega web pueden continuar.
En modo web no se drena una cola global de correos antes de tomar el trabajo.
Los comandos manuales de correo, donde existan, siguen siendo acciones del operador.


## Diagnostico y recuperacion de correo

### Diagnostico y codigos de salida

`python main.py` y `ejecutar.bat` devuelven el mismo codigo:

| Codigo | Significado |
| --- | --- |
| 0 | Ejecucion completada, sin solicitudes o comando de consulta correcto. |
| 1 | Fallo del procesamiento u otro fallo operativo. |
| 2 | Configuracion, dependencias o argumentos invalidos. |
| 3 | Autenticacion o permisos rechazados (HTTP 401/403). |
| 4 | Backend no disponible, peticion rechazada o asignacion sin confirmar. |
| 5 | Proceso, contrato o checkpoint incompatible. |
| 6 | Input invalido o descarga fallida. |
| 7 | Export pendiente o entrega del resultado/fallo sin confirmar. |
| 8 | Procesamiento interrumpido; requiere revision antes de repetirlo. |
| 9 | Gestion de correo fallida o destinatarios pendientes de entrega/revision. |

Los mensajes publicos no muestran credenciales ni cuerpos HTTP. Una entrega
sin confirmacion conserva el checkpoint y no se reenvia automaticamente.
Un resultado confirmado por la web mantiene salida 0 aunque SMTP tenga pendientes;
la recuperacion de correo se gestiona por separado y no repite el negocio.

### Recuperar mensajes de una ejecucion web

El correo de `cola_actual` se conserva por ejecucion. Estos comandos usan
`BACKEND_TRABAJOS_DIR` y `BACKEND_MAQUINA_ID` para localizar la cola, sin llamar
al backend. El ID corresponde a la ejecucion recibida de `/siguiente`.

```powershell
python main.py correos-estado --id-ejecucion 123
python main.py correos-reintentar --id-ejecucion 123 --id-correo ID_DEL_CORREO
python main.py correos-resolver --id-ejecucion 123 --id-correo ID_DEL_CORREO --destinatario persona@ejemplo.test --entregado
```

La consulta de estado y la resolucion manual no requieren obtener secretos SMTP.
El reintento usa las credenciales actuales de correo; no consulta Monitor ni
ejecuta el negocio. Estos comandos recuperan la outbox del contrato `cola_actual`;
los avisos directos del modo local anterior conservan su comportamiento.

El MIME y los adjuntos se conservan en SQLite. El reintento con ID actua solo
sobre ese mensaje y devuelve 9 mientras queden destinatarios pendientes o que
requieran revision. Sin ID, revisa todos los mensajes de la cola seleccionada.
Los destinatarios ya aceptados no se repiten. `AGOTADO` se reactiva solo con
un reintento explicito con ID. `INCIERTO` nunca se reenvia automaticamente:
tras comprobar con el proveedor SMTP si se acepto el correo, el operador usa
`--entregado` para cerrarlo o `--no-entregado` para permitir su posterior reintento.
Son alternativas; no deben ejecutarse ambas para el mismo destinatario.

Los comandos de correo no vuelven a procesar datos ni a subir exports a la web.
Una ejecucion confirmada por el backend conserva salida 0 aunque el correo
quede pendiente. La politica web se aplica al encolar avisos; la recuperacion
manual actua sobre mensajes ya preparados y no drena otras ejecuciones.
