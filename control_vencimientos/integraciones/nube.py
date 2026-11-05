"""Key Vault y conexiones SQL independientes; ningun secreto se persiste."""
from contextlib import contextmanager
import re


def obtener_secretos(config):
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
    from azure.keyvault.secrets import SecretClient
    nombre = config.obligatorio('KEYVAULT_NAME')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]', nombre):
        raise ValueError('KEYVAULT_NAME invalido')
    claves = ['SECRET_MONITOR_DB_USER', 'SECRET_MONITOR_DB_PASSWORD']
    if config.motor_bd == 'azure_sql':
        claves += ['SECRET_DB_USER', 'SECRET_DB_PASSWORD']
    if config.modulo_correo:
        claves += ['SECRET_SMTP_USER', 'SECRET_SMTP_PASSWORD']
    credencial = (ManagedIdentityCredential(connection_timeout=config.timeout, read_timeout=config.timeout, retry_total=0)
                  if config.entorno == 'PRODUCCION' else DefaultAzureCredential(process_timeout=config.timeout))
    try:
        with SecretClient('https://' + nombre + '.vault.azure.net/', credencial,
                          connection_timeout=config.timeout, read_timeout=config.timeout, retry_total=0) as cliente:
            secretos = {}
            for clave in claves:
                valor = cliente.get_secret(config.obligatorio(clave)).value
                if not valor:
                    raise ValueError('Secreto requerido sin valor: ' + clave)
                secretos[clave] = valor
            return secretos
    finally:
        credencial.close()


def _odbc(valor):
    return '{' + str(valor).replace('}', '}}') + '}'


@contextmanager
def conectar_sql(config, secretos, monitor=False):
    import pyodbc
    prefijo = 'MONITOR_DB_' if monitor else 'DB_'
    servidor = config.obligatorio(prefijo + 'SERVER')
    base = config.obligatorio(prefijo + 'NAME')
    referencia = 'SECRET_MONITOR_DB_' if monitor else 'SECRET_DB_'
    cadena = ';'.join(('DRIVER=' + _odbc(config.obligatorio('ODBC_DRIVER')),
                       'SERVER=' + _odbc(servidor), 'DATABASE=' + _odbc(base),
                       'UID=' + _odbc(secretos[referencia + 'USER']),
                       'PWD=' + _odbc(secretos[referencia + 'PASSWORD']),
                       'Encrypt=yes', 'TrustServerCertificate=no'))
    conexion = pyodbc.connect(cadena, timeout=config.timeout)
    conexion.timeout = config.timeout
    try:
        yield conexion
        conexion.commit()
    except BaseException:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def es_transitorio(error):
    # Solo fallos conocidos de red, no autenticacion ni SQL invalido.
    codigo = str(error.args[0]) if error.args else ''
    return isinstance(error, (TimeoutError, ConnectionError)) or codigo[:5] in ('08001', '08003', '08006', '08S01', 'HYT00', 'HYT01')
