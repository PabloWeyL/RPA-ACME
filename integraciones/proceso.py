from automatizacion.ejecucion import finalizar_con_reporte, reintentar
from automatizacion.procesos import CanalInterrumpido
from dataclasses import asdict
import json
import time

from config.base_datos import BaseDatos
from config.configuracion import configurar_logger
from integraciones.filtros import leer_filtros, seleccionar_items
from integraciones.navegador import ErrorAutenticacion, Navegador
from integraciones.notificaciones import Notificador
from integraciones.reportes import generar_reporte
from integraciones.work_items import WorkItems
from integraciones.sesion import con_sesion


def consultar(config, limite=None, fabrica_navegador=Navegador, pausa=time.sleep, fabrica_notificador=Notificador, entrada=None, al_iniciar=None):
    base = BaseDatos(config.ruta(config.db))
    archivo = config.ruta(entrada if entrada is not None else config.filtros)
    ejecucion, archivo_id = base.iniciar(archivo, limite)
    if al_iniciar is not None:
        al_iniciar(ejecucion)
    logger = configurar_logger(config, ejecucion)
    notificador = fabrica_notificador(config, logger, "Work Items", ejecucion, archivo)
    navegador = None
    fallo = None
    try:
        notificador.inicio()
        try:
            filtros = leer_filtros(archivo)
            base.archivo_estado(archivo_id, "leido", json.dumps([asdict(filtro) for filtro in filtros]))
        except Exception as error:
            base.archivo_estado(archivo_id, "error", error=config.ocultar(error))
            raise
        navegador = fabrica_navegador(config)
        navegador.abrir()
        navegador.login()
        cliente = WorkItems(navegador)
        listado = con_sesion(navegador, cliente.listar)
        coincidencias = seleccionar_items(listado, filtros)
        seleccionados = coincidencias[:limite] if limite is not None else coincidencias
        base.encolar(ejecucion, archivo_id, seleccionados, len(coincidencias))
        notificador.seleccionar(len(seleccionados))
        logger.info("Encontradas %s coincidencias; seleccionadas %s", len(coincidencias), len(seleccionados))
        for tarea in base.tareas(ejecucion):
            try:
                detalle = reintentar(
                    lambda: con_sesion(navegador, lambda: cliente.detalle(tarea)),
                    config.max_intentos,
                    antes=lambda numero: base.intento(tarea["id"], numero),
                    reintentable=lambda error: not isinstance(error, (ErrorAutenticacion, CanalInterrumpido)),
                    al_reintentar=lambda numero, error: logger.info("Reintento %s para Work Item %s", numero, tarea["wiid"]),
                    pausa=pausa,
                )
                base.guardar(tarea, detalle)
                notificador.resolver("leido")
                logger.info("Work Item %s consultado", tarea["wiid"])
            except Exception as error:
                captura = navegador.captura(ejecucion, tarea["wiid"])
                base.fallar_tarea(tarea["id"], config.ocultar(error), captura)
                notificador.resolver("error")
                logger.error("Work Item %s: %s", tarea["wiid"], config.ocultar(error))
                if isinstance(error, (ErrorAutenticacion, CanalInterrumpido)):
                    raise
    except Exception as error:
        fallo = config.ocultar(error)
        logger.error("Ejecucion interrumpida: %s", fallo)
    finally:
        if navegador is not None:
            try:
                navegador.cerrar()
            except Exception as error:
                logger.warning("No se confirmo el cierre: %s", config.ocultar(error))
    try:
        resumen, reporte = finalizar_con_reporte(
            lambda error: base.finalizar(ejecucion, error),
            lambda: generar_reporte(base, ejecucion, config.carpeta_reportes),
            notificador, config.ocultar, fallo,
            confirmar_reporte=lambda: base.confirmar_reporte(ejecucion),
        )
        logger.info("Estado: %s; leidos: %s; errores: %s; reporte: %s", resumen["estado"], resumen["leidos"], resumen["errores"], reporte)
        return resumen, reporte
    finally:
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
