"""Excel de seguimiento de las descargas mensuales."""
from automatizacion.excel import escribir_reporte, ruta_reporte


def generar_reporte_mensual(historial, ejecucion, carpeta):
    tareas = historial.tareas(ejecucion)
    columnas = ['fila', 'proveedor_taxid', 'anio', 'mes', 'estado', 'intentos', 'archivo', 'error', 'captura', 'fecha_fin']
    resumen = historial.resumen(ejecucion)
    hojas = (
        ('resultados', columnas, [[t[c] for c in columnas] for t in tareas if t['estado'] in ('descargado', 'sin_reporte')]),
        ('errores', columnas, [[t[c] for c in columnas] for t in tareas if t['estado'] in ('error', 'pendiente')]),
        ('resumen', ['campo', 'valor'], list(resumen.items())),
    )
    destino = ruta_reporte(carpeta, 'reportes_mensuales', ejecucion, resumen['inicio'])
    return escribir_reporte(destino, hojas)
