"""Pipeline batch con estado inmediato, snapshots verificables y reanudacion manual."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil

from automatizacion.excel import guardar_csv, guardar_excel
from automatizacion.operacion import preparar_carpetas, respaldar, rutas_ejecucion
from integraciones.entradas import leer_inventario, leer_confirmaciones
from integraciones.inventario import Registro, leer_fecha
from integraciones.proceso import evaluar


def huella(ruta):
    digest = hashlib.sha256()
    with Path(ruta).open('rb') as archivo:
        for bloque in iter(lambda: archivo.read(65536), b''):
            digest.update(bloque)
    return digest.hexdigest()


def crear_contexto(config, parametros, entrada, revisiones):
    return {'entrada': str(entrada), 'huella': huella(entrada),
            'revisiones': str(revisiones) if revisiones else None,
            'huella_revisiones': huella(revisiones) if revisiones else None,
            'fecha_proceso': parametros.fecha_proceso.isoformat(), 'dias_alerta': parametros.dias_alerta,
            'fecha_ejecucion': datetime.now().date().isoformat(),
            'ruta_base': str(config.ruta_base), 'ruta_historico': str(config.ruta_historico),
            'carga_completa': False}


def preparar_entrada(base, identificador, contexto, rutas):
    entrada = rutas['input'] / ('inventario' + Path(contexto['entrada']).suffix.lower())
    revision = rutas['input'] / ('revisiones' + Path(contexto['revisiones']).suffix.lower()) if contexto['revisiones'] else None
    for origen, destino, hash_esperado in (
        (contexto['entrada'], entrada, contexto['huella']),
        (contexto['revisiones'], revision, contexto['huella_revisiones']),
    ):
        if origen is None:
            continue
        if not destino.exists():
            # Las copias incompletas no se consideran entradas confirmadas.
            temporal = destino.with_suffix(destino.suffix + '.part')
            shutil.copyfile(origen, temporal)
            if huella(temporal) != hash_esperado:
                temporal.unlink(missing_ok=True)
                raise ValueError('La entrada cambio desde que se creo la ejecucion')
            temporal.replace(destino)
        if huella(destino) != hash_esperado:
            raise ValueError('La copia de entrada no coincide con la ejecucion; no se reanuda')
    if not contexto['carga_completa']:
        registros, errores = leer_inventario(entrada)
        confirmaciones = leer_confirmaciones(revision, {(r.codigo, r.lote) for r in registros})
        elementos = []
        for registro in registros:
            datos = asdict(registro)
            datos['cantidad'] = str(registro.cantidad)
            datos['fecha'] = registro.fecha.isoformat() if registro.fecha else None
            fecha_confirmada = confirmaciones.get((registro.codigo, registro.lote))
            datos['confirmada'] = fecha_confirmada.isoformat() if fecha_confirmada else None
            elementos.append((registro.fila, datos))
        elementos += [(e['fila'], {'error_entrada': e['error']}) for e in errores]
        base.encolar(identificador, elementos)
        contexto['carga_completa'] = True
        base.actualizar_contexto(identificador, contexto)


def procesar_pendientes(base, identificador, contexto, logger):
    for detalle in base.detalles(identificador):
        if detalle['estado'] != 'PENDIENTE':
            continue
        datos = detalle['datos']
        if datos.get('error_entrada'):
            logger.error('Fila %s: %s', detalle['fila'], datos['error_entrada'])
            base.resolver(identificador, detalle['fila'], 'ERROR', datos, datos['error_entrada'])
            continue
        try:
            registro = Registro(datos['codigo'], datos['producto'], datos['lote'], Decimal(datos['cantidad']),
                                leer_fecha(datos['fecha']), datos['fila'])
            confirmaciones = {(registro.codigo, registro.lote): leer_fecha(datos['confirmada'])} if datos['confirmada'] else {}
            resultado = evaluar(registro, confirmaciones, leer_fecha(contexto['fecha_proceso']), contexto['dias_alerta'])
            estado = 'OMITIDO' if resultado['estado'] == 'pendiente_revision' else 'PROCESADO'
            base.resolver(identificador, registro.fila, estado, {'original': datos, 'resultado': resultado})
            logger.info('Fila %s: %s; resultado: %s', registro.fila, estado, resultado['estado'])
        except (ValueError, KeyError, ArithmeticError) as error:
            logger.exception('Dato invalido en fila %s', detalle['fila'])
            base.resolver(identificador, detalle['fila'], 'ERROR', datos, str(error))


def generar_reportes(base, identificador, contexto, carpeta):
    detalles = base.detalles(identificador)
    if any(d['estado'] == 'PENDIENTE' for d in detalles):
        raise RuntimeError('No se pueden finalizar reportes con items pendientes')
    filas = [d['datos']['resultado'] for d in detalles if 'resultado' in d['datos']]
    errores = [{'fila': d['fila'], 'error': d['error']} for d in detalles if d['estado'] == 'ERROR']
    conteos = dict(Counter(f['estado'] for f in filas))
    resumen = {'id_ejecucion': identificador, 'fecha_proceso': contexto['fecha_proceso'],
               'dias_alerta': contexto['dias_alerta'], 'errores_item': len(errores), **conteos}
    columnas = ('codigo', 'producto', 'lote', 'cantidad', 'fecha_vencimiento', 'estado', 'dias_restantes', 'origen_fecha')
    revision_cols = ('codigo', 'producto', 'lote', 'fecha_confirmada', 'motivo')
    pendientes = [f for f in filas if f['estado'] == 'pendiente_revision']
    alertas = [f for f in filas if f['estado'] in ('vencido', 'vence_hoy', 'por_vencer')]
    guardar_excel(carpeta / f'control_vencimientos_{identificador}.xlsx', [
        ('inventario', columnas, filas), ('alertas', columnas, alertas),
        ('revision', revision_cols, pendientes), ('errores', ('fila', 'error'), errores),
        ('resumen', ('campo', 'valor'), [{'campo': k, 'valor': v} for k, v in resumen.items()]),
    ])
    for nombre, cabecera, datos in (('alertas', columnas, alertas), ('revisiones', revision_cols, pendientes), ('errores', ('fila','error'), errores)):
        guardar_csv(carpeta / f'{nombre}_{identificador}.csv', cabecera, datos)
    return resumen


def ejecutar_pipeline(config, parametros, base, logger, entrada, revisiones=None):
    identificador = config.id_ejecucion
    if identificador:
        ejecucion = base.obtener(identificador)
        contexto = ejecucion['contexto']
        if contexto['ruta_base'] != str(config.ruta_base) or contexto['ruta_historico'] != str(config.ruta_historico):
            raise ValueError('Para reanudar se deben conservar RUTA_BASE y RUTA_HISTORICO')
        if (contexto['fecha_proceso'], contexto['dias_alerta']) != (parametros.fecha_proceso.isoformat(), parametros.dias_alerta):
            raise ValueError('Los parametros de Monitor cambiaron; reanudar exige los mismos parametros')
        logger.extra['id_ejecucion'] = identificador
        if ejecucion['estado'] == 'FINALIZADO':
            logger.info('Ejecucion ya FINALIZADO; no se repiten operaciones')
            return identificador
    else:
        contexto = crear_contexto(config, parametros, entrada, revisiones)
        identificador = base.crear(contexto)
        logger.extra['id_ejecucion'] = identificador
    rutas = rutas_ejecucion(config, identificador, contexto['fecha_ejecucion'])
    preparar_carpetas(rutas)
    base.estado(identificador, 'EN_EJECUCION')
    logger.extra['modulo'] = 'PROCESAMIENTO'
    logger.info('Inicio Modulo 1: Procesamiento')
    preparar_entrada(base, identificador, contexto, rutas)
    procesar_pendientes(base, identificador, contexto, logger)
    logger.info('Fin Modulo 1: Procesamiento')
    logger.extra['modulo'] = 'REPORTES'
    resumen = generar_reportes(base, identificador, contexto, rutas['reportes'])
    guardar_csv(rutas['procesados'] / f'resumen_{identificador}.csv', ('campo', 'valor'),
                [{'campo': k, 'valor': v} for k, v in resumen.items()])
    logger.extra['modulo'] = 'RESPALDO'
    respaldo = respaldar(config, identificador, contexto['fecha_ejecucion'], rutas)
    logger.info('Respaldo confirmado: %s', respaldo)
    base.estado(identificador, 'FINALIZADO')
    logger.extra['modulo'] = 'FIN'
    logger.info('Ejecucion FINALIZADO: %s', resumen)
    return identificador
