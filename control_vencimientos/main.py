"""Punto de entrada batch Weyland: inicializacion, pipeline, error y cierre."""
import argparse
from pathlib import Path

from configuracion import cargar_configuracion
from automatizacion.operacion import bloqueo
from automatizacion.reintentos import reintentar
from automatizacion.trazabilidad import crear_logger, configurar_archivo, cerrar_logger
from config.base_datos import BaseDatos
from integraciones.correo import notificar_error
from integraciones.ejecucion import ejecutar_pipeline
from integraciones.monitor import obtener_parametros
from integraciones.nube import obtener_secretos, es_transitorio
from integraciones.reportes import crear_plantilla


def main(argv=None):
    parser = argparse.ArgumentParser(description='Control de vencimientos - Estandar Weyland')
    comandos = parser.add_subparsers(dest='comando')
    plantilla = comandos.add_parser('plantilla')
    plantilla.add_argument('destino', type=Path)
    control = comandos.add_parser('procesar')
    control.add_argument('entrada', type=Path, nargs='?')
    control.add_argument('--revisiones', type=Path)
    control.add_argument('--prueba-local', action='store_true', help='Simular Key Vault/Monitor solo en desarrollo')
    control.add_argument('--parametros-prueba', type=Path, help='JSON explicito de parametros de negocio de prueba')
    control.add_argument('--id-ejecucion', help='Reanudar la ejecucion indicada; no crea un reproceso nuevo')
    parser.set_defaults(entrada=None, revisiones=None, prueba_local=False, parametros_prueba=None, id_ejecucion=None)
    args = parser.parse_args(argv)
    if args.comando == 'plantilla':
        try:
            crear_plantilla(args.destino)
            return 0
        except Exception as error:
            print('No se pudo crear la plantilla:', error)
            return 1
    logger = crear_logger()
    config, secretos, base = None, {}, None
    try:
        logger.info('Inicio tecnico')
        config = cargar_configuracion(prueba_local=args.prueba_local)
        if args.id_ejecucion:
            from dataclasses import replace
            import re
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.id_ejecucion):
                raise ValueError('ID_EJECUCION invalido')
            config = replace(config, id_ejecucion=args.id_ejecucion)
        configurar_archivo(logger, config)
        if not config.modulo_procesamiento:
            logger.extra['modulo'] = 'PROCESAMIENTO'
            logger.info('Modulo 1 desactivado por configuracion')
            return 0
        with bloqueo(config.raiz / 'temporales/ejecucion.lock'):
            logger.extra['modulo'] = 'KEY_VAULT'
            if not config.prueba_local:
                secretos = reintentar(lambda: obtener_secretos(config), config, logger,
                                      lambda e: es_transitorio(e) or getattr(e, 'status_code', 0) in (429, 500, 502, 503, 504))
                logger.secretos.extend(secretos.values())
            else:
                logger.warning('PRUEBA LOCAL: servicios Azure simulados explicitamente; no es produccion')
            logger.extra['modulo'] = 'MONITOR'
            parametros = reintentar(lambda: obtener_parametros(config, secretos, args.parametros_prueba),
                                    config, logger, es_transitorio)
            logger.extra['modulo'] = 'BASE_DATOS'
            base = reintentar(lambda: BaseDatos(config, secretos), config, logger, es_transitorio)
            # ID se resuelve en el pipeline antes de crear carpetas operativas.
            ejecutar_pipeline(config, parametros, base, logger,
                              args.entrada.resolve() if args.entrada else config.entrada,
                              args.revisiones.resolve() if args.revisiones else None)
        return 0
    except Exception as error:
        logger.exception('Error critico: %s', error)
        identificador = logger.extra['id_ejecucion']
        if base is not None and identificador != 'SIN_ID':
            try:
                base.estado(identificador, 'ERROR', logger.ocultar(str(error)))
            except Exception:
                logger.exception('No se pudo persistir ERROR; requiere revision manual')
        if config is not None:
            notificar_error(config, secretos, logger, identificador, logger.extra['modulo'], error)
        return 1
    finally:
        logger.info('Cierre de recursos')
        cerrar_logger(logger)


if __name__ == '__main__':
    raise SystemExit(main())
