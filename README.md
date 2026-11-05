# Bots de automatización

Este repositorio contiene dos bots independientes. Cada carpeta tiene su
propio código, configuración, dependencias y archivo `ejecutar.bat`.

| Carpeta | Qué hace | Documentación |
|---|---|---|
| `acme/` | Consulta Work Items y descarga reportes mensuales de ACME System 1. | [Guía de ACME](acme/README.md) |
| `control_vencimientos/` | Revisa inventario Excel/CSV por producto y lote y clasifica sus fechas de vencimiento. | [Guía de vencimientos](control_vencimientos/README.md) |

```text
RPA-ACME/
├── acme/
│   ├── automatizacion/
│   ├── config/
│   ├── integraciones/
│   ├── tests/
│   ├── tools/
│   ├── .env.example
│   ├── ejecutar.bat
│   ├── main.py
│   ├── worker.py
│   └── requirements.txt
├── control_vencimientos/
│   ├── automatizacion/
│   ├── config/
│   ├── integraciones/
│   ├── pruebas/
│   ├── .env.example
│   ├── configuracion.py
│   ├── ejecutar.bat
│   ├── main.py
│   └── requirements.txt
├── .gitignore
└── README.md
```

## Preparar y ejecutar

Desde la raíz del repositorio, preparar cada bot por separado:

```powershell
cd acme
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Completar .env antes de ejecutar.
.\ejecutar.bat --help
```

Para vencimientos, hacer lo mismo dentro de `control_vencimientos/`. No copiar
un `.env` entre bots: tienen configuraciones y requisitos diferentes. Su README
describe la prueba local y las integraciones de Azure y Monitor pendientes.

Una vez preparados, los lanzadores también se pueden llamar desde la raíz:

```powershell
.\acme\ejecutar.bat consultar --limite 10
.\control_vencimientos\ejecutar.bat procesar --prueba-local --parametros-prueba pruebas/parametros_ejemplo.json
```

Cada lanzador ejecuta desde su propia carpeta. Los archivos relativos y la
`.venv` corresponden al bot elegido.

## Cambio de ubicación

ACME se movió de la raíz a `acme/`. Vencimientos se movió de
`proyectos/control_vencimientos/` a `control_vencimientos/`. Actualizar accesos
directos, tareas programadas y comandos externos que usen las rutas anteriores.
Las instalaciones nuevas deben crear sus entornos virtuales en las nuevas rutas.

Los datos locales e historiales se conservan. Las rutas absolutas de
instalaciones distintas deben revisarse en su `.env`. Vencimientos valida las
rutas originales al reanudar una ejecución; una ejecución anterior al traslado
puede requerir conservar esas ubicaciones. No editar su historial ni reutilizar
un ID anterior con datos o rutas diferentes.

## Datos privados

Git excluye los `.env` reales, credenciales, entornos virtuales, inputs, media,
bases de datos y salidas de ambos bots. Solo se comparten plantillas sin secretos,
código y documentación. Las carpetas operativas se crean al preparar o ejecutar
cada proyecto y no representan archivos de entrada incluidos en GitHub.
