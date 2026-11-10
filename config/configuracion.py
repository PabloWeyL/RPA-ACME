import json
import logging
import os
import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Configuracion:
    root: Path = ROOT
    email: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    url: str = "https://acme-test.uipath.com"
    headless: bool = True
    timeout: float = 25
    max_intentos: int = 3
    filtros: Path = Path("input/filtros.xlsx")
    db: Path = Path("temporales/acme.db")
    reportes_input: Path = Path("input/reportes_mensuales.xlsx")
    descargas: Path = Path("descargas/reportes_mensuales")
    correo_activo: bool = False
    correo_usuario: str = field(default="", repr=False)
    correo_password: str = field(default="", repr=False)
    correo_destinatarios: tuple = field(default=(), repr=False)
    correo_cada: int = 10
    base: Path | None = None
    logs: Path = Path("logs")
    capturas: Path = Path("capturas")
    excel_salida: Path = Path("reportes/resultados.xlsx")
    modulo_scraping: bool = True
    correo_inicial: bool = True
    nombre_proceso: str = "RPA_ACME"
    nombre_proceso_texto: str = "Robot ACME: Work Items y reportes mensuales"
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 465
    correo_destinatarios_error: tuple = field(default=(), repr=False)
    inputs: Path = Path("input")
    retencion_logs_meses: int = 12
    backend_api_url: str = ''
    backend_api_token: str = field(default='', repr=False)
    backend_auth_mode: str = 'login'
    backend_login_url: str = ''
    backend_email: str = field(default='', repr=False)
    backend_password: str = field(default='', repr=False)
    robot_id: str = ''
    intervalo_consulta_segundos: float = 10

    def ruta(self, valor):
        ruta = Path(valor)
        return ruta if ruta.is_absolute() else (self.base or self.root) / ruta

    @property
    def carpeta_reportes(self):
        ruta = self.ruta(self.excel_salida)
        return ruta.parent if ruta.suffix.lower() == ".xlsx" else ruta

    def selectores(self):
        return json.loads((self.root / "config/selectores.json").read_text(encoding="utf-8"))

    def ocultar(self, texto):
        texto = str(texto)
        for secreto in (self.password, self.email, self.correo_password,
                        self.correo_password.replace(" ", ""), self.correo_usuario,
                        self.backend_api_token, self.backend_email, self.backend_password):
            if secreto:
                texto = texto.replace(secreto, "[OCULTO]")
        return texto


def cargar_configuracion():
    load_dotenv(ROOT / ".env")

    def valor(estandar, alias=None, defecto=""):
        dato = os.getenv(estandar)
        if dato is not None:
            return dato
        return os.getenv(alias, defecto) if alias else defecto

    def booleano(estandar, alias=None, defecto="false"):
        return valor(estandar, alias, defecto).strip().lower() in ("true", "1", "si")

    def lista(estandar, alias=None):
        return tuple(x for x in re.split(r'[,;\s]+', valor(estandar, alias)) if x)

    try:
        correo_cada = int(valor("CORREO_CADA", "ACME_CORREO_CADA", "10"))
    except ValueError:
        correo_cada = 0 
    try:
        smtp_port = int(valor("SMTP_PORT", defecto="465"))
    except ValueError:
        smtp_port = 0
    base = valor("RUTA_BASE").strip()
    config = Configuracion(
        email=os.getenv("ACME_EMAIL", ""), password=os.getenv("ACME_PASSWORD", ""),
        url=os.getenv("ACME_URL", "https://acme-test.uipath.com").rstrip("/"),
        headless=os.getenv("ACME_HEADLESS", "true").lower() in ("true", "1", "si"),
        timeout=float(os.getenv("ACME_TIMEOUT", "25")),
        max_intentos=int(valor("MAX_REINTENTOS", "ACME_MAX_INTENTOS", "3")),
        filtros=Path(valor("EXCEL_ENTRADA", "ACME_FILTROS", "input/filtros.xlsx")),
        db=Path(valor("DB_PATH", "ACME_DB", "temporales/acme.db")),
        reportes_input=Path(valor("EXCEL_REPORTES_MENSUALES", "ACME_REPORTES_INPUT", "input/reportes_mensuales.xlsx")),
        descargas=Path(valor("CARPETA_DESCARGAS", "ACME_DESCARGAS", "descargas/reportes_mensuales")),
        correo_activo=booleano("MODULO_CORREO", "ACME_CORREO_ACTIVO"),
        correo_usuario=valor("EMAIL_REMITENTE", "ACME_CORREO_USUARIO").strip(),
        correo_password=valor("EMAIL_PASSWORD", "ACME_CORREO_PASSWORD"),
        correo_destinatarios=lista("LISTA_DISTRIBUCION_CORREO", "ACME_CORREO_DESTINATARIOS"),
        correo_cada=correo_cada,
        base=(ROOT / base).resolve() if base else None,
        logs=Path(valor("RUTA_HISTORICO", defecto="logs")),
        capturas=Path(valor("CARPETA_CAPTURAS", defecto="capturas")),
        excel_salida=Path(valor("EXCEL_SALIDA", defecto="reportes/resultados.xlsx")),
        modulo_scraping=booleano("MODULO_SCRAPING", defecto="true"),
        correo_inicial=booleano("MODULO_CORREO_INICIAL", defecto="true"),
        nombre_proceso=valor("NOMBRE_PROCESO", defecto="RPA_ACME"),
        nombre_proceso_texto=valor("NOMBRE_PROCESO_TEXTO", defecto="Robot ACME: Work Items y reportes mensuales"),
        smtp_server=valor("SMTP_SERVER", defecto="smtp.gmail.com").strip(),
        smtp_port=smtp_port,
        correo_destinatarios_error=lista("LISTA_DISTRIBUCION_ERROR"),
        inputs=Path(valor("CARPETA_INPUTS", defecto="input")),
        retencion_logs_meses=int(valor("RETENCION_LOGS_MESES", defecto="12")),
        backend_api_url=valor('BACKEND_API_URL').strip().rstrip('/'),
        backend_api_token=valor('BACKEND_API_TOKEN').strip(),
        backend_auth_mode=valor('BACKEND_AUTH_MODE', defecto='login').strip().lower(),
        backend_login_url=valor('BACKEND_LOGIN_URL').strip(),
        backend_email=valor('BACKEND_EMAIL').strip(),
        backend_password=valor('BACKEND_PASSWORD'),
        robot_id=valor('ROBOT_ID').strip(),
        intervalo_consulta_segundos=float(valor('INTERVALO_CONSULTA_SEGUNDOS', defecto='10')),
    )
    if config.timeout <= 0 or config.max_intentos < 1:
        raise ValueError("ACME_TIMEOUT y ACME_MAX_INTENTOS deben ser positivos")
    if config.retencion_logs_meses < 1:
        raise ValueError("RETENCION_LOGS_MESES debe ser un entero positivo")
    return config


def configurar_logger(config, ejecucion, modulo="WORK_ITEMS"):
    class OcultarSecretos(logging.Filter):
        def filter(self, record):
            record.msg = config.ocultar(record.getMessage())
            record.args = ()
            if not hasattr(record, 'id_ejecucion'):
                record.id_ejecucion = ejecucion or 'SIN_ID'
            if not hasattr(record, 'modulo'):
                record.modulo = modulo
            return True

    carpeta = config.ruta(config.logs)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = re.sub(r'[^\w-]+', '_', config.nombre_proceso.strip().lower()) or 'rpa_acme'
    ruta_log = carpeta / (nombre + '.log')
    logger = logging.getLogger(config.nombre_proceso)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    renovar = False
    if ruta_log.exists() and ruta_log.stat().st_size:
        with ruta_log.open(encoding='utf-8') as archivo:
            primera_fecha = archivo.readline(256)[:19]
        try:
            inicio = datetime.strptime(primera_fecha, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            raise ValueError('No se pudo determinar el inicio del periodo del log: ' + ruta_log.name)
        indice = inicio.year * 12 + inicio.month - 1 + config.retencion_logs_meses
        anio, mes = divmod(indice, 12)
        mes += 1
        vencimiento = inicio.replace(year=anio, month=mes,
                                    day=min(inicio.day, calendar.monthrange(anio, mes)[1]))
        renovar = datetime.now() >= vencimiento

    formato = logging.Formatter(
        '%(asctime)s [%(levelname)s] [%(name)s] [ID:%(id_ejecucion)s] [%(modulo)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    for handler in (logging.StreamHandler(), logging.FileHandler(ruta_log, mode='w' if renovar else 'a', encoding="utf-8")):
        handler.setFormatter(formato)
        handler.addFilter(OcultarSecretos())
        logger.addHandler(handler)
    logger.info('Log centralizado configurado%s.', '; periodo anterior reemplazado' if renovar else '')
    return logger
