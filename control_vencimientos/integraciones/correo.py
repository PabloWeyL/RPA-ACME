"""Correo inmediato de error critico; nunca reemplaza la excepcion original."""
from datetime import datetime
from email.message import EmailMessage
import platform
import re
import smtplib
import ssl


def notificar_error(config, secretos, logger, identificador, modulo, error):
    if not config.modulo_correo:
        logger.warning('Correo desactivado por configuracion; error original conservado')
        return
    try:
        mensaje = EmailMessage()
        mensaje['Subject'] = f'[ERROR CRITICO] {config.nombre_proceso} - Ejecucion {identificador} - {modulo}'
        mensaje['From'] = secretos['SECRET_SMTP_USER']
        destinatarios = list(dict.fromkeys(x for x in re.split(r'[,;\s]+', config.obligatorio('LISTA_DISTRIBUCION_ERROR')) if x))
        mensaje['To'] = ', '.join(destinatarios)
        mensaje.set_content(f'Proceso: {config.nombre_proceso}\nID: {identificador}\nModulo: {modulo}\n'
                            f'Fecha: {datetime.now().astimezone().isoformat()}\nEntorno: {config.entorno}\n'
                            f'Maquina: {platform.node()}\nIntentos realizados: {getattr(error, "intentos_realizados", 1)}\n'
                            f'Error: {logger.ocultar(str(error))}')
        puerto = int(config.obligatorio('SMTP_PORT'))
        contexto = ssl.create_default_context()
        servidor = (smtplib.SMTP_SSL(config.obligatorio('SMTP_SERVER'), puerto, timeout=config.timeout, context=contexto)
                    if puerto == 465 else smtplib.SMTP(config.obligatorio('SMTP_SERVER'), puerto, timeout=config.timeout))
        with servidor:
            if puerto == 587:
                servidor.starttls(context=contexto)
            servidor.login(secretos['SECRET_SMTP_USER'], secretos['SECRET_SMTP_PASSWORD'])
            if servidor.send_message(mensaje):
                raise RuntimeError('SMTP rechazo destinatarios')
    except Exception:
        logger.exception('Fallo el aviso de error; se conserva el error original')
