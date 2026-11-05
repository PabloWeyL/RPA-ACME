"""Escritura atomica de tablas CSV y Excel, independiente del inventario."""
import csv
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def guardar_csv(ruta, columnas, filas):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name('.' + uuid4().hex + '.csv')
    try:
        with temporal.open('w', encoding='utf-8-sig', newline='') as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=columnas)
            escritor.writeheader()
            escritor.writerows({c: fila.get(c, '') for c in columnas} for fila in filas)
        temporal.replace(ruta)
    finally:
        temporal.unlink(missing_ok=True)


def guardar_excel(ruta, hojas):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name('.' + uuid4().hex + '.xlsx')
    libro = Workbook()
    libro.remove(libro.active)
    try:
        for nombre, columnas, filas in hojas:
            hoja = libro.create_sheet(nombre)
            hoja.append(list(columnas))
            for fila in filas:
                hoja.append([fila.get(c, '') for c in columnas])
            for fila in hoja:
                for celda in fila:
                    if isinstance(celda.value, str):
                        celda.data_type = 's'
            for celda in hoja[1]:
                celda.font = Font(bold=True, color='FFFFFF')
                celda.fill = PatternFill('solid', fgColor='1F4E78')
            hoja.freeze_panes = 'A2'
            hoja.auto_filter.ref = hoja.dimensions
            for columna in hoja.columns:
                hoja.column_dimensions[columna[0].column_letter].width = min(65, max(16, max(len(str(c.value or '')) for c in columna) + 2))
        libro.save(temporal)
        temporal.replace(ruta)
    finally:
        libro.close()
        temporal.unlink(missing_ok=True)


