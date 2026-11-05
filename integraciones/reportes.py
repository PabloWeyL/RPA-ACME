from automatizacion.excel import escribir_reporte, ruta_reporte


def generar_reporte(base, ejecucion, carpeta):
    resultados, errores, resumen, archivo = base.datos_reporte(ejecucion)
    datos = [
        ("resultados", ["wiid", "descripcion", "tipo", "estado_web", "fecha_web", "url", "detalle", "fecha_consulta"], resultados),
        ("errores", ["wiid", "estado", "intentos", "error", "captura"], errores),
        ("resumen", ["campo", "valor"], list(resumen.items()) + [("archivo_" + clave, valor) for clave, valor in archivo.items()]),
    ]
    destino = ruta_reporte(carpeta, 'work_items', ejecucion, resumen['inicio'])
    return escribir_reporte(destino, datos)
