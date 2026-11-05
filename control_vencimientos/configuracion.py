"""Configuracion tecnica Weyland; sin negocio ni inicializacion de base al importar."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import re

from dotenv import dotenv_values

RAIZ_PROYECTO = Path(__file__).resolve().parent
NOMBRE_PROCESO = 'CONTROL_VENCIMIENTOS'


@dataclass(frozen=True)
class Configuracion:
    raiz: Path
    entorno: str
    motor_bd: str
    ruta_base: Path
    ruta_historico: Path
    ruta_logs: Path
    db_sqlite: str
    entrada: Path
    modulo_procesamiento: bool
    modulo_correo: bool
    max_reintentos: int
    espera_reintento: float
    timeout: int
    id_ejecucion: str | None
    valores: dict = field(repr=False)
    prueba_local: bool = False
    nombre_proceso: str = NOMBRE_PROCESO

    def obligatorio(self, nombre):
        valor = str(self.valores.get(nombre) or '').strip()
        if not valor:
            raise ValueError('Falta variable obligatoria: ' + nombre)
        return valor


def cargar_configuracion(prueba_local=False, archivo=None):
    archivo = Path(archivo) if archivo else RAIZ_PROYECTO / '.env'
    if not archivo.is_file():
        raise ValueError('Falta .env; copiar .env.example y completar configuracion tecnica')
    propios = dotenv_values(archivo)
    for clave, valor in propios.items():
        if valor and not clave.startswith('SECRET_') and re.search(r'(PASSWORD|TOKEN|API_KEY|CLIENT_SECRET)$', clave):
            raise ValueError('Secreto prohibido en .env: ' + clave + '; usar una referencia SECRET_*')
    valores = {**propios, **os.environ}
    def obligatorio(nombre):
        valor = str(valores.get(nombre) or '').strip()
        if not valor:
            raise ValueError('Falta variable obligatoria: ' + nombre)
        return valor
    def ruta(nombre):
        valor = Path(obligatorio(nombre))
        return valor.resolve() if valor.is_absolute() else (archivo.parent / valor).resolve()
    def booleano(nombre):
        valor = obligatorio(nombre).lower()
        if valor not in ('true', 'false'):
            raise ValueError(nombre + ' debe ser true o false')
        return valor == 'true'
    entorno = obligatorio('ENTORNO').upper()
    motor = obligatorio('MOTOR_BD').lower()
    if entorno not in ('DESARROLLO', 'PRODUCCION') or motor not in ('sqlite', 'azure_sql'):
        raise ValueError('ENTORNO debe ser DESARROLLO/PRODUCCION; MOTOR_BD sqlite/azure_sql')
    if obligatorio('NOMBRE_PROCESO') != NOMBRE_PROCESO:
        raise ValueError('NOMBRE_PROCESO debe ser ' + NOMBRE_PROCESO)
    base, historico, logs = ruta('RUTA_BASE'), ruta('RUTA_HISTORICO'), ruta('RUTA_LOGS')
    if base == historico or historico.is_relative_to(base) or base.is_relative_to(historico):
        raise ValueError('RUTA_BASE y RUTA_HISTORICO deben ser ubicaciones independientes')
    if entorno == 'PRODUCCION':
        if prueba_local or motor != 'azure_sql':
            raise ValueError('Produccion exige Azure SQL y prohibe --prueba-local')
        if any(p.is_relative_to(archivo.parent.resolve()) for p in (base, historico)):
            raise ValueError('En produccion RUTA_BASE y RUTA_HISTORICO deben estar fuera del repositorio')
    intentos = int(obligatorio('MAX_REINTENTOS'))
    espera = float(obligatorio('ESPERA_REINTENTO_SEGUNDOS'))
    timeout = int(obligatorio('TIMEOUT_SEGUNDOS'))
    if not (1 <= intentos <= 10 and 0 <= espera <= 300 and 1 <= timeout <= 300):
        raise ValueError('Reintentos 1..10, espera 0..300 y timeout 1..300 segundos')
    archivo_sqlite = obligatorio('DB_SQLITE') if motor == 'sqlite' else ''
    if archivo_sqlite and (Path(archivo_sqlite).name != archivo_sqlite or '/' in archivo_sqlite or '\\' in archivo_sqlite):
        raise ValueError('DB_SQLITE debe contener solo un nombre de archivo')
    id_ejecucion = str(valores.get('ID_EJECUCION') or '').strip() or None
    if id_ejecucion and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', id_ejecucion):
        raise ValueError('ID_EJECUCION invalido')
    config = Configuracion(archivo.parent.resolve(), entorno, motor, base, historico, logs,
                           archivo_sqlite, ruta('EXCEL_ENTRADA'), booleano('MODULO_PROCESAMIENTO'),
                           booleano('MODULO_CORREO'), intentos, espera, timeout, id_ejecucion, valores, prueba_local)
    if not prueba_local:
        for clave in ('KEYVAULT_NAME', 'MONITOR_DB_SERVER', 'MONITOR_DB_NAME', 'MONITOR_SP_PARAMETROS',
                      'ODBC_DRIVER', 'SECRET_MONITOR_DB_USER', 'SECRET_MONITOR_DB_PASSWORD'):
            config.obligatorio(clave)
    if motor == 'azure_sql':
        for clave in ('DB_SERVER', 'DB_NAME', 'ODBC_DRIVER', 'SECRET_DB_USER', 'SECRET_DB_PASSWORD'):
            config.obligatorio(clave)
    if config.modulo_correo:
        for clave in ('KEYVAULT_NAME', 'SECRET_SMTP_USER', 'SECRET_SMTP_PASSWORD', 'SMTP_SERVER', 'SMTP_PORT', 'LISTA_DISTRIBUCION_ERROR'):
            config.obligatorio(clave)
        if int(config.obligatorio('SMTP_PORT')) not in (465, 587):
            raise ValueError('SMTP_PORT debe ser 465 o 587')
    if entorno == 'PRODUCCION' and not config.modulo_correo:
        raise ValueError('Produccion exige MODULO_CORREO=true para avisos criticos')
    if prueba_local and (motor != 'sqlite' or config.modulo_correo):
        raise ValueError('--prueba-local exige SQLite y correo desactivado')
    return config
