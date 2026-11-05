# CONTROL_VENCIMIENTOS

## Estado y proposito

Estado del proyecto: **DESARROLLO**. Estandar objetivo: Propuesta Weyland
v.0.0.1 facilitada por el usuario. Detecta vencimientos por codigo/lote desde
Excel/CSV y genera evidencia por ejecucion. No es un portal ni un servicio 24/7.
No esta validado contra los servicios Azure reales de la organizacion.

## Entry point y arquitectura

`main.py` muestra inicializacion → secretos → Monitor → BD → ID → carpetas →
Modulo 1 → reportes → ZIP → FINALIZADO, con error critico y cierre controlados.
`configuracion.py` raiz valida tecnica; `config/configuracion.py` es solo un
alias de importacion anterior. No agregar logica de negocio a configuracion.

- `integraciones/inventario.py`: reglas puras y modelo.
- `entradas.py`: validacion de formato y confirmaciones.
- `proceso.py`: evaluacion pura de un registro.
- `ejecucion.py`: snapshots, puntos de persistencia, reanudacion, reportes.
- `nube.py`: Key Vault, SQL cifrado y clasificacion de errores transitorios.
- `monitor.py`: contrato propuesto del SP de parametros, no cola de trabajos.
- `correo.py`: aviso inmediato de error critico.
- `config/base_datos.py`: operaciones equivalentes SQLite/Azure SQL.
- `automatizacion/`: escritura atomica, trazabilidad, bloqueo, respaldo y reintentos.

Modulo funcional numerado: 1 Procesamiento, bandera `MODULO_PROCESAMIENTO`.
Reportes y respaldo son infraestructura obligatoria antes de FINALIZADO.

## Configuracion y secretos

Leer exclusivamente el .env del proyecto y entorno. Validar obligatorios; sin
fallbacks silenciosos para rutas, servidores, reintentos, nombres de secretos o
parametros funcionales. .env solo tecnico y referencias SECRET_*; valores reales
solo en Key Vault y memoria. Produccion usa identidad administrada. No escribir
ni loguear valores de secretos. Conservar redaccion del traceback.

Monitor proporciona FECHA_PROCESO y DIAS_ALERTA. No moverlos a .env. Conexion
Monitor independiente de BD propia, ambas Azure SQL en produccion. Desarrollo
real usa Azure SQL para Monitor y SQLite para persistencia del proceso.
`--prueba-local` es un doble explicito solo en DESARROLLO, SQLite, sin correo;
requiere JSON de parametros y nunca debe habilitarse en produccion.

## Tablas, estados y reanudacion

Constantes en base_datos.py:
QUEUE_CONTROL_VENCIMIENTOS: PENDIENTE/EN_EJECUCION/FINALIZADO/ERROR.
DETAILS_CONTROL_VENCIMIENTOS: PENDIENTE/PROCESADO/ERROR/OMITIDO.
Los estados de negocio son distintos de los estados operacionales.

Persistir cada item inmediatamente. La clave id_ejecucion/fila evita reencolar
la misma fila. Nunca repetir items PROCESADO en reanudacion. ID vacio crea nueva
queue; ID definido retoma la misma y exige mismos parametros y raices. Una
interrupcion externa deja EN_EJECUCION para revision/reanudacion manual.
No crear un estado REPROCESO. Datos corregidos usan una nueva ejecucion.

## Archivos y rutas

SQLite local en raiz, nombre DB_SQLITE sin ruta. Las tablas anteriores y los
snapshots de temporales/inventario.db se conservan sin cambios retrospectivos.
Operativo: RUTA_BASE/CONTROL_VENCIMIENTOS/AAAA/MM/DD/TIPO/ID.
Fecha de carpeta = inicio real original, no fecha funcional ni reanudacion.
Historico: RUTA_HISTORICO/CONTROL_VENCIMIENTOS/respaldo_...zip, raiz independiente.
El ZIP contiene solo input/procesados/reportes de ese ID y precede FINALIZADO.
Verificar las huellas de las copias; nunca desactivar esa validacion para reanudar.

Retencion operativa 6 meses e historica 18 meses: documentada, sin limpieza de
datos automatica. Logs mensuales con limpieza segura solo de logs propios de
seis o mas meses. No borrar bases, reportes anteriores ni snapshots del usuario.

## Reglas para agentes y puntos fragiles

Mantener espanol, simplicidad y estructura minima. No crear capas o carpetas
vacias por apariencia. Las modificaciones no deben cambiar los robots vecinos.
No realizar conexiones productivas ni desplegar tareas/recursos sin que el
usuario los solicite. El contrato real de Monitor aun no existe: no inventar SP,
servidores, credenciales ni resultados de validacion real.

Conservar rollback/cierre de conexiones y timeout externo. No introducir retry
sobre todo el pipeline ni retry ciego de operaciones con efectos inciertos.
Errores por item continuan cuando es seguro; criticos persisten ERROR, avisan y
salen. Un fallo de correo no sustituye al error original. Exit 0 es tecnico,
no significa que todos los productos esten vigentes.

## Ejecutar y validar

Python validado: 3.14.7 Windows. .venv propio y requirements fijados.
`python -B -m pytest pruebas -q -p no:cacheprovider` desde ese .venv.
Prueba local: `main.py procesar --prueba-local --parametros-prueba pruebas/parametros_ejemplo.json`.
Leer README para preparar VM, Key Vault, Monitor, ODBC, correo y reanudacion.
Registrar limitaciones reales; no declarar produccion validada basandose en mocks.

## Desviaciones y pendientes

Nombre de carpeta historico en minusculas conservado; despliegue objetivo
C:\Procesos\CONTROL_VENCIMIENTOS. No hay metadatos Git propios ni ramas creadas.
Compatibilidad de imports antigua conservada, no duplicacion de codigo.
Contrato propuesto Monitor (nombre, valor) por acordar y desplegar.
Backend web/reserva de solicitudes/entrega de resultados no implementados;
la plataforma debe definirlos. Watchdog: no aplica, proceso batch.
Pendientes reales: Key Vault, Azure SQL/Monitor y SMTP autorizados; pruebas en
VM/Task Scheduler. No promover hasta completar esa validacion.
