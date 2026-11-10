"""Adaptador ACME del contrato cola_actual; el SDK conserva HTTP y checkpoints."""
from dataclasses import replace
import json
from pathlib import Path

from config.configuracion import cargar_configuracion
from integraciones.bloqueo import bloqueo_proyecto
from integraciones.proceso import consultar
from integraciones.proceso_mensual import descargar_reportes
from integraciones.trazabilidad import contexto, metadatos
from integraciones.weyland import ErrorWeyland, ResultadoBot
from integraciones.weyland.aplicacion_cola import ejecutar_cola_configurada
from integraciones.weyland.ejecutor_cola import ProcesoBot
from integraciones.weyland_notificaciones import NotificadorWeb


def crear_runner(comando, raiz):
    if comando not in ('consultar', 'descargar-reportes'):
        raise ValueError('Seleccionar consultar o descargar-reportes para ACME')

    def procesar(carpeta, entrada, parametros):
        if entrada is None:
            raise ErrorWeyland('ACME requiere un archivo de entrada')
        original = cargar_configuracion()
        datos = carpeta / 'datos'
        config = replace(original, base=datos, db=datos / 'acme.db',
            filtros=entrada, reportes_input=entrada, inputs=entrada.parent,
            descargas=datos / 'descargas', excel_salida=datos / 'reportes/resultados.xlsx',
            logs=carpeta / 'logs', capturas=carpeta / 'capturas', correo_activo=False)
        if not config.modulo_scraping:
            raise ErrorWeyland('MODULO_SCRAPING=false: no se ejecutara ACME')
        with bloqueo_proyecto(Path(raiz) / '.runtime/consulta.lock'):
            negocio = consultar if comando == 'consultar' else descargar_reportes
            resumen, reporte = negocio(config, entrada=entrada)
        if resumen.get('estado') != 'completado':
            raise ErrorWeyland('El negocio ACME no termino correctamente; revisar evidencia privada')
        archivos = [Path(reporte)]
        for ruta in sorted(config.descargas.rglob('*')):
            if ruta.is_file() and ruta.suffix.lower() in ('.pdf', '.csv', '.xlsx', '.xlsm', '.txt'):
                archivos.append(ruta)
        return ResultadoBot(tuple(archivos), detalles={'proyecto': 'acme', 'comando': comando,
            'ejecucion_local': resumen.get('id'), 'trazabilidad': metadatos()})
    return procesar


def ajustes_correo(valores, raiz):
    config = cargar_configuracion()
    return {'habilitado': config.correo_activo, 'inicio': config.correo_inicial,
        'nombre': config.nombre_proceso, 'host': config.smtp_server, 'puerto': config.smtp_port,
        'usuario': config.correo_usuario, 'password': config.correo_password,
        'remitente': config.correo_usuario, 'resultados': config.correo_destinatarios,
        'errores': config.correo_destinatarios_error,
        'limite': valores.get('MAX_ADJUNTOS_BYTES', '20971520')}


def ejecutar_actual(raiz, comando, valores, *, fabrica=None, runner=None, notificar=None):
    from config.api import crear_cliente_api
    codigo = str(valores.get('BACKEND_PROCESO_CODIGO') or '').strip()
    if not codigo:
        raise ValueError('Falta BACKEND_PROCESO_CODIGO registrado')
    negocio = runner or crear_runner(comando, raiz)
    def procesar(carpeta, entrada, parametros):
        with contexto(json.loads((carpeta / 'contexto.json').read_text(encoding='utf-8'))):
            return negocio(carpeta, entrada, parametros)
    correo = notificar if notificar is not None else NotificadorWeb(lambda: ajustes_correo(valores, raiz))
    return ejecutar_cola_configurada(fabrica or crear_cliente_api, valores, raiz,
        {codigo: ProcesoBot(procesar, True, ('.xlsx', '.xlsm'))}, notificar=correo)
