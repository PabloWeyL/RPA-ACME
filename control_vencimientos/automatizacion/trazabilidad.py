"""Log mensual Weyland con contexto y ocultacion de secretos y traceback."""
from datetime import datetime
import logging
from pathlib import Path
import re


class Contexto(logging.LoggerAdapter):
    def __init__(self, logger):
        super().__init__(logger, {'id_ejecucion': 'SIN_ID', 'modulo': 'INICIALIZACION'})
        self.secretos = []

    def ocultar(self, texto):
        for secreto in sorted(self.secretos, key=len, reverse=True):
            if secreto:
                texto = texto.replace(secreto, '[OCULTO]')
        return texto


def crear_logger():
    interno = logging.getLogger('weyland.control_vencimientos')
    cerrar_logger(interno)
    interno.setLevel(logging.INFO)
    interno.propagate = False
    contexto = Contexto(interno)
    class Formato(logging.Formatter):
        def format(self, record):
            return contexto.ocultar(super().format(record))
    formato = Formato('%(asctime)s [%(levelname)s] [CONTROL_VENCIMIENTOS] [ID:%(id_ejecucion)s] [%(modulo)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    interno.addHandler(consola)
    return contexto


def configurar_archivo(logger, config):
    config.ruta_logs.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now()
    destino = config.ruta_logs / f'{config.nombre_proceso.lower()}_{ahora:%Y_%m}.log'
    handler = logging.FileHandler(destino, encoding='utf-8')
    handler.setFormatter(logger.logger.handlers[0].formatter)
    logger.logger.addHandler(handler)
    # Solo logs propios de seis o mas meses; no limpia datos operativos.
    for ruta in config.ruta_logs.glob('control_vencimientos_????_??.log'):
        if ruta.is_symlink() or ruta.resolve().parent != config.ruta_logs.resolve():
            continue
        partes = re.fullmatch(r'control_vencimientos_(\d{4})_(\d{2})\.log', ruta.name)
        if partes:
            anio, mes = map(int, partes.groups())
            if 1 <= mes <= 12 and ahora.year * 12 + ahora.month - (anio * 12 + mes) >= 6:
                ruta.unlink()
                logger.info('Log antiguo eliminado: %s', ruta.name)


def cerrar_logger(logger):
    interno = logger.logger if isinstance(logger, logging.LoggerAdapter) else logger
    for handler in interno.handlers[:]:
        interno.removeHandler(handler)
        handler.close()
