# ¿Qué hace este proceso?

**RPA ACME** automatiza dos tareas que normalmente se hacen entrando a ACME
System 1 y revisando sus pantallas: consultar tareas y descargar reportes
mensuales. La persona prepara un Excel; el robot hace la consulta y entrega
archivos con los resultados.

## Resumen sencillo

| Proceso | Qué recibe | Qué hace | Qué entrega |
|---|---|---|---|
| Consulta de Work Items | Excel con `tipo` y `estado` | Busca las tareas que cumplen esos filtros y lee sus detalles | Excel con resultados, errores y resumen |
| Descarga de reportes mensuales | Excel con proveedor, año y meses | Solicita el reporte de cada proveedor y mes | Archivos descargados y Excel de seguimiento |

### 1. Consulta de Work Items

Sirve para reunir información de muchas tareas sin abrirlas una por una.
Un **Work Item** es una tarea o registro que aparece en el listado de ACME.

Cómo lo hace:

1. Lee los Excel de la carpeta de entrada y obtiene los filtros.
2. Abre Chrome e inicia sesión en ACME con la cuenta configurada.
3. Recorre el listado completo y selecciona las tareas que coinciden.
4. Abre cada tarea seleccionada y guarda el texto visible de su detalle.
5. Genera un Excel con lo que pudo leer y los errores encontrados.

Por ejemplo, un Excel con `tipo=WI5` y `estado=Open` pide consultar las tareas
WI5 abiertas. No cambia sus estados ni agrega comentarios: reúne información.

### 2. Descarga de reportes mensuales

Sirve para obtener varios reportes sin elegir proveedor, año y mes manualmente
cada vez. `proveedor_taxid` es el identificador del proveedor utilizado en ACME.

Cómo lo hace:

1. Lee cada proveedor y el rango de meses solicitado en el Excel.
2. Convierte el rango en solicitudes individuales: una por proveedor y mes.
3. Inicia sesión en ACME y completa el formulario de reportes.
4. Descarga y valida cada archivo antes de registrarlo como correcto.
5. Genera un Excel que distingue descargas correctas, meses sin reporte y errores.

Por ejemplo, una fila con año `2025`, `mes_desde=1` y `mes_hasta=3` solicita
enero, febrero y marzo de ese proveedor. No sube reportes anuales.

## Qué debe preparar la persona

Colocar los Excel en `inputs/` o en la carpeta configurada. El robot reconoce el
proceso por sus encabezados; el nombre del archivo puede ser cualquiera.
También debe estar preparada la configuración de acceso y del navegador.
Los ejemplos siguientes no contienen credenciales reales.

## Dónde quedan los resultados

- `reportes/`: Excel de resultados y seguimiento, separados por ejecución.
- `descargas/reportes_mensuales/`: reportes mensuales descargados, salvo otra ruta configurada.
- `logs/`: registro de lo que hizo el robot y los problemas encontrados.
- SQLite: historial de ejecuciones, tareas y resultados.

Una **ejecución** es una corrida del robot. Su identificador permite relacionar
los archivos con su historial. Los resultados de corridas anteriores se conservan.

## Qué ocurre si algo falla

Los fallos temporales de una tarea tienen intentos limitados. Si una tarea falla
definitivamente, se registra el motivo y se continúa cuando es posible. Si falla
una condición necesaria para seguir, como la autenticación, se detiene esa
corrida y se conserva lo alcanzado. Los avisos por correo son opcionales.

El proyecto también tiene un `worker.py`: un programa que consulta una API por
solicitudes pendientes y devuelve resultados. Para usarlo, la plataforma debe
implementar el contrato correspondiente. La configuración de Key Vault de este
proyecto sigue reservada; no debe confundirse con la integración del control de
vencimientos, que tiene su propia configuración.

## Instalación y uso técnico

Las secciones siguientes explican los comandos, los formatos completos y las
opciones de configuración para preparar y operar el robot.

## Probar

Todos los comandos descubren los Excel de `CARPETA_INPUTS`, por defecto `inputs/`.
Para procesar ambos tipos automaticamente:

```powershell
.\.venv\Scripts\python.exe main.py
# Equivalente, con un limite por archivo y proceso:
.\.venv\Scripts\python.exe main.py procesar --limite 10
```

También puedes abrir `ejecutar.bat` con doble clic o usarlo desde PowerShell:

```powershell
.\ejecutar.bat
.\ejecutar.bat consultar --limite 10
.\ejecutar.bat descargar-reportes --limite 1
.\ejecutar.bat --help
```

El lanzador usa exclusivamente `.venv\Scripts\python.exe`, cambia a la carpeta
del proyecto, transmite los argumentos y devuelve el código de salida de Python.
Funciona aunque se invoque desde otra carpeta. No incluye pausa, para permitir
su uso con el Programador de tareas; al abrirlo con doble clic, la ventana se
cierra al finalizar. Si falta el entorno virtual, muestra un error y devuelve 1.

Los Excel se leen desde `inputs/` (o `CARPETA_INPUTS` si se configura otra ruta).
No se selecciona un archivo por su nombre: se intenta procesar cada `.xlsx` y
`.xlsm` compatible que haya directamente en esa carpeta. Los temporales `~$` se
ignoran; las subcarpetas no se recorren. Los encabezados determinan qué proceso
corresponde. Los archivos inválidos o incompatibles se informan como omitidos.

Los reportes de salida incluyen fecha y hora de inicio de su ejecución con el
formato **DDMMYYYY-HHMMSS**, por ejemplo:

```text
work_items_05112026-143000_a1b2c3d4e5f6.xlsx
reportes_mensuales_05112026-143015_f6e5d4c3b2a1.xlsx
```

El identificador adicional distingue reportes creados en el mismo segundo.
Al recuperar una ejecución se conserva su nombre de reporte; los archivos de
otras ejecuciones no se reemplazan. Los reportes anteriores conservan sus nombres.
Los archivos mensuales descargados también añaden `DDMMYYYY-HHMMSS`, manteniendo
el proveedor y el período para poder identificarlos.

Los nombres de los archivos son libres. Se examina la primera fila de la hoja
activa de cada `.xlsx` o `.xlsm`, en orden alfabetico, sin recorrer subcarpetas.
Los encabezados pueden estar en cualquier orden y tener mayusculas o espacios
exteriores; se permiten columnas adicionales. Cada encabezado requerido debe
aparecer una sola vez. `consultar` elige los archivos con `tipo` y `estado`;
`descargar-reportes` elige los que tienen `proveedor_taxid`, `anio`, `mes_desde`
y `mes_hasta`. Un libro con ambos conjuntos se procesa por ambos flujos al
ejecutar `procesar` o iniciar sin comando.
Para los reportes tambien se aceptan `año` como `anio`, `mes_inicio` como
`mes_desde` y `mes_fin` como `mes_hasta`. No deben coexistir dos columnas que
representen el mismo encabezado.

Los archivos incompatibles o corruptos se omiten indicando el motivo. Los
temporales de Excel `~$` se ignoran; los `.xls` deben convertirse a `.xlsx`.
Los datos se validan despues de los encabezados: un archivo con datos invalidos
genera su ejecucion con error y permite continuar con los siguientes.
Si no hay archivos compatibles, se informa el error sin iniciar el navegador.

Cada Excel y proceso tienen su propia ejecucion, historial, reporte y correos;
`--limite` se aplica por archivo. Los duplicados se evitan dentro de cada
ejecucion, no entre archivos diferentes. Un fallo no impide intentar los otros
archivos; el comando retorna 1 si alguno falla y 0 si todos completan.

En PowerShell:

```powershell
cd "C:\Users\Pablo\Documents\GitHub\RPA-ACME"
.\.venv\Scripts\python.exe main.py consultar --limite 10
```

Para consultar todas las coincidencias:

```powershell
.\.venv\Scripts\python.exe main.py consultar
```

El limite se aplica a los detalles que se abren, despues de recorrer el listado
completo, filtrar y ordenar los IDs numericamente. El reporte se guarda en
`reportes/work_items_DDMMYYYY-HHMMSS_<identificador>.xlsx`, con hojas `resultados`, `errores` y
`resumen`. Si no existen coincidencias, se genera un reporte vacio valido.

La carpeta contiene su propio `.venv` y un `.env` privado configurado para esta
cuenta. `.env` esta excluido del repositorio; no copiarlo a documentacion ni logs.
Para configurar otra instalacion, copiar `.env.example` a `.env` y completar
`ACME_EMAIL` y `ACME_PASSWORD`.

## Excel de entrada

Crear o editar cualquier Excel de la carpeta de entrada (por ejemplo,
`inputs/filtros.xlsx`), con estas dos columnas en la hoja activa:

| tipo | estado |
|---|---|
| WI5 | Open |
| WI4 | |

En este ejemplo se consultan los WI5 abiertos y todos los WI4. Los filtros de una
fila se combinan con AND; las filas con OR. Una celda vacia incluye todos los
valores de ese campo. Ambas vacias incluyen todos los Work Items. El archivo
entregado tiene ambos filtros vacios; una hoja con solo encabezados tiene el
mismo efecto. No se distinguen mayusculas ni espacios exteriores. Se evitan
duplicados por WIID dentro de una ejecucion.

Los codigos reales de practica incluyen WI1 a WI5 y estados como `Open`; usar los
valores que muestre el listado de la cuenta. Un filtro sin coincidencias produce
cero resultados. Los archivos con columnas requeridas ausentes o repetidas se
omiten durante el descubrimiento por carpeta.

## Descargar reportes mensuales por proveedor

El comando `descargar-reportes` usa **Reports > Download Monthly Report**.
Lee todos los Excel compatibles de la carpeta de entrada, por ejemplo
`inputs/reportes_mensuales.xlsx`, con cuatro columnas en la hoja activa:

| proveedor_taxid | anio | mes_desde | mes_hasta |
|---|---|---|---|
| TAXID_REAL_DEL_PROVEEDOR | 2025 | 1 | 3 |

Reemplazar el TaxID del ejemplo por uno real de ACME y elegir un anio disponible
en el formulario. Guardar el TaxID como texto para conservar ceros iniciales.
Cada fila solicita todos los meses del rango, incluidos sus extremos. Para un
solo mes, repetirlo en `mes_desde` y `mes_hasta`; para un anio completo, usar
1 y 12. Se pueden agregar varios proveedores o periodos en filas separadas.
Los periodos de distintos anios se ingresan en filas distintas.

Las cuatro columnas son obligatorias y no pueden repetirse. Los meses deben ser
enteros entre 1 y 12, en orden ascendente; el anio debe ser un entero de cuatro
digitos desde 1900. Las filas completamente vacias se ignoran; una fila incompleta
o un archivo sin solicitudes produce un error antes de abrir Chrome. Las
combinaciones repetidas de proveedor, anio y mes se descargan una sola vez por
ejecucion, conservando la referencia a la primera fila que las solicito.

```powershell
.\.venv\Scripts\python.exe main.py descargar-reportes
# Probar una descarga mensual por cada archivo compatible:
.\.venv\Scripts\python.exe main.py descargar-reportes --limite 1
```

`--limite` se aplica a las solicitudes mensuales despues de expandir los rangos
y eliminar duplicados, manteniendo el orden de las filas y los meses.
`CARPETA_INPUTS` configura la carpeta a recorrer. La opcion `--entrada` deja de
usarse en la CLI: basta agregar el archivo a la carpeta configurada.
`CARPETA_DESCARGAS` configura la carpeta de salida, por defecto
`descargas/reportes_mensuales`.

Los archivos se guardan en subcarpetas por ejecucion y solicitud, con nombres
que identifican el proveedor, anio y mes. Las ejecuciones anteriores se conservan.
Solo se confirma una descarga al encontrar un archivo terminado y no vacio;
las respuestas HTML y los archivos parciales no cuentan como reportes. Los
intentos incompletos pueden conservarse en su propia subcarpeta para diagnostico.

El historial se guarda en las tablas `ejecuciones_mensuales` y
`descargas_mensuales` de la misma SQLite. El Excel de seguimiento se genera en
`reportes/reportes_mensuales_DDMMYYYY-HHMMSS_<identificador>.xlsx`, con hojas `resultados`, `errores`
y `resumen`. Los resultados distinguen `descargado` y `sin_reporte`. Un mes que
ACME identifica expresamente sin reporte no se reintenta y no implica un fallo
tecnico; un periodo rechazado se registra como error. Los fallos tecnicos usan
`ACME_MAX_INTENTOS`, y una sesion caducada permite volver a iniciar sesion.
Si se interrumpe una ejecucion, el siguiente comando registra las solicitudes
pendientes como error y genera el reporte parcial. Comparte el bloqueo exclusivo
con la consulta de Work Items. No sube reportes anuales ni modifica Work Items.

El formulario se localiza mediante selectores configurables en
`config/selectores.json` y etiquetas visibles en ingles. La integracion tiene
pruebas con navegador simulado y DOM de formulario; la validacion autenticada
contra ACME de esta funcionalidad queda pendiente por restricciones de red del
entorno de desarrollo.

## Datos e historial de Work Items

La base predeterminada es `temporales/acme.db`:

| Tabla | Contenido |
|---|---|
| `ejecuciones` | Inicio, fin, limite, coincidencias, seleccionados y resumen de cada consulta. |
| `details` | Excel leido y sus filtros, estado `pendiente`, `leido` o `error`. |
| `cola` | Un Work Item por ejecucion, su estado local, intentos, errores y captura. |
| `resultados` | Datos del listado, texto visible del detalle, URL y fecha de consulta. |

El estado local de `cola` es independiente de `estado_web`. `leido` significa
que se guardo el detalle; no modifica `Open` ni ningun otro estado en ACME.
Guardar el resultado y marcarlo leido es una sola transaccion. El detalle se
conserva como texto visible, sin asumir los mismos campos para todos los tipos.
SQLite conserva el texto completo; Excel limita cada celda a 32767 caracteres.

Cada consulta crea una nueva ejecucion y vuelve a leer las coincidencias. No
elimina resultados anteriores. El comando permite una sola ejecucion simultanea
por carpeta. Si una ejecucion anterior se interrumpio, al volver a iniciar se
marca con error y se genera su reporte parcial antes de empezar la nueva.

## Configuracion y navegador

`config/selectores.json` contiene rutas y selectores de acceso, tabla,
paginacion y detalle. `.env` permite configurar:

- `ACME_HEADLESS`: `true` por defecto; `false` para observar Chrome.
- `ACME_TIMEOUT`: espera maxima por elemento o pagina, 25 segundos.
- `ACME_MAX_INTENTOS`: intentos por tarea, 3 por defecto.
- `ACME_FILTROS` y `ACME_DB`: rutas de entrada y base, relativas al proyecto o absolutas.
- `TAGUI_ORIGEN`: instalacion de TagUI que se copiara a un runtime privado.

Se usa Chrome instalado en Windows. `tools/preparar_tagui.py` copia la
instalacion existente de `%APPDATA%\tagui` (o `TAGUI_ORIGEN`) a `.runtime/tagui`.
El proyecto usa otro perfil y puerto, no modifica la instalacion de los otros
bots ni finaliza sus procesos. El runtime privado desactiva las trazas de
protocolo que podrian contener credenciales. El archivo temporal de credenciales
se elimina al terminar cada intento de acceso; los comandos TagUI no contienen
sus valores y los logs propios ocultan usuario y password.

Si no tienes instalacion TagUI, instalar primero las dependencias y ejecutar su
instalacion una vez antes de preparar el runtime. En este equipo ya esta preparado.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Solo cuando no existe una instalacion TagUI:
.\.venv\Scripts\python.exe -c "import rpa as r; r.setup()"
.\.venv\Scripts\python.exe tools/preparar_tagui.py
```

Las esperas verifican elementos y URL. Si caduca la sesion, el robot vuelve a
autenticarse y repite la operacion. Credenciales rechazadas detienen la consulta;
los errores tecnicos de una tarea se reintentan y, al agotarse, se guarda una
captura en `capturas/<ejecucion>/` si es posible y se continua con las otras.
Se intenta cerrar sesion y navegador al finalizar. Los avisos por correo son
opcionales y se configuran como se indica a continuacion.

## Notificaciones por Gmail

Ambos comandos (`consultar` y `descargar-reportes`) pueden enviar avisos de
inicio y final con el resumen y el Excel de seguimiento. No se envian avisos de avance.
Agregar o completar estas variables en el `.env` privado:

```dotenv
MODULO_CORREO=true
MODULO_CORREO_INICIAL=true
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_REMITENTE=tu_cuenta@gmail.com
EMAIL_PASSWORD=tu_contrasena_de_aplicacion
LISTA_DISTRIBUCION_CORREO=destino1@example.com,destino2@example.com
LISTA_DISTRIBUCION_ERROR=
```

Usar una contrasena de aplicacion de Google para la cuenta remitente; se puede
guardar con o sin espacios. Requiere verificacion en dos pasos y que la cuenta
permita esta opcion. Ver [la guia de Google](https://support.google.com/accounts/answer/185833?hl=es).
Las credenciales de correo son independientes de las de ACME y solo se guardan
en `.env`. El envio usa `SMTP_SERVER`, puerto 465 con TLS directo o 587 con
STARTTLS, validacion de certificado y tiempo de espera de 10 segundos. No
requiere nuevas dependencias. `MODULO_CORREO_INICIAL=false` omite solo el aviso
de inicio; `MODULO_CORREO=false` desactiva todos los correos. Las listas aceptan
comas, punto y coma o espacios, y no se duplican las direcciones compartidas.

| Aviso | LISTA_DISTRIBUCION_ERROR | LISTA_DISTRIBUCION_CORREO |
|---|---|---|
| Inicio | Sí | Sí |
| Resultados (`completado` o `con_errores`) | Sí | Sí |
| Falla que interrumpe la ejecución (`error`) | Sí | No |

Un resultado con tareas fallidas sigue siendo un resumen de resultados para
ambas listas. Un fallo general, de autenticación o de generación del Excel se
envía solo a `LISTA_DISTRIBUCION_ERROR`. Si esa lista está vacía, no se sustituye
por la lista de resultados. Se puede configurar cualquiera de las dos listas
por separado; cada aviso se omite con advertencia si no tiene destinatarios.

Los correos incluyen el proceso, identificador de ejecucion, archivo de entrada
y fecha de inicio. El resumen final incluye total, porcentaje, lecturas o descargas
correctas, meses sin reporte y errores definitivos. Una tarea cuenta despues de
guardar su resultado en SQLite; los reintentos no cuentan como tareas nuevas.
En las descargas, cada mes de cada proveedor es una tarea, no cada fila del Excel.
Cada archivo y proceso envia como maximo dos avisos: inicio y final, independientemente
del numero de tareas. `CORREO_CADA` y `ACME_CORREO_CADA` se conservan por compatibilidad
de configuracion, pero ya no tienen efecto.

El correo final indica `completado`, `con_errores` o `error`, duracion y conteos.
Adjunta solo el Excel de resultados/errores/resumen, no las descargas mensuales,
logs ni capturas. Si el Excel supera 15 MiB, no se puede leer o falla su
generacion, se envia el resumen indicando que falta el adjunto y se registra
la causa en el log. Un fallo al generar el Excel mantiene el error del comando;
el correo no lo convierte en una ejecucion correcta.

El correo esta desactivado por defecto. Una configuracion de correo incompleta
o invalida desactiva los avisos de esa ejecucion con una advertencia. Los fallos
de conexion, autenticacion o destinatarios se registran y el robot continua,
sin alterar los resultados de las tareas ni su codigo de salida. No se reenvia
automaticamente un evento fallido; los eventos siguientes se intentan normalmente.
La aceptacion por SMTP no garantiza la entrega a la bandeja de entrada.

Los avisos comienzan al crear la ejecucion, antes de leer el Excel. Tambien se
intenta enviar el final ante errores de entrada o de acceso a ACME. Los errores
anteriores a crear una ejecucion (por ejemplo, no poder adquirir el bloqueo)
se informan por consola. Las ejecuciones antiguas recuperadas no generan
correos retrospectivos. Las pruebas de correo usan SMTP y navegador simulados;
no envian mensajes reales.

## Estandar del archivo .env

La plantilla conserva la estructura original de modulos, identificacion,
rutas, correo, bases de datos, monitor y reintentos. Las credenciales para
entrar a ACME se guardan en `ACME_EMAIL` y `ACME_PASSWORD`; no se reutiliza la
contrasena SMTP para acceder a la web.

| Variable estandar | Uso |
|---|---|
| `MODULO_SCRAPING` | En `false`, ambos comandos salen sin iniciar ejecuciones, navegador ni correo. |
| `NOMBRE_PROCESO`, `NOMBRE_PROCESO_TEXTO` | Identificacion incluida en el cuerpo de los correos. |
| `RUTA_BASE` | Base de las rutas de datos relativas; vacia usa la carpeta del proyecto. Los selectores y el runtime pertenecen a la instalacion. |
| `DB_PATH` | Archivo SQLite del historial. |
| `CARPETA_INPUTS` | Carpeta que los comandos recorren siempre; `inputs` por defecto. Se procesan todos los Excel compatibles, sin filtrar por nombre. |
| `EXCEL_ENTRADA` | Ruta individual de compatibilidad para llamadas Python a `consultar`; la CLI descubre todos los archivos en `CARPETA_INPUTS`. |
| `EXCEL_REPORTES_MENSUALES` | Ruta individual de compatibilidad para llamadas Python a `descargar_reportes`; la CLI usa `CARPETA_INPUTS`. |
| `EXCEL_SALIDA` | Carpeta de salida indicada por la ruta del archivo, por ejemplo `reportes/resultados.xlsx` usa `reportes/`. Los nombres finales siguen siendo propios de cada ejecucion para conservar el historial. Tambien acepta directamente una carpeta. |
| `CARPETA_CAPTURAS`, `CARPETA_DESCARGAS`, `RUTA_HISTORICO` | Salidas de capturas, descargas y logs. |
| `MAX_REINTENTOS` | Intentos tecnicos por tarea. |

Las variables estandar tienen prioridad sobre sus alias anteriores:
`DB_PATH` sobre `ACME_DB`, `EXCEL_ENTRADA` sobre `ACME_FILTROS`,
`EXCEL_REPORTES_MENSUALES` sobre `ACME_REPORTES_INPUT`, `CARPETA_DESCARGAS`
sobre `ACME_DESCARGAS`, `MAX_REINTENTOS` sobre `ACME_MAX_INTENTOS`,
`MODULO_CORREO` sobre `ACME_CORREO_ACTIVO`, `EMAIL_REMITENTE` sobre
`ACME_CORREO_USUARIO`, `EMAIL_PASSWORD` sobre `ACME_CORREO_PASSWORD`,
`LISTA_DISTRIBUCION_CORREO` sobre `ACME_CORREO_DESTINATARIOS` y `CORREO_CADA`
sobre `ACME_CORREO_CADA`. Un valor estandar presente pero vacio no toma el alias.

`ID_EJECUCION` permanece vacio porque SQLite genera un ID por ejecucion.
Las claves de Key Vault, SQL Server, monitor, `DB_SQLITE` y `DB_NAME`
se conservan como campos reservados de la plantilla;
no activan esas integraciones. `MAX_REINTENTOS_CORREO`
se mantiene en 1 como referencia; no habilita reenvios automaticos.

## Logs centralizados

Ambos procesos escriben en un unico archivo dentro de `RUTA_HISTORICO` (por defecto
`logs/`), llamado `<nombre_proceso>.log`; por ejemplo, `rpa_acme.log`. Cada registro
incluye fecha, nivel, nombre del proceso, ID de ejecucion y modulo (`WORK_ITEMS`
o `REPORTES_MENSUALES`). Tambien se muestra en consola y se ocultan las credenciales.

`RETENCION_LOGS_MESES=12` establece un periodo de doce meses calendario desde el
primer registro del archivo. Al iniciar una ejecucion despues del vencimiento,
se reemplaza el contenido del mismo archivo, sin generar copias adicionales.
Antes de esa fecha se agregan los registros al final. Los antiguos archivos por
ejecucion se conservan; esta configuracion no los elimina ni los modifica.

## Conexion con un backend remoto

`worker.py` recoge Excel desde una API HTTPS, ejecuta `consultar` o
`descargar-reportes` y devuelve el Excel de seguimiento. Conserva los trabajos
y envios pendientes en SQLite; una entrega fallida no repite el RPA.
No requiere FastAPI en la maquina Windows.

Configurar `BACKEND_API_URL`, `BACKEND_API_TOKEN`, `ROBOT_ID` e
`INTERVALO_CONSULTA_SEGUNDOS` en `.env` y ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe worker.py
```

El backend debe implementar el [contrato de intercambio y recuperacion](docs/api_backend.md).
Cada trabajo usa su propia subcarpeta de `CARPETA_INPUTS` y un solo proceso.
La CLI `main.py` mantiene su funcionamiento habitual.

## Componentes reutilizables

La carpeta `automatizacion/` contiene componentes sin selectores, credenciales
ni tablas de ACME. Para usarlos en otro proyecto, copiar ese paquete e instalar
`openpyxl` y `xlrd` cuando se utilicen sus funciones de Excel y validacion.
El adaptador de este proyecto usa adicionalmente `rpa`.

| Modulo | Responsabilidad |
|---|---|
| `automatizacion.procesos` | Ejecutar llamadas a una biblioteca bloqueante en un proceso propio, con un plazo y descarte de la sesion. |
| `automatizacion.ejecucion` | Reintentos configurables, recuperacion de sesion y cierre con reporte e historial coherentes. |
| `automatizacion.archivos` | Validar estructura de CSV, XLSX y XLS, con columnas requeridas opcionales. |
| `automatizacion.excel` | Generar reportes de varias hojas y reemplazar el destino solo cuando el archivo completo esta guardado. |
| `automatizacion.sqlite` | Conexion transaccional SQLite sin crear tablas de negocio. |

Los flujos de `integraciones/proceso.py` y `integraciones/proceso_mensual.py`
conservan las reglas de ACME. `integraciones/sesion.py` adapta su error de sesion
al recuperador comun. Los puntos de entrada y los parametros de fabrica de
navegador y notificador se mantienen para facilitar pruebas y otros adaptadores.
`integraciones/historial_mensual.py` contiene la persistencia mensual y
`integraciones/seguimiento_mensual.py` genera su Excel. Los historiales de
Work Items, reportes mensuales e intercambio API crean solo sus propias tablas.
Los nombres importables anteriores de `proceso_mensual.py` se conservan como
alias de compatibilidad.

Las ejecuciones nuevas mantienen `reporte_pendiente=1` en SQLite hasta guardar
el Excel completo y confirmar su generacion. Si el proceso se interrumpe entre
la finalizacion de tareas y el reporte, el siguiente inicio regenera el Excel
con los resultados guardados, sin repetir consultas ni descargas. La recuperacion
del historial existente funciona incluso si ya no quedan Excel de entrada.
Si vuelve a fallar la generacion, la marca se conserva para el siguiente intento.
La columna se agrega automaticamente a las bases existentes, sin borrar filas.

Ejemplo de reutilizacion de los componentes:

```python
from pathlib import Path
from automatizacion.archivos import validar_tabla
from automatizacion.ejecucion import reintentar
from automatizacion.excel import escribir_reporte

# La aplicacion proporciona su operacion y decide que errores reintentar.
resultado = reintentar(
    obtener_datos, intentos=3,
    reintentable=lambda error: isinstance(error, ConnectionError),
)
escribir_reporte(Path("salida.xlsx"), [
    ("datos", ["codigo", "importe"], resultado),
])
validar_tabla(Path("salida.xlsx"), columnas_requeridas=("codigo", "importe"))
```

`ProcesoAislado` importa una biblioteca por nombre y ejecuta sus funciones en
serie dentro de un proceso creado mediante `spawn`. Se utiliza con funciones
cuyos argumentos y resultados sean serializables por Python. Los scripts que
lo utilicen deben iniciar el trabajo dentro de `if __name__ == '__main__':`,
como ya hacen `main.py` y `worker.py`. Las condiciones de espera se ejecutan en
serie; el plazo cancela las llamadas al motor, no funciones Python arbitrarias
del proceso principal.

Ante un bloqueo de TagUI se termina exclusivamente el arbol del proceso creado
por el adaptador, se invalida el canal y se detiene esa ejecucion. Las tareas
pendientes quedan como error; las lecturas y descargas confirmadas se conservan.
Un error de espera normal, con el motor respondiendo, conserva los reintentos.
El directorio de trabajo cambia solo dentro del proceso de TagUI.

Un fallo al generar el reporte tambien se guarda en SQLite como `error`,
conservando la causa anterior si la habia. Los conteos del correo final se
obtienen del resumen persistido, incluidas las tareas canceladas.

Las descargas deben contener una cabecera con al menos dos columnas unicas y
registros con estructura consistente. Se rechazan JSON, HTML, texto de error,
libros corruptos y tablas vacias. Se aceptan CSV separados por coma, punto y
coma, tabulador o barra vertical. Para exigir nombres concretos de columnas,
agregar `columnas_requeridas` como una lista en la seccion `reportes_mensuales`
de `config/selectores.json`, usando los encabezados del reporte real. Sin esa
lista se verifica la estructura, sin asumir un esquema de negocio.

## Ejecutar las pruebas

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Las pruebas usan archivos y bases temporales y no acceden a la cuenta real.
Las pruebas de DOM requieren Node.js 22 o posterior y Chrome en
`C:\Program Files\Google\Chrome\Application\chrome.exe`; `CHROME_PATH` permite
indicar otra ruta solo para esas pruebas. Node.js no es necesario para ejecutar
el robot.

El reinicio de datos de practica se realizo una sola vez durante la preparacion,
con autorizacion del usuario, porque la cuenta estaba vacia. El codigo del robot
no incluye una funcion para reiniciar datos ni para actualizar Work Items.

Validacion realizada el 28-10-2026: 20 pruebas automatizadas aprobadas y una
consulta real que recorrio 110 Work Items, guardo 10 detalles y termino con cero
errores. Se comprobaron las relaciones de SQLite, las tres hojas del Excel y el
cierre de sesion/navegador. La revision de los registros no encontro credenciales.


## Archivos privados y Git

`.gitignore` excluye `.env`, credenciales y claves, inputs, reportes, exportaciones,
descargas, media, capturas, logs, SQLite, respaldos, perfiles de navegador, el
runtime de TagUI y los entornos virtuales. También excluye archivos operativos
por extensión si se guardan fuera de las carpetas habituales.

Se conservan como versionables el código, los README, `ejecutar.bat`, los
selectores no sensibles y `.env.example` sin valores privados. `.gitignore` actúa
sobre archivos completos; los secretos no deben escribirse dentro del código.
