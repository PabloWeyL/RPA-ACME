"""Reglas puras: fechas, cantidades y estados del inventario."""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


COLUMNAS = ('codigo', 'producto', 'lote', 'cantidad', 'fecha_vencimiento')


@dataclass(frozen=True)
class Registro:
    codigo: str
    producto: str
    lote: str
    cantidad: Decimal
    fecha: date | None
    fila: int


def leer_fecha(valor):
    if valor is None or str(valor).strip() == '':
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    for formato in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(str(valor).strip(), formato).date()
        except ValueError:
            pass
    raise ValueError('Fecha invalida; usar AAAA-MM-DD o DD/MM/AAAA con anio de cuatro digitos')


def leer_cantidad(valor):
    try:
        numero = Decimal(str(valor).strip())
    except (InvalidOperation, ValueError):
        raise ValueError('Cantidad invalida; usar un numero no negativo sin separador de miles') from None
    if not numero.is_finite() or numero < 0:
        raise ValueError('Cantidad debe ser finita y no negativa')
    return numero


def clasificar(fecha, hoy, dias_alerta):
    if dias_alerta < 0:
        raise ValueError('Los dias de alerta no pueden ser negativos')
    if fecha is None:
        return 'pendiente_revision', None
    dias = (fecha - hoy).days
    if dias < 0:
        return 'vencido', dias
    if dias == 0:
        return 'vence_hoy', dias
    if dias <= dias_alerta:
        return 'por_vencer', dias
    return 'vigente', dias
