"""Cierre consistente y reintentos para procesos con historiales propios."""
import time


def con_sesion(navegador, operacion, error_sesion):
    try:
        return operacion()
    except error_sesion:
        navegador.login()
        return operacion()


def reintentar(operacion, intentos, antes=lambda numero: None,
               reintentable=lambda error: True, al_reintentar=lambda numero, error: None,
               pausa=time.sleep):
    if intentos < 1:
        raise ValueError('Debe haber al menos un intento')
    for numero in range(1, intentos + 1):
        antes(numero)
        try:
            return operacion()
        except Exception as error:
            if numero == intentos or not reintentable(error):
                raise
            al_reintentar(numero + 1, error)
            pausa(min(numero, 3))


def finalizar_con_reporte(finalizar, generar, notificador, ocultar, fallo=None, confirmar_reporte=lambda: None):
    resumen = finalizar(fallo)
    try:
        reporte = generar()
        confirmar_reporte()
        resumen.pop('reporte_pendiente', None)
    except Exception as error:
        motivo = 'No se pudo generar el Excel de seguimiento: ' + ocultar(error)
        resumen = finalizar((fallo + '; ' if fallo else '') + motivo)
        notificador.final(resumen, error_reporte=ocultar(error))
        raise
    notificador.final(resumen, reporte)
    return resumen, reporte
