import json
import logging
from pathlib import Path
import signal
import threading

from config.base_datos import BaseDatos
from config.configuracion import cargar_configuracion, configurar_logger
from integraciones.backend_api import ClienteBackend, EntradaInvalida, ErrorAPI, ErrorAPIPermanente
from integraciones.bloqueo import bloqueo_proyecto
from integraciones.filtros import leer_filtros
from integraciones.intercambio import HistorialIntercambio
from integraciones.proceso import consultar
from integraciones.proceso_mensual import descargar_reportes
from integraciones.historial_mensual import HistorialMensual
from integraciones.seguimiento_mensual import generar_reporte_mensual
from integraciones.reportes import generar_reporte
from integraciones.reportes_mensuales import leer_solicitudes


class Ejecutor:
    def __init__(self, config, cliente=None, procesos=None):
        self.config = config
        self.cliente = cliente if cliente is not None else ClienteBackend(config)
        self.historial = HistorialIntercambio(config)
        self.procesos = procesos if procesos is not None else {
            'consultar': consultar, 'descargar-reportes': descargar_reportes}

    def log(self, mensaje, *args, trabajo=None, nivel=logging.INFO):
        logger = logging.getLogger(self.config.nombre_proceso)
        if not logger.handlers:
            logger = configurar_logger(self.config, self.config.robot_id, modulo='WORKER')
        logger.log(nivel, mensaje, *args, extra={
            'id_ejecucion': trabajo['remoto_id'] if trabajo else self.config.robot_id,
            'modulo': 'WORKER'})

    def error(self, trabajo, error):
        self.historial.resultado(trabajo['id'], {
            'estado': 'error', 'error': self.config.ocultar(error),
            'ejecucion_id': trabajo.get('ejecucion_id')})

    def _preparar(self, trabajo):
        extension = Path(trabajo['nombre_archivo']).suffix.lower()
        if trabajo['proceso'] not in self.procesos:
            self.error(trabajo, 'Proceso no admitido: se requiere consultar o descargar-reportes')
            return
        if extension not in ('.xlsx', '.xlsm'):
            self.error(trabajo, 'El archivo de entrada debe ser .xlsx o .xlsm')
            return
        entrada = (self.config.ruta(self.config.inputs) / trabajo['id'] / ('entrada' + extension)).resolve()
        self.historial.actualizar(trabajo['id'], entrada=str(entrada))
        try:
            self.cliente.descargar(trabajo['remoto_id'], entrada)
            lector = leer_filtros if trabajo['proceso'] == 'consultar' else leer_solicitudes
            try:
                lector(entrada)
            except Exception:
                raise EntradaInvalida('Excel invalido: revise formato, hoja activa, encabezados y datos del proceso solicitado') from None
        except EntradaInvalida as error:
            self.error(trabajo, error)
            return
        self.historial.actualizar(trabajo['id'], estado='preparado')
        self.log('Excel de entrada recibido y validado', trabajo=trabajo)

    def _ejecutar(self, trabajo):
        with bloqueo_proyecto(self.config.root / '.runtime/consulta.lock'):
            if not self.config.modulo_scraping:
                raise RuntimeError('MODULO_SCRAPING=false: el trabajo permanece pendiente')
            self.historial.actualizar(trabajo['id'], estado='ejecutando')
            self.log('Iniciando proceso %s', trabajo['proceso'], trabajo=trabajo)
            try:
                resumen, reporte = self.procesos[trabajo['proceso']](
                    self.config, entrada=Path(trabajo['entrada']),
                    al_iniciar=lambda identificador: self.historial.actualizar(
                        trabajo['id'], ejecucion_id=identificador))
                self.historial.resultado(trabajo['id'], resumen, reporte)
            except Exception as error:
                actual = self.historial.obtener(trabajo['id'])
                self._recuperar(actual, error=self.config.ocultar(error))

    def _recuperar(self, trabajo, error=None):
        identificador = trabajo['ejecucion_id']
        if not identificador:
            self.error(trabajo, error or 'Ejecucion interrumpida antes de registrar su identificador; no se repetira automaticamente')
            return
        resumen, reporte = None, None
        try:
            if trabajo['proceso'] == 'consultar':
                base = BaseDatos(self.config.ruta(self.config.db))
                with base.conexion() as con:
                    fila = con.execute('SELECT * FROM ejecuciones WHERE id=?', (identificador,)).fetchone()
                if fila is None:
                    raise RuntimeError('No se encontro la ejecucion vinculada')
                resumen = dict(fila)
                resumen.pop('reporte_pendiente', None)
                if resumen['estado'] == 'en_proceso':
                    resumen = base.finalizar(identificador, error or 'Ejecucion interrumpida; se conserva el resultado parcial')
                reporte = generar_reporte(base, identificador, self.config.carpeta_reportes)
                base.confirmar_reporte(identificador)
            else:
                historial = HistorialMensual(self.config.ruta(self.config.db))
                resumen = historial.resumen(identificador)
                if resumen['estado'] == 'en_proceso':
                    resumen = historial.finalizar(identificador, error or 'Ejecucion interrumpida; se conserva el resultado parcial')
                reporte = generar_reporte_mensual(historial, identificador, self.config.carpeta_reportes)
                historial.confirmar_reporte(identificador)
        except Exception as fallo_reporte:
            error = error or self.config.ocultar(fallo_reporte)
        if resumen is None:
            resumen = {'estado': 'error', 'ejecucion_id': identificador}
        if error:
            resumen.update(estado='error', error=error)
        self.historial.resultado(trabajo['id'], resumen, reporte)
        self.log('Resultado recuperado sin repetir el RPA', trabajo=trabajo)

    def _entregar(self, trabajo):
        resumen = json.loads(trabajo['resumen_json'])
        reporte = Path(trabajo['reporte']) if trabajo['reporte'] else None
        if reporte and not reporte.is_file():
            resumen.update(estado='error', error='No se encuentra el Excel de seguimiento para entregar')
            self.historial.resultado(trabajo['id'], resumen)
            reporte = None
        self.cliente.enviar_resultado(trabajo['remoto_id'], resumen, reporte)
        self.historial.actualizar(trabajo['id'], estado='entregado', ultimo_error=None)
        self.log('Resultado aceptado por el backend: %s', resumen['estado'], trabajo=trabajo)

    def ciclo(self):
        trabajo = self.historial.pendiente()
        if trabajo is None:
            solicitud = self.cliente.tomar()
            if solicitud is None:
                return False
            try:
                trabajo = self.historial.registrar(solicitud)
            except ValueError as error:
                raise ErrorAPIPermanente(str(error)) from None
            self.log('Solicitud recibida: %s', trabajo['proceso'], trabajo=trabajo)
        try:
            if trabajo['estado'] == 'descargando':
                self._preparar(trabajo)
            elif trabajo['estado'] == 'preparado':
                self._ejecutar(trabajo)
            elif trabajo['estado'] == 'ejecutando':
                with bloqueo_proyecto(self.config.root / '.runtime/consulta.lock'):
                    self._recuperar(trabajo)
            elif trabajo['estado'] == 'pendiente_envio':
                self._entregar(trabajo)
        except Exception as error:
            self.historial.actualizar(trabajo['id'], ultimo_error=self.config.ocultar(error))
            raise
        return True


def ejecutar(config, detener=None, cliente=None):
    detener = detener if detener is not None else threading.Event()
    espera_error = 5
    with bloqueo_proyecto(config.root / '.runtime/worker.lock'):
        ejecutor = Ejecutor(config, cliente=cliente)
        try:
            ejecutor.log('Ejecutor iniciado; consulta saliente de solicitudes pendientes')
            while not detener.is_set():
                try:
                    actividad = ejecutor.ciclo()
                    espera_error = 5
                    if not actividad:
                        detener.wait(config.intervalo_consulta_segundos)
                except ErrorAPIPermanente:
                    raise
                except (ErrorAPI, OSError, RuntimeError) as error:
                    ejecutor.log('%s; nuevo intento en %s segundos', config.ocultar(error), espera_error,
                                 nivel=logging.WARNING)
                    detener.wait(espera_error)
                    espera_error = min(60, espera_error * 2)
        finally:
            ejecutor.cliente.cerrar()
            ejecutor.log('Ejecutor detenido; los trabajos y envios pendientes se conservan')


def main():
    config = None
    detener = threading.Event()
    previo = signal.signal(signal.SIGINT, lambda *_: detener.set())
    try:
        config = cargar_configuracion()
        ejecutar(config, detener)
        return 0
    except Exception as error:
        if config is not None:
            logger = configurar_logger(config, config.robot_id or 'SIN_ID', modulo='WORKER')
            logger.error('Ejecutor detenido por error: %s', config.ocultar(error))
        else:
            print('No se pudo cargar la configuracion del ejecutor')
        return 1
    finally:
        signal.signal(signal.SIGINT, previo)
        if config is not None:
            logger = logging.getLogger(config.nombre_proceso)
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
                handler.close()


if __name__ == '__main__':
    raise SystemExit(main())
