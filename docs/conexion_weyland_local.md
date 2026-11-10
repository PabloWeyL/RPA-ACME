# Conexion comun a Weyland Automate

Este bot utiliza la version 2.0.0 del paquete `integraciones/weyland`, identica a la de los otros tres bots. La fuente reutilizable y la guia estan en la carpeta hermana `Integracion-Weyland-Automate/README.md`. Cada proyecto conserva su propia copia para poder instalarse de forma independiente.

Comandos: `ejecutar.bat api-verificar` comprueba login y perfil; `ejecutar.bat web-procesar --id-solicitud 123` procesa solo ese ID. ACME permite ademas `--proceso descargar-reportes`. Los IDs de los ejemplos deben reemplazarse por valores registrados.

La URL, codigo del proceso y proveedor de secretos se configuran en el proyecto. No se requiere un ID de equipo para la conexion. Vencimientos conserva credenciales API en Key Vault, parametros de negocio en Monitor y conexiones independientes.

Funciones comunes: crear_cliente, verificar, consultar, validar, iniciar, descargar, entregar y fallar. La coordinacion, idempotencia, integridad y registro local pertenecen al paquete comun; `integraciones/weyland_worker.py` adapta los comandos/artefactos particulares del negocio. Godzilla utiliza su adaptador `integraciones/worker.py`.

1. Autenticar con correo/password, sesion y CSRF.
2. Consultar GET `/api/bot/ejecuciones/{id}` sin mutar la solicitud.
3. Exigir EN_COLA, ID y codigo correctos; POST `/api/bot/ejecuciones/siguiente` con solicitud_id y ejecucion_id iguales.
4. Esperar EN_PROCESO; descargar input solo si se requiere y verificar tamano y SHA-256.
5. Ejecutar negocio una vez; enviar ZIP y metadatos JSON al reporte del mismo ID, o notificar ERROR.

No existe fallback a la seleccion heredada ni a la ultima solicitud. Las confirmaciones deben conservar ambos IDs y el reporte debe confirmar su hash. Las claves de idempotencia se guardan antes de enviar POST; una entrega pendiente puede reenviarse sin repetir el negocio.

**Pendiente en el servidor:** el backend actual requiere adaptar siguiente a IDs obligatorios, habilitar la consulta del bot por ID e implementar las confirmaciones e idempotencia del contrato. Mientras falte ese soporte, web-procesar se detiene sin tomar otra solicitud. api-verificar por si solo no valida esos requisitos. El backend no se modifica con esta migracion.

No borrar los journals anteriores: una ejecucion incierta bloquea la migracion hasta revision. El codigo nuevo no reanuda negocio desde EJECUTANDO ni sobrescribe un registro incompatible. En produccion, configurar BACKEND_TRABAJOS_DIR fuera del repositorio y permisos de acceso exclusivos de la cuenta operativa.
