"""Avisos de ejecucion por Gmail. Los fallos de correo no interrumpen el RPA."""
from datetime import datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
import smtplib
import ssl
import time


MAX_ADJUNTO = 15 * 1024 * 1024


def enviar_gmail(config, mensaje):
    if config.smtp_port not in (465, 587):
        raise ValueError('SMTP_PORT debe ser 465 (TLS directo) o 587 (STARTTLS)')
    contexto = ssl.create_default_context()
    conexion = (smtplib.SMTP_SSL(config.smtp_server, 465, timeout=10, context=contexto)
                if config.smtp_port == 465 else smtplib.SMTP(config.smtp_server, 587, timeout=10))
    with conexion as servidor:
        if config.smtp_port == 587:
            servidor.ehlo()
            servidor.starttls(context=contexto)
            servidor.ehlo()
        servidor.login(config.correo_usuario, config.correo_password.replace(' ', ''))
        destinatarios = [direccion for _, direccion in getaddresses(mensaje.get_all('To', []))]
        rechazados = servidor.send_message(mensaje, from_addr=config.correo_usuario,
                                          to_addrs=destinatarios or list(config.correo_destinatarios))
        if rechazados:
            raise RuntimeError('Gmail rechazo algunos destinatarios; no se reintentara el envio')


class Notificador:
    def __init__(self, config, logger, proceso, ejecucion, archivo, enviar=enviar_gmail):
        self.config = config
        self.logger = logger
        self.proceso = proceso
        self.ejecucion = ejecucion
        self.archivo = Path(archivo)
        self.enviar = enviar
        self.iniciado = datetime.now().astimezone()
        self.reloj = time.monotonic()
        self.total = 0
        self.conteos = {'leido': 0, 'descargado': 0, 'sin_reporte': 0, 'error': 0}
        self.final_enviado = False
        self.activo = config.correo_activo
        if self.activo:
            try:
                if not config.correo_usuario or not config.correo_password.strip() or not (config.correo_destinatarios or config.correo_destinatarios_error):
                    raise ValueError('faltan usuario, contrasena de aplicacion o destinatarios')
                if not config.smtp_server or config.smtp_port not in (465, 587):
                    raise ValueError('SMTP_SERVER o SMTP_PORT invalidos')
                for direccion in (config.correo_usuario, *config.correo_destinatarios, *config.correo_destinatarios_error):
                    if '\n' in direccion or '\r' in direccion:
                        raise ValueError('direccion de correo invalida')
                    parsed = Address(addr_spec=direccion)
                    if not parsed.username or not parsed.domain:
                        raise ValueError('direccion de correo invalida')
            except Exception:
                self.activo = False
                self.logger.warning('Correo desactivado para esta ejecucion: configuracion incompleta o invalida; revise SMTP_*, EMAIL_* y las listas de distribucion')

    def inicio(self):
        if self.config.correo_inicial:
            self._enviar('Inicio', self._cabecera() + '\nEl proceso ha comenzado.')

    def seleccionar(self, total):
        self.total = total

    def resolver(self, estado):
        self.conteos[estado] += 1

    def final(self, resumen, reporte=None, error_reporte=None):
        if self.final_enviado:
            return
        self.final_enviado = True
        # El historial incluye tambien las tareas canceladas por un fallo fatal.
        self.total = resumen.get('seleccionados', self.total)
        for clave, campo in (('leido', 'leidos'), ('descargado', 'descargado'),
                             ('sin_reporte', 'sin_reporte'), ('error', 'errores')):
            self.conteos[clave] = resumen.get(campo, self.conteos[clave])
        estado = resumen['estado']
        cuerpo = self._cabecera() + f'\nEstado: {estado}\n'
        cuerpo += f'Duracion: {time.monotonic() - self.reloj:.1f} segundos\n'
        cuerpo += f"Fin: {resumen.get('fin') or datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        cuerpo += self._avance() + '\n'
        for campo, etiqueta in (('seleccionados', 'Seleccionados'), ('coincidencias', 'Coincidencias'),
                                ('leidos', 'Leidos'), ('descargado', 'Descargados'),
                                ('sin_reporte', 'Sin reporte'), ('errores', 'Errores')):
            if campo in resumen:
                cuerpo += f'{etiqueta}: {resumen[campo]}\n'
        if resumen.get('error'):
            cuerpo += f"Error general: {resumen['error']}\n"
        if error_reporte:
            cuerpo += f'No se pudo generar el Excel de seguimiento: {error_reporte}\n'
        if reporte is not None:
            cuerpo += f'Excel de seguimiento: {reporte}\n'
        self._enviar('Final ' + estado, cuerpo, reporte, adjuntar=True,
                     tipo='error' if estado == 'error' or error_reporte else 'resultados')

    def _cabecera(self):
        return (f'Nombre: {self.config.nombre_proceso}\nDescripcion: {self.config.nombre_proceso_texto}\n'
                f'Proceso: {self.proceso}\nEjecucion: {self.ejecucion}\n'
                f'Inicio: {self.iniciado.isoformat(timespec="seconds")}\n'
                f'Archivo de entrada: {self.archivo.name}\n')

    def _avance(self):
        resueltas = sum(self.conteos.values())
        porcentaje = 100 * resueltas / self.total if self.total else 0
        return (f'Tareas resueltas: {resueltas}/{self.total} ({porcentaje:.1f}%)\n'
                f'Lecturas correctas: {self.conteos["leido"]}\n'
                f'Descargas correctas: {self.conteos["descargado"]}\n'
                f'Meses sin reporte: {self.conteos["sin_reporte"]}\n'
                f'Errores definitivos resueltos: {self.conteos["error"]}')

    def destinatarios(self, tipo):
        """Inicio/resultados a ambas listas; fallas criticas solo a operaciones."""
        if tipo == 'error':
            candidatos = self.config.correo_destinatarios_error
        elif tipo in ('inicio', 'resultados'):
            candidatos = (*self.config.correo_destinatarios, *self.config.correo_destinatarios_error)
        else:
            raise ValueError('Tipo de aviso desconocido: ' + str(tipo))
        unicos, vistos = [], set()
        for direccion in candidatos:
            if direccion.casefold() not in vistos:
                unicos.append(direccion)
                vistos.add(direccion.casefold())
        return unicos

    def _enviar(self, evento, cuerpo, reporte=None, adjuntar=False, tipo='inicio'):
        if not self.activo:
            return
        try:
            destinatarios = self.destinatarios(tipo)
            if not destinatarios:
                self.logger.warning('Correo %s omitido: lista de destinatarios vacia para %s', evento, tipo)
                return
            mensaje = EmailMessage()
            mensaje['Subject'] = f'[ACME][{self.proceso}][{evento}] {self.ejecucion}'
            mensaje['From'] = self.config.correo_usuario
            mensaje['To'] = ', '.join(destinatarios)
            contenido = None
            if adjuntar:
                try:
                    if reporte is None:
                        raise FileNotFoundError('no se genero el Excel')
                    ruta = Path(reporte)
                    with ruta.open('rb') as archivo:
                        contenido = archivo.read(MAX_ADJUNTO + 1)
                    if len(contenido) > MAX_ADJUNTO:
                        raise ValueError('el Excel supera 15 MiB')
                except Exception as error:
                    contenido = None
                    motivo = self.config.ocultar(error)
                    cuerpo += f'\nExcel adjunto omitido: {motivo}\n'
                    self.logger.warning('Correo %s: adjunto omitido: %s', evento, motivo)
            mensaje.set_content(self.config.ocultar(cuerpo))
            if contenido is not None:
                mensaje.add_attachment(contenido, maintype='application',
                                       subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                       filename=Path(reporte).name)
            self.enviar(self.config, mensaje)
            self.logger.info('Correo %s: aceptado por SMTP', evento)
        except Exception as error:
            self.logger.warning('Correo %s: fallo de envio (sin reintento): %s', evento, self.config.ocultar(error))
