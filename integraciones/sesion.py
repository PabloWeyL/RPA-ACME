"""Recuperacion de la sesion ACME compartida por ambos flujos."""
from automatizacion.ejecucion import con_sesion as recuperar_sesion
from integraciones.navegador import SesionCaducada


def con_sesion(navegador, operacion):
    return recuperar_sesion(navegador, operacion, SesionCaducada)
