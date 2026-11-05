"""Carpetas, respaldo ZIP por ejecucion y bloqueo de una instancia."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import os
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED


def rutas_ejecucion(config, identificador, fecha):
    fecha = date.fromisoformat(fecha)
    raiz = config.ruta_base / config.nombre_proceso / f'{fecha:%Y/%m/%d}'
    return {tipo: raiz / tipo / identificador for tipo in ('input', 'procesados', 'reportes', 'temporales')}


def preparar_carpetas(rutas):
    for ruta in rutas.values():
        ruta.mkdir(parents=True, exist_ok=True)


def respaldar(config, identificador, fecha, rutas):
    carpeta = config.ruta_historico / config.nombre_proceso
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f'respaldo_{config.nombre_proceso}_{identificador}_{fecha.replace("-", "_")}.zip'
    temporal = carpeta / ('.' + uuid4().hex + '.zip')
    try:
        with ZipFile(temporal, 'w', ZIP_DEFLATED) as archivo:
            for tipo in ('input', 'procesados', 'reportes'):
                for ruta in sorted(rutas[tipo].rglob('*')):
                    if ruta.is_symlink():
                        raise ValueError('No se permiten enlaces simbolicos en el respaldo')
                    if ruta.is_file():
                        archivo.write(ruta, str(Path(tipo) / ruta.relative_to(rutas[tipo])))
        with ZipFile(temporal) as archivo:
            if archivo.testzip() is not None:
                raise OSError('Respaldo ZIP corrupto')
        temporal.replace(destino)
    finally:
        temporal.unlink(missing_ok=True)
    return destino


@contextmanager
def bloqueo(ruta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open('a+b') as archivo:
        if archivo.tell() == 0:
            archivo.write(b'0')
            archivo.flush()
        archivo.seek(0)
        adquirido = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                adquirido = True
            except OSError:
                raise RuntimeError('Ya existe una ejecucion activa; no se encola otra instancia') from None
            yield
        finally:
            if adquirido:
                archivo.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)
