"""Plantillas y adaptacion de reportes del inventario."""
from pathlib import Path

from automatizacion.excel import guardar_csv, guardar_excel
from integraciones.inventario import COLUMNAS


def crear_plantilla(ruta):
    ruta = Path(ruta)
    if ruta.exists():
        raise FileExistsError('La plantilla ya existe; elegir otro destino')
    if ruta.suffix.lower() == '.csv':
        guardar_csv(ruta, COLUMNAS, [])
    elif ruta.suffix.lower() == '.xlsx':
        guardar_excel(ruta, [('inventario', COLUMNAS, [])])
    else:
        raise ValueError('La plantilla debe ser CSV o XLSX')
