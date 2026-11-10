"""Adaptador de negocio: la conexion y el journal viven en integraciones.weyland."""
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from integraciones.weyland import ErrorWeyland, ResultadoBot, ejecutar_solicitud, ejecutar_configurado
from integraciones.weyland.archivos import guardar_json


def ejecutar_subproceso(proyecto, comando, raiz, carpeta, entrada):
    datos = carpeta / 'datos'
    salida = datos / 'reportes'
    salida.mkdir(parents=True, exist_ok=True)
    variables = {**os.environ,
        'RUTA_BASE': str(datos), 'RUTA_HISTORICO': str(carpeta / 'historico'),
        'RUTA_LOGS': str(carpeta / 'logs'), 'CARPETA_INPUTS': str(datos / 'input'),
        'EXCEL_ENTRADA': str(entrada or datos / 'input'), 'EXCEL_SALIDA': str(salida / 'resultados.xlsx'),
        'CARPETA_CAPTURAS': str(carpeta / 'capturas'), 'CARPETA_DESCARGAS': str(datos / 'descargas'),
        'DB_PATH': str(datos / 'historial.db'), 'ID_EJECUCION': ''}
    args = [comando]
    if proyecto == 'vencimientos':
        variables['DB_SQLITE'] = 'weyland_' + carpeta.name + '.db'
        args = ['procesar', str(entrada)]
    elif proyecto == 'supermercados' and entrada is None:
        args += ['--productos-json', str(datos / 'parametros' / 'productos.json')]
    with (carpeta / 'proceso.log').open('ab') as log:
        return subprocess.run([sys.executable, str(raiz / 'main.py'), *args], cwd=raiz,
            env=variables, stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def artefactos(carpeta):
    datos = carpeta / 'datos'
    return tuple(p for p in sorted(datos.rglob('*')) if p.is_file() and not p.is_symlink()
        and p.resolve().is_relative_to(datos.resolve())
        and p.suffix.lower() in ('.xlsx', '.xlsm', '.csv', '.json', '.txt')
        and any(c in ('reportes', 'descargas') for c in p.relative_to(datos).parts))


def exportar(carpeta):
    """Utilidad de negocio conservada; la entrega web usa el ZIP del coordinador comun."""
    archivos = artefactos(carpeta)
    if not archivos:
        raise ErrorWeyland('El proceso termino sin generar un export')
    if len(archivos) == 1:
        return archivos[0]
    destino = carpeta / 'resultado.zip'
    with ZipFile(destino, 'w', ZIP_DEFLATED) as paquete:
        for archivo in archivos:
            paquete.write(archivo, archivo.relative_to(carpeta / 'datos').as_posix())
    return destino


def crear_runner(proyecto, comando, raiz, runner=None):
    if proyecto not in ('acme', 'supermercados', 'vencimientos'):
        raise ValueError('Proyecto de negocio no admitido')
    permitidos = ('consultar', 'descargar-reportes', 'procesar') if proyecto == 'acme' else ('procesar',)
    if comando not in permitidos:
        raise ValueError('Comando de negocio no admitido')
    raiz = Path(raiz).resolve()
    ejecutar = runner or ejecutar_subproceso
    def procesar(carpeta, entrada, parametros):
        if entrada is None:
            if proyecto != 'supermercados':
                raise ErrorWeyland('Este bot necesita un archivo de entrada')
            destino = carpeta / 'datos' / 'parametros' / 'productos.json'
            destino.parent.mkdir(parents=True, exist_ok=True)
            productos = parametros.get('productos', [parametros] if parametros.get('producto') else [])
            if isinstance(productos, str):
                productos = json.loads(productos)
            guardar_json(destino, {'productos': productos})
            from integraciones.productos_json import leer_productos_json
            leer_productos_json(destino)
        if ejecutar(proyecto, comando, raiz, carpeta, entrada) != 0:
            raise ErrorWeyland('El proceso de negocio finalizo con errores')
        # El resumen generico representa una invocacion del proceso, no el conteo de filas.
        return ResultadoBot(artefactos(carpeta), detalles={'proyecto': proyecto, 'comando': comando})
    return procesar


def ejecutar_uno(cliente, proyecto, comando, raiz, trabajos, codigo, runner=None, *, solicitud_id):
    resultado = ejecutar_solicitud(cliente, solicitud_id, codigo, trabajos,
        crear_runner(proyecto, comando, raiz, runner), requiere_input=None if proyecto == 'supermercados' else True,
        extensiones=('.xlsx', '.xlsm', '.csv') if proyecto == 'vencimientos' else ('.xlsx', '.xlsm'),
        contexto=proyecto + ':' + comando)
    return 0 if resultado['fase'] == 'COMPLETADA' else 1


def ejecutar_desde_config(proyecto, raiz, comando='procesar', solicitud_id=None):
    from config.api import crear_cliente_api
    from config.weyland import leer_valores
    valores = leer_valores()
    modo = str(valores.get('BACKEND_CONTRATO') or 'por_id').strip().lower()
    if modo == 'cola_actual':
        if solicitud_id is not None:
            raise ValueError('cola_actual recibe el ID del backend; no enviar --id-solicitud')
        from integraciones.backend_actual import ejecutar_actual
        estado = ejecutar_actual(raiz, comando, valores)
        print('Weyland Automate:', estado['fase'], 'ID:', estado.get('ejecucion_id', 'SIN_TRABAJO'))
        return 0 if estado['fase'] == 'COMPLETADA' else 1
    if modo != 'por_id':
        raise ValueError('BACKEND_CONTRATO debe ser por_id o cola_actual')
    resultado = ejecutar_configurado(crear_cliente_api, valores, raiz, solicitud_id,
        crear_runner(proyecto, comando, raiz), requiere_input=None if proyecto == 'supermercados' else True,
        extensiones=('.xlsx', '.xlsm', '.csv') if proyecto == 'vencimientos' else ('.xlsx', '.xlsm'),
        contexto=proyecto + ':' + comando)
    return 0 if resultado['fase'] == 'COMPLETADA' else 1
