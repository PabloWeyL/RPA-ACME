"""Validaciones operativas previas al login y la consulta de la cola web."""
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import NamedTemporaryFile

from integraciones.weyland.archivos import bloqueo
from integraciones.weyland.aplicacion_cola import verificar_migracion
from integraciones.weyland.cola_actual import entero_configurado
from integraciones.weyland.configuracion import validar_url


class ErrorArranque(RuntimeError):
    pass


def probar_directorio(ruta):
    ruta = Path(ruta)
    ruta.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=ruta, prefix='verificacion_', suffix='.tmp'):
        pass
    return ruta


def validar_backend(valores, raiz):
    if valores.get('BACKEND_CONTRATO') != 'cola_actual' or valores.get('BACKEND_AUTH_MODE') != 'weyland':
        raise ErrorArranque('El arranque automatico exige BACKEND_CONTRATO=cola_actual y BACKEND_AUTH_MODE=weyland')
    maquina = entero_configurado(valores, 'BACKEND_MAQUINA_ID')
    if not str(valores.get('BACKEND_PROCESO_CODIGO') or '').strip():
        raise ErrorArranque('Configurar BACKEND_PROCESO_CODIGO registrado antes de ejecutar')
    if str(valores.get('BACKEND_REUTILIZAR_ULTIMA') or 'false').lower() != 'false':
        raise ErrorArranque('No se reutilizan ejecuciones anteriores')
    permitir = str(valores.get('BACKEND_PERMITIR_HTTP_LOCAL') or 'false').lower()
    if permitir not in ('true', 'false'):
        raise ErrorArranque('BACKEND_PERMITIR_HTTP_LOCAL debe ser true o false')
    url = str(valores.get('BACKEND_API_URL') or '').rstrip('/')
    login = str(valores.get('BACKEND_LOGIN_URL') or url + '/auth/login')
    if validar_url(url, permitir == 'true') != validar_url(login, permitir == 'true'):
        raise ErrorArranque('Login y API deben estar en el mismo origen')
    ruta = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
    base = (ruta if ruta.is_absolute() else Path(raiz) / ruta).resolve()
    if str(valores.get('ENTORNO') or '').upper() == 'PRODUCCION' and base.is_relative_to(Path(raiz).resolve()):
        raise ErrorArranque('En produccion BACKEND_TRABAJOS_DIR debe estar fuera del repositorio')
    verificar_migracion(base)
    return base / 'cola_actual' / ('maquina_' + str(maquina))


def validar_modulos(nombres):
    if sys.version_info < (3, 11):
        raise ErrorArranque('Se requiere Python 3.11 o posterior')
    for nombre in nombres:
        try:
            disponible = importlib.util.find_spec(nombre) is not None
        except (ImportError, ValueError):
            disponible = False
        if not disponible:
            raise ErrorArranque('Falta dependencia: ' + nombre + '; instalar requirements.txt')


def validar_chrome():
    candidatos = [os.getenv('CHROME_PATH'), shutil.which('google-chrome'), shutil.which('chromium'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        str(Path(os.getenv('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe')]
    if not any(p and Path(p).is_file() for p in candidatos):
        raise ErrorArranque('No se encontro Chrome; instalarlo o configurar CHROME_PATH')


def validar_node():
    nodo = shutil.which('node')
    if not nodo:
        raise ErrorArranque('Falta Node.js 22 o posterior en PATH')
    respuesta = subprocess.run([nodo, '--version'], capture_output=True, text=True, timeout=10, check=True)
    if int(respuesta.stdout.strip().lstrip('v').split('.')[0]) < 22:
        raise ErrorArranque('Se requiere Node.js 22 o posterior')


def revisar_integridad(ruta):
    # Solo lectura: nunca crear, migrar ni vaciar una base como precondicion.
    with closing(sqlite3.connect(Path(ruta).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as conexion:
        if conexion.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ErrorArranque('SQLite no supera quick_check; revisar antes de ejecutar')
        if conexion.execute('PRAGMA foreign_key_check').fetchall():
            raise ErrorArranque('SQLite contiene referencias invalidas')


def revisar_bases(base):
    for ruta in sorted(base.rglob('*')) if base.exists() else ():
        if ruta.is_file() and ruta.suffix.lower() in ('.db', '.sqlite', '.sqlite3'):
            if ruta.is_symlink():
                raise ErrorArranque('Base operativa enlazada; requiere revision')
            revisar_integridad(ruta)


def ejecutar_automatico(raiz, valores, validar, proceso):
    raiz = Path(raiz).resolve()
    # La configuracion incompleta falla antes de crear evidencia o autenticar.
    base = validar_backend(valores, raiz)
    (raiz / '.runtime').mkdir(parents=True, exist_ok=True)
    with bloqueo(raiz / '.runtime/arranque.lock'):
        print('[1/3] Validando configuracion y dependencias', flush=True)
        ajustes = validar()
        print('[2/3] Comprobando directorios y bases SQLite existentes', flush=True)
        probar_directorio(ajustes['logs'])
        probar_directorio(base)
        revisar_bases(base)
        print('[3/3] Iniciando login y consulta al backend; entradas locales no se cargan', flush=True)
        return proceso()
