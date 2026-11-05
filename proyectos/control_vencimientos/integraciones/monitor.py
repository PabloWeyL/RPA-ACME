"""Contrato propuesto de Monitor: EXEC SP @nombre_proceso=? -> nombre, valor."""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re

from integraciones.inventario import leer_fecha
from integraciones.nube import conectar_sql


@dataclass(frozen=True)
class Parametros:
    fecha_proceso: date
    dias_alerta: int


def validar_parametros(valores):
    if not isinstance(valores, dict) or not {'FECHA_PROCESO', 'DIAS_ALERTA'} <= valores.keys():
        raise ValueError('Monitor debe entregar FECHA_PROCESO y DIAS_ALERTA')
    fecha = leer_fecha(valores.get('FECHA_PROCESO'))
    dias = str(valores.get('DIAS_ALERTA', '')).strip()
    if fecha is None or not re.fullmatch(r'\d+', dias):
        raise ValueError('Parametros Monitor invalidos: FECHA_PROCESO y DIAS_ALERTA')
    return Parametros(fecha, int(dias))


def obtener_parametros(config, secretos, archivo_prueba=None):
    if config.prueba_local:
        if archivo_prueba is None:
            raise ValueError('--prueba-local requiere --parametros-prueba')
        with Path(archivo_prueba).open(encoding='utf-8') as archivo:
            return validar_parametros(json.load(archivo))
    procedimiento = config.obligatorio('MONITOR_SP_PARAMETROS')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?', procedimiento):
        raise ValueError('MONITOR_SP_PARAMETROS debe ser un nombre SQL, no una consulta libre')
    with conectar_sql(config, secretos, monitor=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute('EXEC ' + procedimiento + ' @nombre_proceso=?', config.nombre_proceso)
        nombres = [c[0].lower() for c in cursor.description or ()]
        if nombres != ['nombre', 'valor']:
            raise ValueError('Contrato Monitor pendiente de configurar: debe devolver nombre, valor')
        valores = {}
        for nombre, valor in cursor.fetchall():
            if nombre in valores:
                raise ValueError('Monitor devolvio un parametro repetido: ' + str(nombre))
            valores[nombre] = valor
        return validar_parametros(valores)
