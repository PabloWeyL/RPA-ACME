"""HTTP autenticado reutilizable; no ejecuta el RPA ni persiste secretos."""
from dataclasses import dataclass, field
import math
import threading
import time
from urllib.parse import urlsplit

try:
    import requests
except ModuleNotFoundError:
    requests = None


class ErrorAPI(RuntimeError):
    """Peticion rechazada o contrato de respuesta incorrecto."""


class ErrorTemporalAPI(ErrorAPI):
    """Fallo de transporte o disponibilidad; no reejecutar el RPA."""


class ErrorAutenticacionAPI(ErrorAPI):
    """Es necesario revisar el acceso antes de continuar."""


@dataclass(frozen=True)
class ConfiguracionAPI:
    url: str
    correo: str = field(repr=False)
    password: str = field(repr=False)
    login_url: str

    def __post_init__(self):
        for nombre, valor in (('BACKEND_API_URL', self.url),
                              ('BACKEND_LOGIN_URL', self.login_url)):
            try:
                partes = urlsplit(valor)
                partes.port
            except ValueError:
                raise ValueError('URL o puerto invalido: ' + nombre) from None
            if (partes.scheme != 'https' or not partes.hostname or partes.username
                    or partes.password or partes.query or partes.fragment
                    or any(c.isspace() for c in valor) or '\\' in valor):
                raise ValueError(nombre + ' debe ser HTTPS sin credenciales, query ni fragmento')
        if (not self.correo.strip() or '@' not in self.correo
                or any(c.isspace() for c in self.correo) or not self.password):
            raise ValueError('Faltan BACKEND_EMAIL o BACKEND_PASSWORD, o el correo es invalido')


class ClienteAPI:
    """Login con correo/password en URL configurada y refresh del contrato docs/."""

    def __init__(self, config, session=None, reloj=time.monotonic):
        if session is None:
            if requests is None:
                raise RuntimeError('Instale requirements.txt para usar el cliente API')
            session = requests.Session()
            # No heredar credenciales de .netrc ni mezclar esta sesion con el navegador.
            session.trust_env = False
        self.config = config
        self._session = session
        self._reloj = reloj
        self._lock = threading.RLock()
        self._access_token = None
        self._refresh_token = None
        self._vence = 0
        self._margen = 0
        self._bloqueado = False
        self._cerrado = False

    def _limpiar_tokens(self):
        self._access_token = self._refresh_token = None
        self._vence = self._margen = 0

    @property
    def autenticacion_bloqueada(self):
        return self._bloqueado

    def cerrar(self):
        with self._lock:
            self._limpiar_tokens()
            self._cerrado = True
            self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()

    def _enviar(self, metodo, ruta, *, url=None, **kwargs):
        try:
            return self._session.request(
                metodo, url if url is not None else self.config.url.rstrip('/') + ruta,
                timeout=(10, 60), allow_redirects=False, **kwargs)
        except Exception as error:
            if requests is not None and isinstance(error, requests.RequestException):
                raise ErrorTemporalAPI('Fallo de comunicacion con la API') from None
            raise

    @staticmethod
    def _token_valido(valor):
        return (isinstance(valor, str) and bool(valor)
                and all(32 < ord(c) < 127 for c in valor))

    def _autenticar(self, renovar=False):
        inicio = self._reloj()
        ruta = '/auth/refresh' if renovar else '/auth/login'
        datos = ({'refresh_token': self._refresh_token} if renovar else {
            'correo': self.config.correo, 'password': self.config.password})
        try:
            with self._enviar('POST', ruta, json=datos,
                             url=None if renovar else self.config.login_url) as respuesta:
                if respuesta.status_code != 200:
                    raise ErrorAutenticacionAPI('La API rechazo la autenticacion o renovacion')
                try:
                    tokens = respuesta.json()
                except ValueError:
                    raise ErrorAutenticacionAPI('Respuesta de autenticacion no valida') from None
                if not isinstance(tokens, dict):
                    raise ErrorAutenticacionAPI('Respuesta de autenticacion no valida')
                ttl = tokens.get('expires_in')
                if (not self._token_valido(tokens.get('access_token'))
                        or not self._token_valido(tokens.get('refresh_token'))
                        or not isinstance(tokens.get('token_type'), str)
                        or tokens['token_type'].lower() != 'bearer'
                        or isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                        or not math.isfinite(ttl) or ttl <= 0
                        or inicio + ttl <= self._reloj()):
                    raise ErrorAutenticacionAPI('Respuesta de autenticacion incompleta o invalida')
                self._access_token = tokens['access_token']
                self._refresh_token = tokens['refresh_token']
                self._vence = inicio + ttl
                self._margen = min(30, ttl * .1)
        except (ErrorTemporalAPI, ErrorAutenticacionAPI):
            # Un timeout puede ocultar una rotacion ya realizada por el servidor.
            # No reenviar a ciegas el refresh anterior ni iniciar un bucle de login.
            self._limpiar_tokens()
            self._bloqueado = True
            raise

    def solicitar(self, metodo, ruta, *, datos=None, archivos=None, idempotencia=None, stream=False):
        """Devuelve Response; reconstruye JSON o multipart al repetir tras un 401."""
        if datos is not None and archivos is not None:
            raise ValueError('No combinar cuerpo JSON con multipart')
        posiciones = []
        if archivos is not None:
            for campo in archivos.values():
                contenido = campo[1] if isinstance(campo, (tuple, list)) else campo
                if hasattr(contenido, 'read'):
                    try:
                        posicion = contenido.tell()
                        contenido.seek(posicion)
                    except (OSError, AttributeError):
                        raise ValueError('El archivo debe permitir rebobinado para renovar el token') from None
                    posiciones.append((contenido, posicion))
        partes = urlsplit(ruta)
        if (not ruta.startswith('/') or ruta.startswith('//') or partes.scheme
                or partes.netloc or partes.query or partes.fragment or '\\' in ruta
                or any(c.isspace() for c in ruta)
                or any(p in ('.', '..') for p in partes.path.split('/'))):
            raise ValueError('Se requiere una ruta relativa de la API sin query ni fragmento')
        metodo = metodo.upper()
        if metodo not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            raise ValueError('Metodo HTTP no admitido')
        if idempotencia is not None and not self._token_valido(idempotencia):
            raise ValueError('Clave de idempotencia invalida')
        with self._lock:
            if self._cerrado or self._bloqueado:
                raise ErrorAutenticacionAPI('Cliente cerrado o autenticacion bloqueada; revisar el acceso')
            if self._access_token is None:
                self._autenticar()
            elif self._reloj() >= self._vence - self._margen:
                self._autenticar(renovar=True)
            for intento in range(2):
                headers = {'Authorization': 'Bearer ' + self._access_token}
                if idempotencia is not None:
                    headers['Idempotency-Key'] = idempotencia
                for archivo, posicion in posiciones:
                    archivo.seek(posicion)
                cuerpo = {'files': archivos} if archivos is not None else {'json': datos}
                respuesta = self._enviar(metodo, ruta, headers=headers, stream=stream, **cuerpo)
                codigo = respuesta.status_code
                if 200 <= codigo < 300:
                    return respuesta
                respuesta.close()
                if codigo == 401:
                    if intento == 0:
                        self._autenticar(renovar=True)
                        continue
                    self._limpiar_tokens()
                    self._bloqueado = True
                    raise ErrorAutenticacionAPI('La API sigue rechazando el token renovado')
                if codigo == 403:
                    raise ErrorAPI('Acceso sin permisos suficientes (HTTP 403)')
                if codigo == 429 or 500 <= codigo <= 599:
                    raise ErrorTemporalAPI(f'API temporalmente no disponible (HTTP {codigo})')
                raise ErrorAPI(f'Peticion rechazada por la API (HTTP {codigo})')

    def verificar(self):
        with self.solicitar('GET', '/auth/me') as respuesta:
            if respuesta.status_code != 200:
                raise ErrorAPI('La comprobacion de acceso requiere HTTP 200')
