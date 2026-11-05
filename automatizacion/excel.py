"""Escritura atomica de reportes tabulares con varias hojas."""
from pathlib import Path
from datetime import datetime
import hashlib
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


def ruta_reporte(carpeta, prefijo, ejecucion, inicio):
    """Nombre estable al recuperar una ejecucion y distinto para corridas simultaneas."""
    fecha_hora = datetime.fromisoformat(inicio).strftime('%d%m%Y-%H%M%S')
    identificador = hashlib.sha256(str(ejecucion).encode()).hexdigest()[:12]
    return Path(carpeta) / f'{prefijo}_{fecha_hora}_{identificador}.xlsx'


def escribir_reporte(destino, hojas):
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name('.' + destino.stem + '_' + uuid4().hex + '.xlsx')
    libro = Workbook()
    libro.remove(libro.active)
    try:
        for nombre, encabezados, filas in hojas:
            hoja = libro.create_sheet(nombre)
            hoja.append(list(encabezados))
            for fila in filas:
                hoja.append(list(fila))
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
                hoja.column_dimensions[columna[0].column_letter].width = min(70, max(16, max(len(str(c.value or '')) for c in columna) + 2))
        libro.save(temporal)
        temporal.replace(destino)
        return destino
    finally:
        libro.close()
        temporal.unlink(missing_ok=True)
