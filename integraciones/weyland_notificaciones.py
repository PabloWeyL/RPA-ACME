"""Notificaciones de cola_actual; persistencia por destinatario sin repetir negocio."""
from email.message import EmailMessage
from email.utils import formatdate, parseaddr
import hashlib
import json
import smtplib
import ssl

from integraciones.cola_correo import ColaCorreo, ErrorEntrega
from integraciones.trazabilidad import contexto
from integraciones.weyland.archivos import confinar, sha256


class NotificadorWeb:
    def __init__(self, ajustes):
        self.ajustes = ajustes

    def __call__(self, evento, estado, carpeta):
        if evento not in ('inicio', 'resultados', 'fallas'):
            raise ValueError('Evento de correo no admitido')
        config = self.ajustes()
        if not config['habilitado'] or evento == 'inicio' and not config.get('inicio', True):
            return False
        destinatarios = set(config['errores'])
        if evento != 'fallas':
            destinatarios.update(config['resultados'])
        destinatarios = sorted({c.strip().casefold() for c in destinatarios if c.strip()})
        remitente = config['remitente']
        if (not destinatarios or len(destinatarios) > 200 or any(
                '\r' in c or '\n' in c or '@' not in c or parseaddr(c)[1] != c
                for c in [remitente, *destinatarios])):
            raise ValueError('Remitente o listas de distribucion invalidos')
        datos = {'solicitud_id': estado['ejecucion_id'], 'ejecucion_id_web': estado['ejecucion_id'],
                 'activacion_id': estado['id_externo']}
        mensaje = EmailMessage()
        mensaje['From'], mensaje['To'] = remitente, ', '.join(destinatarios)
        mensaje['Subject'] = f'{config["nombre"]} - {evento} - {estado["ejecucion_id"]}'
        mensaje['X-Weyland-Solicitud'] = str(estado['ejecucion_id'])
        mensaje['X-Weyland-Activacion'] = estado['id_externo']
        mensaje.set_content(f'Proceso: {config["nombre"]}\nEvento: {evento}\n'
                            f'ID web: {estado["ejecucion_id"]}\nCorrelacion: {estado["id_externo"]}\n')
        huella = None
        if evento == 'resultados':
            entrega = estado['entrega']
            ruta = confinar(carpeta, entrega['ruta'])
            limite = int(config['limite'])
            if limite < 1 or ruta.stat().st_size > limite or sha256(ruta) != entrega['sha256']:
                raise ValueError('Adjunto alterado o superior a MAX_ADJUNTOS_BYTES')
            with ruta.open('rb') as archivo:
                contenido = archivo.read(limite + 1)
            huella = hashlib.sha256(contenido).hexdigest()
            if len(contenido) > limite or huella != entrega['sha256']:
                raise ValueError('Adjunto alterado durante su lectura')
            mensaje.add_attachment(contenido, maintype='application',
                subtype='json' if ruta.suffix == '.json' else 'zip', filename=ruta.name)
        identidad = {'evento': evento, 'datos': datos, 'remitente': remitente,
                     'destinatarios': destinatarios, 'huella': huella, 'nombre': config['nombre']}
        clave = hashlib.sha256(json.dumps(identidad, sort_keys=True).encode()).hexdigest()
        mensaje['Message-ID'] = f'<{clave}@weyland-bot.local>'
        mensaje['Date'] = formatdate(localtime=False)
        with contexto(datos):
            cola = ColaCorreo(carpeta / 'correo')
            cola.registrar(clave, remitente, destinatarios, mensaje.as_bytes(), datos)
            cola.procesar(lambda *args: enviar(config, *args), solo=clave)
            aceptado = cola.entregado(clave)
            if not aceptado:
                print('CORREO_PENDIENTE: mensaje y adjuntos conservados; revisar correos-estado y recuperar sin repetir el proceso.')
            return aceptado


def enviar(config, remitente, destinatarios, mime):
    if remitente.casefold() != config['remitente'].casefold():
        raise ErrorEntrega('REMITENTE_CAMBIO', permanente=True)
    puerto = int(config['puerto'])
    if puerto not in (465, 587) or not config['host'] or not config['usuario'] or not config['password']:
        raise ErrorEntrega('CONFIGURACION_SMTP_INVALIDA', permanente=True)
    durante_datos = False
    try:
        tls = ssl.create_default_context()
        servidor = (smtplib.SMTP_SSL(config['host'], puerto, timeout=20, context=tls)
                    if puerto == 465 else smtplib.SMTP(config['host'], puerto, timeout=20))
        with servidor:
            if puerto == 587:
                servidor.starttls(context=tls)
            servidor.login(config['usuario'], config['password'])
            durante_datos = True
            return servidor.sendmail(remitente, destinatarios, mime)
    except smtplib.SMTPRecipientsRefused as error:
        return error.recipients
    except smtplib.SMTPResponseException as error:
        raise ErrorEntrega(f'SMTP_{error.smtp_code}', permanente=error.smtp_code >= 500) from None
    except Exception:
        raise ErrorEntrega('SMTP_SIN_CONFIRMACION' if durante_datos else 'SMTP_NO_DISPONIBLE',
                           incierto=durante_datos) from None
