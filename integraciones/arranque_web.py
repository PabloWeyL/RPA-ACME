"""Perfil de validacion ACME y entrada web predeterminada sin argumentos."""
import math
from pathlib import Path
from urllib.parse import urlsplit

from config.configuracion import cargar_configuracion
from config.weyland import leer_valores
from integraciones.arranque import (ErrorArranque, ejecutar_automatico, validar_modulos,
    validar_node, validar_chrome, revisar_integridad)
from integraciones.weyland.configuracion import configuracion_desde_valores

RAIZ = Path(__file__).resolve().parents[1]


def validar_tecnico(valores):
    config = cargar_configuracion()
    if not config.modulo_scraping:
        raise ErrorArranque('MODULO_SCRAPING=false: arranque automatico desactivado')
    if not config.email or not config.password:
        raise ErrorArranque('Faltan ACME_EMAIL/ACME_PASSWORD; configurar antes del arranque')
    if not math.isfinite(config.timeout) or config.timeout <= 0:
        raise ErrorArranque('ACME_TIMEOUT debe ser positivo y finito')
    url = urlsplit(config.url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ErrorArranque('ACME_URL debe ser HTTPS y no contener credenciales')
    if config.correo_activo and (not config.correo_usuario or not config.correo_password
            or not config.correo_destinatarios_error or config.smtp_port not in (465, 587)):
        raise ErrorArranque('Correo habilitado: revisar SMTP, credenciales y LISTA_DISTRIBUCION_ERROR')
    if not isinstance(config.selectores(), dict):
        raise ErrorArranque('El archivo de selectores ACME es incompatible')
    validar_modulos(('requests', 'dotenv', 'openpyxl', 'rpa'))
    validar_node()
    validar_chrome()
    if not (RAIZ / 'tests/browser_dom.cjs').is_file():
        raise ErrorArranque('Falta el fixture tests/browser_dom.cjs')
    acceso = configuracion_desde_valores(valores)
    if not acceso.api_key:
        raise ErrorArranque('Falta BACKEND_BOT_API_KEY')
    if config.ruta(config.db).is_file():
        revisar_integridad(config.ruta(config.db))
    return {'logs': config.ruta(config.logs)}


def ejecutar_sin_argumentos():
    valores = leer_valores()
    comando = str(valores.get('ACME_PROCESO_WEB') or 'consultar').strip()
    if comando not in ('consultar', 'descargar-reportes'):
        raise ErrorArranque('ACME_PROCESO_WEB debe ser consultar o descargar-reportes')
    def procesar():
        from integraciones.weyland_worker import ejecutar_desde_config
        return ejecutar_desde_config('acme', RAIZ, comando)
    return ejecutar_automatico(RAIZ, valores, lambda: validar_tecnico(valores), procesar)
