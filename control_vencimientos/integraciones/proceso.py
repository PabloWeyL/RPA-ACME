"""Evaluacion de registros individuales; no abre conexiones ni crea carpetas."""
from integraciones.inventario import clasificar


def evaluar(registro, confirmaciones, hoy, dias_alerta):
    clave = (registro.codigo, registro.lote)
    fecha = confirmaciones.get(clave, registro.fecha)
    origen = 'revision_confirmada' if clave in confirmaciones else 'inventario'
    motivo = '' if fecha else 'Completar fecha_vencimiento o confirmar la fecha en revisiones.csv'
    estado, dias = clasificar(fecha, hoy, dias_alerta)
    return {
        'codigo': registro.codigo, 'producto': registro.producto, 'lote': registro.lote,
        'cantidad': str(registro.cantidad), 'fecha_vencimiento': fecha.isoformat() if fecha else '',
        'estado': estado, 'dias_restantes': dias, 'origen_fecha': origen,
        'fila': registro.fila, 'fecha_confirmada': '', 'motivo': motivo,
    }


