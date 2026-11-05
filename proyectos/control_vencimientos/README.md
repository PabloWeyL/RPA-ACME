# ¿Qué hace este proceso?

**CONTROL_VENCIMIENTOS** revisa un inventario Excel o CSV para identificar qué
productos están vencidos, vencen hoy o están próximos a vencer. El control se
hace por producto y lote, porque dos lotes del mismo producto pueden tener
fechas diferentes.

## Resumen sencillo

| Qué recibe | Qué hace | Qué entrega |
|---|---|---|
| Excel o CSV con código, producto, lote, cantidad y fecha de vencimiento | Valida los datos y compara cada fecha con la fecha de control | Excel, CSV de alertas, listado de revisiones y respaldo ZIP |

## Cómo funciona, paso a paso

1. **Carga la configuración.** Comprueba rutas, módulos y conexiones necesarias.
2. **Obtiene los parámetros.** Monitor indica la fecha de control y cuántos días de anticipación se consideran alerta. Key Vault proporciona los secretos de conexión cuando se usan servicios reales.
3. **Prepara la ejecución.** Crea o retoma un identificador y conserva una copia verificable de la entrada.
4. **Revisa cada fila.** Valida los datos y clasifica el producto según su vencimiento.
5. **Guarda el avance.** Registra cada resultado para poder retomar una interrupción sin repetir los ya procesados.
6. **Entrega y respalda.** Genera los reportes, crea un ZIP de esa ejecución y después la marca como finalizada.

**Monitor** es el sistema que entrega los parámetros de negocio. **Key Vault**
es el servicio que guarda los secretos de acceso. El **identificador de
ejecución** permite relacionar el avance con los archivos de una misma corrida.

## Ejemplo de resultado

Si la fecha de control es el **4 de noviembre de 2026** y la alerta es de 30 días:

| Producto | Fecha de vencimiento | Resultado |
|---|---|---|
| Leche, lote A | 3 de noviembre de 2026 | Vencido |
| Yogur, lote B | 4 de noviembre de 2026 | Vence hoy |
| Queso, lote C | 15 de noviembre de 2026 | Próximo a vencer |
| Arroz, lote D | 1 de marzo de 2027 | Vigente |
| Producto sin fecha | Sin informar | Pendiente de revisión |

## Qué debe preparar la persona

Preparar el inventario con las columnas `codigo`, `producto`, `lote`, `cantidad`
y `fecha_vencimiento`. Una fecha vacía se envía a revisión; no se inventa.
Para las pruebas locales se utiliza un archivo de parámetros de ejemplo.
Para la operación real deben configurarse Monitor, Key Vault y las conexiones.

## Qué entrega y cómo se revisa

- Un Excel con hojas de inventario, alertas, revisión, errores y resumen.
- CSV separados con alertas, fechas por completar y errores de datos.
- Historial del estado de la ejecución y de cada fila.
- Un ZIP con las entradas, los archivos procesados y los reportes de esa corrida.

La persona puede completar `fecha_confirmada` en el archivo de revisiones y
aplicarlo en una nueva ejecución. El inventario original no se modifica.
Los archivos quedan en las rutas configuradas, separados por fecha e identificador.

## Qué ocurre si algo falla

Un dato inválido afecta a su fila y se registra para corregirlo. Un fallo que
impide continuar, como perder acceso a la base o no poder crear el respaldo,
marca la ejecución como error. Después de resolverlo, se puede reanudar por su
identificador. Una interrupción inesperada requiere revisión y reanudación manual.

Que el proceso termine correctamente significa que pudo realizar el control;
no significa que todos los productos estén vigentes. El reporte es el que
informa los vencidos y los datos que requieren atención.

## Situación actual e instrucciones técnicas

Estado: **DESARROLLO**. Se adopta como objetivo la propuesta de Estandar Weyland
`PROPUESTA_ESTANDAR_AUTOMATIZACIONES_v.0.0.1`. Este proyecto es independiente de
RPA-ACME y Bots-Dummies; esta adaptacion no cambia esos dos robots.

## Flujo general y ciclo de vida

El punto de entrada es `main.py`; el unico modulo funcional es:

1. **Procesamiento** (`MODULO_PROCESAMIENTO`): validar filas y clasificar por lote.

Reportes, respaldo y cierre son etapas obligatorias de infraestructura, no
modulos opcionales que puedan omitir el respaldo de los resultados.

Orden real: inicio tecnico con `ID:SIN_ID` → `.env` → validacion → Key Vault →
Monitor y parametros de negocio → BD propia → nueva ejecucion o reanudacion →
carpetas operativas → `EN_EJECUCION` → Modulo 1 → reportes → respaldo ZIP →
`FINALIZADO` → cierre. Un error critico posterior a crear el ID se registra como
`ERROR`, se notifica inmediatamente si SMTP esta disponible y devuelve salida 1.
El traceback completo queda en el log; la BD conserva un resumen sin secretos.

## Estructura

```text
CONTROL_VENCIMIENTOS/
  main.py
  configuracion.py
  ejecutar.bat
  .env                   # Local, ignorado por Git; sin secretos
  .env.example
  requirements.txt
  README.md
  AGENTS.md
  config/base_datos.py
  integraciones/
    inventario.py        # Reglas puras
    entradas.py          # Excel/CSV
    proceso.py           # Evaluacion por registro
    ejecucion.py         # Pipeline y reanudacion
    reportes.py          # Plantillas
    nube.py              # Key Vault y conexion SQL cifrada
    monitor.py           # Parametros de negocio
    correo.py            # Aviso de error critico
  automatizacion/
    excel.py             # Escritura atomica CSV/Excel
    operacion.py         # Carpetas, bloqueo y respaldo
    reintentos.py
    trazabilidad.py
  logs/
  pruebas/
  input/
  procesados/
  reportes/
  temporales/
```

Las carpetas minimas existen incluso vacias. Los paquetes adicionales agrupan
responsabilidades ya existentes. Se retiraron las carpetas vacias sin uso.
`config/configuracion.py` solo reexporta el modulo raiz para compatibilidad;
no contiene otra implementacion. No hay watchdog ni servidor web en este batch.

## Requisitos e instalacion

Validado en **Python 3.14.7, Windows**, con dependencias fijadas en
`requirements.txt` y un `.venv` propio. El procesamiento no necesita Chrome,
TagUI, Node ni OCR. Para Azure SQL/Monitor se requiere Microsoft ODBC Driver 18
for SQL Server instalado en Windows y conectividad TLS al servidor autorizado.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Solo si no existe ya un .env local:
Copy-Item .env.example .env
```

La carpeta actual conserva su nombre historico `control_vencimientos`; al
instalar en la VM se utiliza `C:\Procesos\CONTROL_VENCIMIENTOS`.
No copiar `.venv` entre maquinas: recrearlo con requirements.

## Configuracion tecnica y secretos

`.env` contiene exclusivamente configuracion tecnica y referencias `SECRET_*`.
No se carga el `.env` del proyecto padre. El entorno prevalece sobre `.env`.
Las variables obligatorias no tienen fallbacks productivos ni valores de reserva.

- `ENTORNO`: `DESARROLLO` o `PRODUCCION`.
- `NOMBRE_PROCESO`: siempre `CONTROL_VENCIMIENTOS`.
- `MOTOR_BD`: `sqlite` en desarrollo; produccion exige `azure_sql`.
- `DB_SQLITE`: solo el nombre del archivo, creado en la raiz del proyecto.
- `DB_SERVER`, `DB_NAME`: BD operacional propia cuando se selecciona Azure SQL.
- `RUTA_BASE`: raiz operativa. `RUTA_HISTORICO`: raiz independiente de respaldos.
- `RUTA_LOGS`: logs tecnicos locales.
- `EXCEL_ENTRADA`: ruta tecnica del inventario; las rutas relativas parten del proyecto.
- `MODULO_PROCESAMIENTO`: `true` o `false`, numerado en `.env`.
- `MAX_REINTENTOS`: intentos totales 1..10; `ESPERA_REINTENTO_SEGUNDOS`: espera fija
  0..300; `TIMEOUT_SEGUNDOS`: 1..300 para operaciones externas.
- `ID_EJECUCION`: opcional; vacio crea una nueva, definido reanuda esa ejecucion.

En produccion, las raices operativa e historica deben estar fuera del proyecto y
ser independientes. SQLite y `--prueba-local` se rechazan en produccion.

Key Vault: configurar `KEYVAULT_NAME` y los nombres de los secretos:
`SECRET_MONITOR_DB_USER`, `SECRET_MONITOR_DB_PASSWORD`; para Azure SQL propio,
`SECRET_DB_USER`, `SECRET_DB_PASSWORD`; para correo, `SECRET_SMTP_USER` y
`SECRET_SMTP_PASSWORD`. Los valores reales nunca se escriben en archivos.
La URL se construye desde el nombre. Se validan existencia y valor no vacio.

En la VM se usa identidad administrada; en desarrollo real,
`DefaultAzureCredential` permite la identidad de desarrollo autorizada. Asignar
solo lectura de los secretos necesarios. Las conexiones SQL usan cifrado y
validacion del certificado. Documentacion del proveedor:
[Key Vault Python](https://learn.microsoft.com/en-us/azure/key-vault/secrets/quick-create-python).

## Weyland Monitor: contrato propuesto, todavia no desplegado

Monitor es una **conexion Azure SQL independiente** de la BD propia, incluso en
desarrollo real. Configurar `MONITOR_DB_SERVER`, `MONITOR_DB_NAME`,
`MONITOR_SP_PARAMETROS` y `ODBC_DRIVER`.

Como aun no existe el procedimiento del usuario, el adaptador implementa este
contrato propuesto, que debera acordarse o ajustarse antes de produccion:

```sql
EXEC <MONITOR_SP_PARAMETROS> @nombre_proceso = ?
-- Parametro enlazado: CONTROL_VENCIMIENTOS
-- Columnas exactas, en este orden: nombre, valor
-- Filas requeridas:
-- FECHA_PROCESO | 2026-11-04
-- DIAS_ALERTA   | 30
```

El codigo no crea ni modifica el Monitor. No se aceptan parametros faltantes o
repetidos. `DIAS_ALERTA=0` es valido. La fecha funcional y los dias de alerta ya
no se leen de `.env` ni se sustituyen silenciosamente por el dia actual.

La plataforma web puede preparar el inventario y los parametros en Monitor y
solicitar al agente/planificador que ejecute `ejecutar.bat`. La reserva de
solicitudes, autenticacion del backend, entrega de reportes y callback de estados
**no estan implementados aqui**: requieren el contrato de la plataforma.
Monitor no se asume como una cola de ejecucion ni se inventan operaciones de
reserva o actualizacion. Este proyecto queda como batch invocable y trazable.

## Prueba controlada local

Para probar sin ninguna conexion Azure ni correo, utilizar explicitamente:

```powershell
.\.venv\Scripts\python.exe main.py procesar --prueba-local --parametros-prueba pruebas/parametros_ejemplo.json
```

Esta modalidad solo funciona en DESARROLLO, con SQLite y correo desactivado.
El JSON es un doble de prueba de Monitor, no una configuracion productiva.
El `.env` entregado contiene rutas locales de demostracion y referencias Azure
vacias: el comando normal fallara claramente hasta configurar las integraciones.
No es un dry-run sin escrituras: genera historial, reportes y un respaldo local.

Para crear una entrada vacia, sin abrir sistemas externos:

```powershell
.\.venv\Scripts\python.exe main.py plantilla input/mi_inventario.xlsx
```

## Inventario y revision

Columnas: `codigo`, `producto`, `lote`, `cantidad`, `fecha_vencimiento`.
Las primeras cuatro son obligatorias. Cada codigo/lote debe aparecer una sola
vez; lotes distintos del mismo codigo son registros diferentes. Consolidar las
cantidades duplicadas. Guardar identificadores como texto para conservar ceros.

Excel usa la hoja activa de XLSX/XLSM. CSV debe ser UTF-8 con coma, punto y coma
o tabulador. Fechas: celdas Excel de fecha, `AAAA-MM-DD`, `DD/MM/AAAA` o
`DD-MM-AAAA`; no se adivinan fechas parciales o anios de dos digitos. Cantidades
finitas no negativas y punto decimal, sin separadores de miles.

Clasificacion de negocio: `vencido` si la fecha es anterior a FECHA_PROCESO;
`vence_hoy` si coincide; `por_vencer` hasta DIAS_ALERTA inclusive; `vigente`
despues. Sin fecha: `pendiente_revision` y detalle operacional `OMITIDO`.
Una fila con dato invalido queda `ERROR`, sin abortar los otros items.

El reporte genera una hoja/CSV de revisiones con `fecha_confirmada`. Para aplicar
fechas revisadas crear una **nueva ejecucion** con `--revisiones archivo.csv`.
Las confirmaciones se enlazan por codigo/lote exactos. Para reanudar el mismo ID,
se conservan las copias y los parametros de la ejecucion original.

## Persistencia y reanudacion

Tablas nuevas centralizadas en `config/base_datos.py`:

- `QUEUE_CONTROL_VENCIMIENTOS`: `PENDIENTE`, `EN_EJECUCION`, `FINALIZADO`, `ERROR`.
- `DETAILS_CONTROL_VENCIMIENTOS`: `PENDIENTE`, `PROCESADO`, `ERROR`, `OMITIDO`.

Incluyen ID, fechas, mensaje de error, contexto/parametros y datos de cada item.
El ID pertenece solo a este proceso. La preparacion de items es idempotente y
sus resultados se confirman individualmente, antes del siguiente item.

```powershell
.\.venv\Scripts\python.exe main.py procesar --id-ejecucion <ID>
# En prueba local, agregar los mismos --prueba-local y --parametros-prueba.
```

Tambien puede definirse `ID_EJECUCION` en `.env`; la CLI prevalece. Solo los items
PENDIENTE se ejecutan al reanudar. Los ERROR/OMITIDO quedan para revision; corregir
el inventario y crear una nueva ejecucion para reprocesarlos. No se inventa un
estado REPROCESO. Un ID FINALIZADO es una operacion sin efectos adicionales.

Se verifican las huellas SHA-256 de las copias de entrada y se rechazan cambios.
Se puede reanudar aunque el original ya no exista si su copia confirmada existe.
Las raices y los parametros de negocio deben mantenerse iguales. Las carpetas
usan la fecha real original de ejecucion, no FECHA_PROCESO ni el dia de reanudacion.

El historial de las versiones locales anteriores permanece intacto en
`temporales/inventario.db`, con sus tablas y reportes originales. No se borran ni
se inventan estados retrospectivos para esos snapshots. Las nuevas ejecuciones
usan la BD configurada `DB_SQLITE` y el modelo QUEUE/DETAILS.

## Archivos y respaldo

```text
RUTA_BASE/CONTROL_VENCIMIENTOS/AAAA/MM/DD/
  input/ID_EJECUCION/
  procesados/ID_EJECUCION/
  reportes/ID_EJECUCION/
  temporales/ID_EJECUCION/
```

Los nombres generados incluyen el ID. El ZIP contiene exclusivamente input,
procesados y reportes de ese ID:

`RUTA_HISTORICO/CONTROL_VENCIMIENTOS/respaldo_CONTROL_VENCIMIENTOS_ID_AAAA_MM_DD.zip`.

Se valida el ZIP y se confirma antes de FINALIZADO. Si falla, la ejecucion queda
ERROR y puede reanudar el cierre sin repetir items. No se copian entradas
productivas a las carpetas locales del repositorio en produccion.

Retencion objetivo: operativo 6 meses, ZIP historico 18 meses. La limpieza de
datos queda documentada, sin borrado automatico, conforme a la propuesta.
Solo la limpieza de logs propios de seis o mas meses esta implementada.

## Logging, errores y correo

Formato en consola y archivo:

`AAAA-MM-DD HH:MM:SS [NIVEL] [CONTROL_VENCIMIENTOS] [ID:ID_EJECUCION] [MODULO] Mensaje`.

Antes del ID se utiliza SIN_ID. Log mensual
`logs/control_vencimientos_AAAA_MM.log`, sin rotacion por tamanio. Se filtran los
secretos tambien en los tracebacks. Las excepciones usan `logger.exception()`.

Correo: `MODULO_CORREO=true` es obligatorio en produccion. Configurar servidor,
puerto TLS 465 o STARTTLS 587 y `LISTA_DISTRIBUCION_ERROR`; las credenciales vienen
de Key Vault. El aviso inmediato incluye proceso, ID o SIN_ID, modulo, entorno,
fecha, maquina e intentos. Un fallo de correo se registra sin reemplazar el error
original. Si la inicializacion falla antes de tener credenciales disponibles,
queda el log y no se simula un envio.

Los reintentos se aplican a lecturas/conexiones externas clasificadas como
transitorias y registran N/M. No se reintentan autenticacion, datos o config
invalidos, ni se repite todo el pipeline por fallo de un reporte.

Exit code `0`: finalizacion tecnica correcta, incluidos vencidos, errores de
items y registros OMITIDO. Exit code `1`: error critico. Argparse usa `2` para
argumentos invalidos. Revisar el resumen para conocer las incidencias de negocio.

## Operacion en VM

Instalar bajo `C:\Procesos\CONTROL_VENCIMIENTOS`; recrear `.venv` y configurar
identidad administrada, ODBC, permisos de BD/Monitor/Key Vault y rutas externas.
Programar `ejecutar.bat` con Windows Task Scheduler, directorio del proyecto y
opcion **No iniciar una instancia nueva** si ya hay otra. El bloqueo local es
una proteccion adicional; un solapamiento se informa como fallo, no se encola.
No configurar terminacion generica por duracion. La reanudacion es manual por ID.
El batch no instala tareas programadas ni modifica recursos Azure automaticamente.

## Pruebas y validacion

```powershell
.\.venv\Scripts\python.exe -B -m pytest pruebas -q -p no:cacheprovider
```

Cobertura funcional: reglas, Excel/CSV, entradas invalidas, parametros faltantes,
Key Vault/Monitor simulados, operaciones SQL simuladas, estados inmediatos,
interrupciones, reanudacion, copia alterada, respaldo fallido, bloqueo, correo,
logs y redaccion. No hay conexiones reales en las pruebas.

Evidencia: pytest; prueba controlada local con el inventario de ejemplo, su log,
reportes y ZIP. Validacion manual local: revisar hojas y abrir el ZIP. Pendiente:
Key Vault real, permisos de identidad, SP real de Monitor, DDL/operaciones en
Azure SQL con ODBC instalado, envio SMTP real y Task Scheduler de la VM.

No promover a PRODUCCION hasta probar esos puntos con datos autorizados. No se
sostiene que los mocks validen la red, el esquema real o la seguridad del entorno.
La conexion Monitor debe implementarse o ajustarse al contrato acordado.

## Troubleshooting y limites

- Falta KEYVAULT_NAME/MONITOR_SP_PARAMETROS: completar configuracion real o usar
  explicitamente la prueba local; nunca agregar un valor productivo de reserva.
- ODBC no instalado: instalar Driver 18 en la VM antes de probar SQL.
- Acceso denegado a secretos: revisar identidad y permisos sin imprimir secretos.
- Parametro de Monitor ausente: corregir el SP/contrato; no usar el mes/dia actual.
- ID desconocido: comprobar la BD y el ID de este proceso.
- Copia modificada: restaurar la copia original; no borrar huellas para forzar reanudacion.
- Error de ZIP: corregir permisos/espacio y reanudar el ID.
- No existe backend web aqui: su contrato de reserva/entrega requiere otra integracion.
- La carpeta recibida no tenia repositorio Git propio: no se crearon commits ni se
  modificaron ramas. Antes de integrar cambios, usar repo y rama de trabajo.
