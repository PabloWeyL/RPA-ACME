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
el proceso y envia el ZIP al backend. Un 204 termina sin trabajo, con salida 1.
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
