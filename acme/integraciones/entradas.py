"""Clasifica los Excel de entrada por los encabezados de su hoja activa."""
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from integraciones.filtros import normalizar_encabezado


COLUMNAS = {
    'consultar': ('tipo', 'estado'),
    'descargar-reportes': ('proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'),
}


@dataclass(frozen=True)
class Entrada:
    ruta: Path
    comando: str


def descubrir_entradas(carpeta, comando='procesar'):
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        raise ValueError('No existe la carpeta de entrada: ' + str(carpeta))
    tipos = COLUMNAS if comando == 'procesar' else {comando: COLUMNAS[comando]}
    entradas, omitidos = [], []
    for ruta in sorted(carpeta.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
        if not ruta.is_file() or ruta.name.startswith('~$'):
            continue
        if ruta.suffix.lower() not in ('.xlsx', '.xlsm'):
            if ruta.suffix.lower() == '.xls':
                omitidos.append((ruta, 'formato .xls no compatible; guardar como .xlsx'))
            continue
        try:
            libro = load_workbook(ruta, read_only=True, data_only=True)
            try:
                fila = next(libro.active.iter_rows(min_row=1, max_row=1, values_only=True), ())
                cabecera = [normalizar_encabezado(celda) for celda in fila]
            finally:
                libro.close()
        except Exception:
            omitidos.append((ruta, 'no se pudo abrir como Excel valido'))
            continue
        compatibles = [tipo for tipo, columnas in tipos.items()
                       if all(cabecera.count(columna) == 1 for columna in columnas)]
        if not compatibles:
            omitidos.append((ruta, 'la hoja activa no tiene encabezados compatibles y unicos para el comando'))
        entradas.extend(Entrada(ruta, tipo) for tipo in compatibles)
    return entradas, omitidos
