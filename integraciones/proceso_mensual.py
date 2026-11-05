"""Historial, reintentos y reporte de las descargas mensuales."""
from automatizacion.ejecucion import finalizar_con_reporte, reintentar
from automatizacion.procesos import CanalInterrumpido
import time
from uuid import uuid4


from integraciones.historial_mensual import HistorialMensual
from integraciones.seguimiento_mensual import generar_reporte_mensual
from config.configuracion import configurar_logger
from integraciones.navegador import ErrorAutenticacion, Navegador
from integraciones.notificaciones import Notificador
from integraciones.sesion import con_sesion
from integraciones.reportes_mensuales import (
    ReportesMensuales, SinReporte, SolicitudMensual, SolicitudRechazada, leer_solicitudes,
)


def descargar_reportes(config, entrada=None, limite=None, fabrica_navegador=Navegador, pausa=time.sleep, fabrica_notificador=Notificador, al_iniciar=None):
    historial = HistorialMensual(config.ruta(config.db))
    archivo = config.ruta(entrada if entrada is not None else config.reportes_input)
    ejecucion = historial.iniciar(archivo, limite)
    if al_iniciar is not None:
        al_iniciar(ejecucion)
    logger = configurar_logger(config, ejecucion, modulo='REPORTES_MENSUALES')
    notificador = fabrica_notificador(config, logger, 'Reportes mensuales', ejecucion, archivo)
    navegador, fallo = None, None
    try:
        notificador.inicio()
        solicitudes = leer_solicitudes(archivo)
        if limite is not None:
            solicitudes = solicitudes[:limite]
        historial.encolar(ejecucion, solicitudes)
        notificador.seleccionar(len(solicitudes))
        logger.info('Solicitudes mensuales seleccionadas: %s', len(solicitudes))
        navegador = fabrica_navegador(config)
        navegador.abrir()
        navegador.login()
        cliente = ReportesMensuales(navegador)
        for tarea in historial.tareas(ejecucion):
            solicitud = SolicitudMensual(**{c: tarea[c] for c in ('proveedor_taxid', 'anio', 'mes', 'fila')})
            def descargar():
                carpeta = config.ruta(config.descargas) / ejecucion / str(tarea['id']) / ('intento_' + uuid4().hex[:8])
                return cliente.descargar(solicitud, carpeta)
            try:
                destino = reintentar(
                    lambda: con_sesion(navegador, descargar), config.max_intentos,
                    antes=lambda numero: historial.intento(tarea['id'], numero),
                    reintentable=lambda error: not isinstance(error, (SinReporte, SolicitudRechazada, ErrorAutenticacion, CanalInterrumpido)),
                    al_reintentar=lambda numero, error: logger.info('Reintento %s para fila %s, mes %s', numero, solicitud.fila, solicitud.mes),
                    pausa=pausa,
                )
                historial.resolver(tarea['id'], 'descargado', archivo=destino)
                notificador.resolver('descargado')
                logger.info('Fila %s, %s-%02d: descargado', solicitud.fila, solicitud.anio, solicitud.mes)
            except SinReporte as error:
                historial.resolver(tarea['id'], 'sin_reporte', error=config.ocultar(error))
                notificador.resolver('sin_reporte')
                logger.info('Fila %s, mes %s: sin reporte disponible', solicitud.fila, solicitud.mes)
            except Exception as error:
                captura = navegador.captura('mensuales_' + ejecucion, tarea['id'])
                historial.resolver(tarea['id'], 'error', error=config.ocultar(error), captura=captura)
                notificador.resolver('error')
                logger.error('Fila %s, mes %s: %s', solicitud.fila, solicitud.mes, config.ocultar(error))
                if isinstance(error, (ErrorAutenticacion, CanalInterrumpido)):
                    raise
    except Exception as error:
        fallo = config.ocultar(error)
        logger.error('Ejecucion interrumpida: %s', fallo)
    finally:
        if navegador is not None:
            try:
                navegador.cerrar()
            except Exception as error:
                logger.warning('No se confirmo el cierre: %s', config.ocultar(error))
    try:
        resumen, reporte = finalizar_con_reporte(
            lambda error: historial.finalizar(ejecucion, error),
            lambda: generar_reporte_mensual(historial, ejecucion, config.carpeta_reportes),
            notificador, config.ocultar, fallo,
            confirmar_reporte=lambda: historial.confirmar_reporte(ejecucion),
        )
        logger.info('Estado: %s; descargados: %s; sin reporte: %s; errores: %s', resumen['estado'], resumen['descargado'], resumen['sin_reporte'], resumen['errores'])
        return resumen, reporte
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
