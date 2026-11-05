"""Lectura de inventario Excel/CSV y fechas confirmadas por una persona."""
import csv
from pathlib import Path
import unicodedata

from openpyxl import load_workbook

from integraciones.inventario import COLUMNAS, Registro, leer_cantidad, leer_fecha



def _nombre(valor):
    texto = unicodedata.normalize('NFKD', str(valor or '').strip().lower())
    return ''.join(c for c in texto if not unicodedata.combining(c))


def leer_filas(ruta):
    ruta = Path(ruta)
    if ruta.suffix.lower() == '.csv':
        with ruta.open(encoding='utf-8-sig', newline='') as archivo:
            muestra = archivo.read(8192)
            archivo.seek(0)
            try:
                dialecto = csv.Sniffer().sniff(muestra, delimiters=',;\t')
            except csv.Error:
                dialecto = csv.excel
            filas = list(csv.reader(archivo, dialecto, strict=True))
    elif ruta.suffix.lower() in ('.xlsx', '.xlsm'):
        libro = load_workbook(ruta, read_only=True, data_only=True)
        try:
            filas = list(libro.active.iter_rows(values_only=True))
        finally:
            libro.close()
    else:
        raise ValueError('Entrada no compatible; usar CSV UTF-8, XLSX o XLSM')
    if not filas:
        raise ValueError('El archivo esta vacio')
    cabecera = [_nombre(c) for c in filas[0]]
    nombres = [c for c in cabecera if c]
    if len(nombres) != len(set(nombres)):
        raise ValueError('La cabecera tiene columnas duplicadas')
    salida = []
    for numero, fila in enumerate(filas[1:], 2):
        if not any(c is not None and str(c).strip() for c in fila):
            continue
        if len(fila) > len(cabecera) and any(str(c or '').strip() for c in fila[len(cabecera):]):
            raise ValueError(f'Fila {numero}: hay valores sin encabezado')
        salida.append((numero, {nombre: fila[i] if i < len(fila) else None
                                for i, nombre in enumerate(cabecera) if nombre}))
    return cabecera, salida


def leer_inventario(ruta):
    cabecera, filas = leer_filas(ruta)
    if not set(COLUMNAS[:4]).issubset(cabecera):
        raise ValueError('Faltan columnas: codigo, producto, lote, cantidad')
    if not filas:
        raise ValueError('El inventario no contiene registros')
    registros, errores, vistos = [], [], set()
    for fila, datos in filas:
        try:
            codigo, producto, lote = (str(datos.get(c) or '').strip() for c in COLUMNAS[:3])
            if not all((codigo, producto, lote)):
                raise ValueError('Codigo, producto y lote son obligatorios')
            clave = (codigo, lote)
            if clave in vistos:
                raise ValueError('Codigo y lote duplicados; consolidar cantidades en una sola fila')
            cantidad = leer_cantidad(datos.get('cantidad'))
            fecha = leer_fecha(datos.get('fecha_vencimiento'))
            registros.append(Registro(codigo, producto, lote, cantidad, fecha, fila))
            vistos.add(clave)
        except ValueError as error:
            errores.append({'fila': fila, 'error': str(error)})
    return registros, errores


def leer_confirmaciones(ruta, claves):
    if ruta is None:
        return {}
    cabecera, filas = leer_filas(ruta)
    if not {'codigo', 'lote', 'fecha_confirmada'}.issubset(cabecera):
        raise ValueError('La revision necesita codigo, lote y fecha_confirmada')
    confirmadas = {}
    for fila, datos in filas:
        if not str(datos.get('fecha_confirmada') or '').strip():
            continue
        clave = tuple(str(datos.get(c) or '').strip() for c in ('codigo', 'lote'))
        if clave not in claves or clave in confirmadas:
            raise ValueError(f'Revision fila {fila}: codigo/lote desconocido o confirmado mas de una vez')
        confirmadas[clave] = leer_fecha(datos['fecha_confirmada'])
    return confirmadas
