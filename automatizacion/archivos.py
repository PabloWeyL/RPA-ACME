"""Validacion de tablas descargadas, sin dependencias de un sitio web."""
import csv
import io
from pathlib import Path

from openpyxl import load_workbook


class ContenidoHTML(ValueError):
    pass


def _validar_tabla(filas, columnas_requeridas=()):
    filas = iter(filas)
    cabecera = list(next(filas, ()))
    while cabecera and (cabecera[-1] is None or str(cabecera[-1]).strip() == ''):
        cabecera.pop()
    nombres = [str(c or '').strip().casefold() for c in cabecera]
    if len(nombres) < 2 or any(not c for c in nombres) or len(set(nombres)) != len(nombres):
        raise ValueError('El reporte no tiene una cabecera tabular valida')
    if not set(c.casefold() for c in columnas_requeridas).issubset(nombres):
        raise ValueError('Faltan columnas requeridas en el reporte')
    cantidad = 0
    for fila in filas:
        if not any(c is not None and str(c).strip() for c in fila):
            continue
        if len(fila) > len(cabecera) and not any(c is not None and str(c).strip() for c in fila[len(cabecera):]):
            fila = fila[:len(cabecera)]
        if len(fila) != len(cabecera):
            raise ValueError('El reporte contiene filas con columnas inconsistentes')
        cantidad += 1
    if not cantidad:
        raise ValueError('El reporte no contiene registros')


def validar_tabla(ruta, columnas_requeridas=()):
    ruta = Path(ruta)
    if not ruta.stat().st_size:
        raise ValueError('El archivo esta vacio')
    with ruta.open('rb') as archivo:
        inicio = archivo.read(4096).lstrip().lower()
    if b'<html' in inicio or b'<!doctype html' in inicio:
        raise ContenidoHTML('El archivo contiene HTML')
    extension = ruta.suffix.lower()
    if extension == '.csv':
        contenido = ruta.read_bytes()
        try:
            texto = contenido.decode('utf-8-sig')
        except UnicodeDecodeError:
            texto = contenido.decode('cp1252')
        if texto.lstrip().startswith(('{', '[', '<')) or '\x00' in texto:
            raise ValueError('La respuesta no es un CSV')
        try:
            dialecto = csv.Sniffer().sniff(texto[:8192], delimiters=',;\t|')
            _validar_tabla(csv.reader(io.StringIO(texto, newline=''), dialecto, strict=True), columnas_requeridas)
        except csv.Error as error:
            raise ValueError('CSV invalido') from error
    elif extension == '.xlsx':
        libro = load_workbook(ruta, read_only=True, data_only=True)
        try:
            _validar_tabla(libro.active.iter_rows(values_only=True), columnas_requeridas)
        finally:
            libro.close()
    elif extension == '.xls':
        import xlrd
        libro = xlrd.open_workbook(ruta, on_demand=True)
        try:
            hoja = libro.sheet_by_index(0)
            _validar_tabla((hoja.row_values(i) for i in range(hoja.nrows)), columnas_requeridas)
        finally:
            libro.release_resources()
    else:
        raise ValueError('Formato de reporte inesperado: ' + extension)
